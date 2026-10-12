"""Mirror one sample-project snapshot into doplaydo/gfp-sample-projects as a PR.

Run by sample-project-upload.yml right after the portal upload of the same
pristine snapshot succeeded, so gfp-sample-projects only receives content the
portal also received. Which projects are mirrored, and under which directory
name, is decided by gfp-sample-projects' own ``.github/sample-projects-sources.yml``
(``projects: {<dir>: {repo, path}}``); unmapped projects are skipped.

The mapped directory is replaced wholesale (exact mirror, minus the project's
top-level ``build/``) on a ``sync/<dir>`` branch rebuilt from ``main`` each
time, and a PR is opened or updated. Files the sync deletes are listed in the
PR body and labelled ``needs-backsync-review``: they existed only in
gfp-sample-projects and may need porting back to the PDK repo first.

Needs ``git``, ``gh`` (authenticated via ``GH_TOKEN`` with contents + pull
requests write on the target) and PyYAML.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import yaml

TARGET_REPO = "doplaydo/gfp-sample-projects"
SOURCES_PATH = ".github/sample-projects-sources.yml"
LABEL = "needs-backsync-review"
SOURCE_TRAILER = "Source-Commit"
COMMITTER_NAME = "gfp-sample-sync"
COMMITTER_EMAIL = "noreply@gdsfactory.com"


@dataclasses.dataclass(frozen=True)
class Source:
    """Where one gfp-sample-projects directory is maintained upstream."""

    repo: str
    path: str


def _norm(path: str) -> str:
    return PurePosixPath(path.strip()).as_posix().strip("/")


def parse_sources(text: str) -> dict[str, Source]:
    projects = (yaml.safe_load(text) or {}).get("projects") or {}
    return {d: Source(repo=e["repo"], path=_norm(e["path"])) for d, e in projects.items()}


def find_target_dir(sources: dict[str, Source], repo: str, project_dir: str) -> str | None:
    want = _norm(project_dir)
    hits = sorted(d for d, s in sources.items() if s.repo.lower() == repo.lower() and s.path == want)
    if len(hits) > 1:
        raise ValueError(f"{repo}:{want} is mapped by several directories: {', '.join(hits)}")
    return hits[0] if hits else None


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}


def replace_dir(dest: Path, snapshot_tar: Path) -> list[str]:
    """Replace ``dest`` with the tar's contents minus top-level ``build/``; return deleted files."""
    # Old top-level build/ is removed too, but it's generated, so it's never
    # reported as a deletion needing backsync.
    old = {f for f in (_files(dest) if dest.exists() else set()) if not f.startswith("build/")}
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(snapshot_tar) as tf:
        tf.extractall(dest, filter="data")
    shutil.rmtree(dest / "build", ignore_errors=True)
    return sorted(old - _files(dest))


def branch_name(target_dir: str) -> str:
    return f"sync/{target_dir}"


def pr_title(target_dir: str, source_repo: str) -> str:
    return f"sync {target_dir} from {source_repo}"


def commit_message(target_dir: str, source_repo: str, source_sha: str, pdk_version: str) -> str:
    return f"sync {target_dir} from {source_repo}@{source_sha[:7]} ({pdk_version})\n\n{SOURCE_TRAILER}: {source_sha}\n"


def parse_source_commit(message: str) -> str | None:
    for line in reversed(message.splitlines()):
        if line.startswith(f"{SOURCE_TRAILER}: "):
            return line.split(": ", 1)[1].strip()
    return None


def is_superseded(compare_status: str | None) -> bool:
    """True when the sync branch already carries this run's source commit or a newer one.

    ``compare_status`` is GitHub's status for ``compare/<this run>...<recorded>``,
    or None when the comparison failed (e.g. an unknown sha).
    """
    return compare_status in ("identical", "ahead")


