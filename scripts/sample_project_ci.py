"""Sample-project archive preparation, test selection, and CI reporting.

Run independently of a checkout's environment with:
``uv run --no-project --with tomlkit python sample_project_ci.py ...``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import tomllib

NOTEBOOK_EXCLUDE = re.compile(r"hfss_|hfss-|matlab_|matlab-|_meep|meep_")


def slug(project_dir: str) -> str:
    return project_dir.replace("/", "-").strip("-")


def discover(repo_root: Path) -> list[dict]:
    """Match the existing find command's depth-three, non-venv projects."""
    projects = []
    for path in repo_root.glob("*--sample-projects/*/pyproject.toml"):
        relative = path.relative_to(repo_root)
        if path.is_file() and ".venv" not in relative.parts:
            directory = relative.parent.as_posix()
            projects.append({"slug": slug(directory), "project_dir": directory})
    return sorted(projects, key=lambda project: project["project_dir"])


def _normalize(name: str) -> str:
    """PEP 503 distribution-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pdk_distribution(sample_pyproject: str, root_name: str) -> str:
    """Resolve the root PDK or its first variant in dependency order.

    An editable PDK sample project ships the full PDK source instead of
    depending on it: its own distribution is the root PDK (or a variant).
    Its import name (``tool.gdsfactoryplus.pdk.name``) may differ, e.g.
    ``ihp-gdsfactory`` imports as ``ihp``.
    """
    root = _normalize(root_name)
    data = tomllib.loads(sample_pyproject)
    for requirement in data.get("project", {}).get("dependencies", []):
        name = _normalize(re.split(r"[\[~><=!;@\s]", requirement.strip())[0])
        if name == root or name.startswith(root + "-"):
            return name
    own = _normalize(data.get("project", {}).get("name", ""))
    pdk = data.get("tool", {}).get("gdsfactoryplus", {}).get("pdk", {}).get("name")
    if pdk and (own == root or own.startswith(root + "-")):
        return own
    raise LookupError(
        f"no dependency on {root_name!r} (or a variant of it) in pyproject.toml"
    )


def variant(root_name: str, distribution: str) -> str | None:
    root, name = _normalize(root_name), _normalize(distribution)
    return name[len(root) + 1 :] if name.startswith(root + "-") else None


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo_root), *args], text=True
    ).strip()


def package(
    repo_root: Path, project_dir: str, out_dir: Path, source_repo: str, source_sha: str
) -> dict:
    """Archive committed content and derive its metadata from the same HEAD."""
    root = tomllib.loads(_git(repo_root, "show", "HEAD:pyproject.toml"))["project"][
        "name"
    ]
    sample = _git(repo_root, "show", f"HEAD:{project_dir}/pyproject.toml")
    import_name = (
        tomllib.loads(sample)
        .get("tool", {})
        .get("gdsfactoryplus", {})
        .get("pdk", {})
        .get("name")
    )
    if not import_name:
        raise LookupError("no tool.gdsfactoryplus.pdk.name in pyproject.toml")
    meta = {
        "project_dir": project_dir,
        "slug": slug(project_dir),
        "pdk_distribution": pdk_distribution(sample, root),
        "pdk_import_name": import_name,
        "tree_sha": _git(repo_root, "rev-parse", f"HEAD:{project_dir}"),
        "source_repo": source_repo,
        "source_sha": source_sha,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "sample-project.zip").open("wb") as archive:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "archive",
                "--format=zip",
                f"HEAD:{project_dir}",
            ],
            stdout=archive,
            check=True,
        )
    _write_json(out_dir / "meta.json", meta)
    return meta


def set_editable_source(pyproject: str, distribution: str, path: str) -> str:
    import tomlkit

    data = tomlkit.parse(pyproject)
    sources = data.setdefault("tool", {}).setdefault("uv", {}).setdefault("sources", {})
    source = tomlkit.inline_table()
    source.update({"path": path, "editable": True})
    distribution = _normalize(distribution)
    for key in list(sources):
        if key != distribution and _normalize(key) == distribution:
            del sources[key]
    sources[distribution] = source
    return tomlkit.dumps(data)


def add_gfp_constraint(pyproject: str, version: str) -> str:
    import tomlkit

    data = tomlkit.parse(pyproject)
    uv = data.setdefault("tool", {}).setdefault("uv", {})
    uv.setdefault("constraint-dependencies", []).append(f"gdsfactoryplus=={version}")
    return tomlkit.dumps(data)


def select_notebooks(paths: list[str]) -> tuple[list[str], list[str]]:
    run, excluded = [], []
    for path in paths:
        (excluded if NOTEBOOK_EXCLUDE.search(path) else run).append(path)
    return run, excluded


def skip_execution_cells(notebook: dict) -> int:
    return sum(
        "skip-execution" in cell.get("metadata", {}).get("tags", [])
        for cell in notebook.get("cells", [])
    )


def gfp_test_skips(output: str) -> list[str]:
    return [
        line.strip() for line in output.splitlines() if re.search(r"\bSKIPPED\b", line)
    ]


def render_summary(checks: dict[str, str], exclusions: list[str]) -> str:
    def escape(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines = ["| Check | Result | Notes |", "| --- | --- | --- |"]
    lines.extend(
        f"| {escape(check)} | {escape(result)} | |" for check, result in checks.items()
    )
    lines.extend(["", "### Exclusions", ""])
    lines.extend(f"- {escape(item)}" for item in exclusions)
    if not exclusions:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _runner_temp() -> Path:
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())


def _run_notebooks(project_root: Path, out: Path) -> int:
    project_root = project_root.resolve()
    paths = []
    for directory, dirs, files in os.walk(project_root):
        dirs[:] = [name for name in dirs if name != ".venv"]
        paths.extend(
            (Path(directory) / name).relative_to(project_root).as_posix()
            for name in files
            if name.endswith(".ipynb") and (Path(directory) / name).is_file()
        )
    paths.sort()
    run, excluded = select_notebooks(paths)
    report = {"run": run, "excluded": excluded, "failed": [], "skip_execution": {}}
    output_dir = _runner_temp() / "nb"
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        try:
            report["skip_execution"][path] = skip_execution_cells(
                json.loads((project_root / path).read_text())
            )
        except (OSError, ValueError) as error:
            print(f"Cannot read notebook {path}: {error}", file=sys.stderr)
            if path in run:
                report["failed"].append(path)
    for path in run:
        if path in report["failed"]:
            continue
        try:
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "jupyter",
                    "nbconvert",
                    "--to",
                    "notebook",
                    "--execute",
                    "--output-dir",
                    str(output_dir),
                    "--ExecutePreprocessor.timeout=600",
                    path,
                ],
                cwd=project_root,
                check=False,
            )
            if result.returncode == 0:
                continue
        except OSError as error:
            print(f"Cannot execute {path}: {error}", file=sys.stderr)
        print(f"Notebook failed: {path}", file=sys.stderr)
        report["failed"].append(path)
    _write_json(out, report)
    return int(bool(report["failed"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    archive = commands.add_parser("package")
    archive.add_argument("project_dir")
    archive.add_argument("--out", type=Path, required=True)
    archive.add_argument("--source-repo", required=True)
    archive.add_argument("--source-sha", required=True)
    edit = commands.add_parser("edit-pyproject")
    edit.add_argument("path", type=Path)
    edit.add_argument("--editable-source", metavar="DIST=PATH")
    edit.add_argument("--gfp-version")
    notebooks = commands.add_parser("run-notebooks")
    notebooks.add_argument("project_root", type=Path)
    notebooks.add_argument(
        "--out", type=Path, default=_runner_temp() / "notebooks.json"
    )
    summary = commands.add_parser("summary")
    summary.add_argument(
        "--checks", required=True, help="JSON object mapping check names to results"
    )
    summary.add_argument("--notebooks", type=Path)
    summary.add_argument(
        "--exclusions", type=Path, nargs="+", action="extend", default=[]
    )
    args = parser.parse_args()
    if args.command == "discover":
        print(json.dumps(discover(Path.cwd())))
    elif args.command == "package":
        package(
            Path.cwd(), args.project_dir, args.out, args.source_repo, args.source_sha
        )
    elif args.command == "edit-pyproject":
        text = args.path.read_text()
        if args.editable_source:
            distribution, separator, path = args.editable_source.partition("=")
            if not separator or not distribution or not path:
                parser.error("--editable-source must be DIST=PATH")
            text = set_editable_source(text, distribution, path)
        if args.gfp_version:
            text = add_gfp_constraint(text, args.gfp_version)
        args.path.write_text(text)
    elif args.command == "run-notebooks":
        return _run_notebooks(args.project_root, args.out)
    elif args.command == "summary":
        exclusions = []
        for path in args.exclusions:
            items = json.loads(path.read_text())
            if not isinstance(items, list) or not all(
                isinstance(item, str) for item in items
            ):
                parser.error("--exclusions files must contain JSON string lists")
            exclusions.extend(items)
        if args.notebooks:
            report = json.loads(args.notebooks.read_text())
            exclusions.extend(report["excluded"])
            exclusions.extend(
                f"{path}: {count} skip-execution cells"
                for path, count in report["skip_execution"].items()
            )
        print(render_summary(json.loads(args.checks), exclusions), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