def render_pr_body(
    *,
    target_dir: str,
    source_repo: str,
    project_dir: str,
    source_sha: str,
    pdk_version: str,
    deleted: list[str],
) -> str:
    source_url = f"https://github.com/{source_repo}/tree/{source_sha}/{project_dir}"
    lines = [
        f"`{target_dir}` is generated from [{source_repo}@{source_sha[:7]}:{project_dir}]({source_url}),",
        f"the snapshot that passed `gfp test`, notebooks and DRC against the published PDK {pdk_version}",
        "and was uploaded to the GDSFactory+ portal.",
        "",
        f"Edit it in {source_repo}, not here.",
        "",
    ]
    if deleted:
        lines += [
            "Deleted files (exist only here - backsync them to the source repo before merging if they matter):",
            "",
            *(f"- `{f}`" for f in deleted),
        ]
    else:
        lines.append("Deleted files: none")
    return "\n".join(lines) + "\n"


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def fetch_sources(target_repo: str) -> str | None:
    """Return the target's sources file, or None if it has none.

    A token without access sees 404 for everything, so check the repo itself
    first: otherwise a missing grant would look like "nothing mapped".
    """
    repo = subprocess.run(["gh", "api", f"repos/{target_repo}", "--jq", ".full_name"], capture_output=True, text=True)
    if repo.returncode != 0:
        raise SystemExit(f"GH_TOKEN cannot see {target_repo}: {repo.stderr.strip()}")
    got = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{target_repo}/contents/{SOURCES_PATH}?ref=main",
            "--header",
            "Accept: application/vnd.github.raw+json",
        ],
        capture_output=True,
        text=True,
    )
    if got.returncode == 0:
        return got.stdout
    if "HTTP 404" in got.stderr:
        return None
    raise SystemExit(f"reading {target_repo}:{SOURCES_PATH} failed: {got.stderr.strip()}")


def _recorded_source_commit(target_repo: str, branch: str) -> str | None:
    """Source commit recorded on the target's existing sync branch, if any."""
    got = subprocess.run(
        ["gh", "api", f"repos/{target_repo}/commits/heads/{branch}", "--jq", ".commit.message"],
        capture_output=True,
        text=True,
    )
    return parse_source_commit(got.stdout) if got.returncode == 0 else None


def _compare_status(source_repo: str, base: str, head: str) -> str | None:
    """GitHub compare status of head relative to base in the source repo.

    Uses SOURCE_GH_TOKEN (the PDK repo's github.token): the gsp token may not
    be able to read the PDK repo.
    """
    env = {**os.environ, "GH_TOKEN": os.environ.get("SOURCE_GH_TOKEN") or os.environ.get("GH_TOKEN", "")}
    got = subprocess.run(
        ["gh", "api", f"repos/{source_repo}/compare/{base}...{head}", "--jq", ".status"],
        capture_output=True,
        text=True,
        env=env,
    )
    return got.stdout.strip() if got.returncode == 0 else None


def _open_pr_number(target_repo: str, branch: str) -> str:
    return _run(
        ["gh", "pr", "list", "--repo", target_repo, "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number // empty"]
    )


def _open_or_update_pr(target_repo: str, branch: str, title: str, body: str, needs_review: bool) -> None:
    existing = _open_pr_number(target_repo, branch)
    if existing:
        _run(["gh", "pr", "edit", existing, "--repo", target_repo, "--title", title, "--body", body])
        number = existing
    else:
        _run(["gh", "pr", "create", "--repo", target_repo, "--base", "main", "--head", branch, "--title", title, "--body", body])
        number = _run(["gh", "pr", "view", branch, "--repo", target_repo, "--json", "number", "--jq", ".number"])
    if needs_review:
        _run(["gh", "pr", "edit", number, "--repo", target_repo, "--add-label", LABEL])
    elif existing:
        # gh fails with "'<label>' not found" when removing a label the repo lacks, so only remove a label the PR carries.
        labels = _run(["gh", "pr", "view", number, "--repo", target_repo, "--json", "labels", "--jq", ".labels[].name"]).splitlines()
        if LABEL in labels:
            _run(["gh", "pr", "edit", number, "--repo", target_repo, "--remove-label", LABEL])
    print(f"https://github.com/{target_repo}/pull/{number}")


def _sync(args: argparse.Namespace) -> None:
    target = args.target_repo
    text = fetch_sources(target)
    if text is None:
        print(f"::notice::{target} has no {SOURCES_PATH} - nothing is synced")
        return
    try:
        sources = parse_sources(text)
    except (yaml.YAMLError, AttributeError, KeyError, TypeError) as e:
        # Don't echo the file: it names private repos, and public PDK repos run this too.
        raise SystemExit(f"{target}:{SOURCES_PATH} is malformed ({type(e).__name__})") from None
    target_dir = find_target_dir(sources, args.source_repo, args.project_dir)
    if target_dir is None:
        print(f"::notice::{args.source_repo}:{args.project_dir} is not mapped in {target}:{SOURCES_PATH} - not synced")
        return

    branch = branch_name(target_dir)
    recorded = _recorded_source_commit(target, branch)
    if recorded and is_superseded(_compare_status(args.source_repo, args.source_sha, recorded)):
        print(
            f"::notice::{target}:{branch} already carries {args.source_repo}@{recorded[:7]}, "
            f"the same as or newer than {args.source_sha[:7]} - not synced"
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "target"
        _run(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", f"https://github.com/{target}.git", str(clone)])
        deleted = replace_dir(clone / target_dir, args.snapshot)
        # --force: gsp's .gitignore (build/, *.zip, ...) must not filter the mirror.
        _run(["git", "add", "--all", "--force", "--", target_dir], cwd=clone)
        if not _run(["git", "status", "--porcelain", "--", target_dir], cwd=clone):
            print(f"{target}:{target_dir} already matches {args.source_repo}:{args.project_dir}")
            stale = _open_pr_number(target, branch)
            if stale and not args.dry_run:
                # Otherwise someone could still merge it and reintroduce what the source reverted.
                _run(
                    [
                        "gh", "pr", "close", stale, "--repo", target, "--delete-branch", "--comment",
                        f"`{target_dir}` on main already matches {args.source_repo}@{args.source_sha[:7]}; closing.",
                    ]
                )
            return

        title = pr_title(target_dir, args.source_repo)
        body = render_pr_body(
            target_dir=target_dir,
            source_repo=args.source_repo,
            project_dir=_norm(args.project_dir),
            source_sha=args.source_sha,
            pdk_version=args.pdk_version,
            deleted=deleted,
        )
        if args.dry_run:
            print(f"[dry-run] would push {branch} and open/update PR {title!r} (label {LABEL}: {bool(deleted)})")
            print(body)
            return

        _run(
            [
                "git",
                "-c",
                f"user.name={COMMITTER_NAME}",
                "-c",
                f"user.email={COMMITTER_EMAIL}",
                "commit",
                "--quiet",
                "-m",
                commit_message(target_dir, args.source_repo, args.source_sha, args.pdk_version),
            ],
            cwd=clone,
        )
        _run(["git", "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}"], cwd=clone)
        _open_or_update_pr(target, branch, title, body, bool(deleted))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="Mirror one sample-project snapshot into gfp-sample-projects as a PR")
    s.add_argument("snapshot", type=Path, help="Pristine `git archive` tar of the sample project")
    s.add_argument("--source-repo", required=True, help="owner/name of the PDK repo")
    s.add_argument("--project-dir", required=True, help="The sample project's path in the PDK repo")
    s.add_argument("--source-sha", required=True, help="PDK repo commit the snapshot was taken at")
    s.add_argument("--pdk-version", required=True, help="PDK version the snapshot was tested against")
    s.add_argument("--target-repo", default=TARGET_REPO)
    s.add_argument("--dry-run", action="store_true", help="Stop before commit/push/PR; print what would happen")
    _sync(parser.parse_args())


if __name__ == "__main__":
    main()
