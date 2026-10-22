"""Upload a tested sample-project ZIP unchanged to the GDSFactory+ portal.

Resolve the portal key from its catalog and the archived pyproject. Use the
exact PDK pin, else the installed version; refuse untested versions, disallowed
portal names, and keys shared by sibling projects in --repo-root.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).parent))
from sample_project_ci import pdk_distribution

BASE_URL = "https://api.gdsfactory.com"

_EXACT_COMPARATORS = ("===", "==")


@dataclasses.dataclass(frozen=True)
class PdkRecord:
    """One row from the portal's PDK catalog (from compat.portal_client.PdkRecord)."""

    name: str
    import_names: list[str]
    package_names: list[str]


def _normalize(name: str) -> str:
    """Normalize a distribution name per PEP 503 (from compat.pins._normalize)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pdk_import_name(pyproject_text: str) -> str:
    """Return ``tool.gdsfactoryplus.pdk.name`` (from compat.portal_keys.read_pdk_import_name).

    Reads exactly this path and no other: some projects carry a sibling key
    with the same string value, which must not be mistaken for this one.
    """
    data = tomllib.loads(pyproject_text)
    name = data.get("tool", {}).get("gdsfactoryplus", {}).get("pdk", {}).get("name")
    if not name:
        raise LookupError("no tool.gdsfactoryplus.pdk.name in pyproject.toml")
    return name


def versions_match(target: str, installed: str) -> bool:
    """Return whether two version strings name the same release (``4.0`` == ``4.0.0``)."""

    def release(version: str) -> tuple[int, ...] | str:
        parts = version.split(".")
        if not all(part.isdigit() for part in parts):
            return version
        numbers = [int(part) for part in parts]
        while numbers and numbers[-1] == 0:
            numbers.pop()
        return tuple(numbers)

    return release(target) == release(installed)


def upload_version(exact_pin: str | None, installed: str) -> str:
    """Return the version to upload at: the exact pin, else what was installed.

    Raises ``ValueError`` unless it names the version that was installed, so
    content is never labelled with a version it wasn't tested against.
    """
    target = exact_pin or installed
    if not installed or not versions_match(target, installed):
        raise ValueError(f"tested against {installed or '(unknown)'}, not {target}")
    return target


def find_collisions(keys: dict[str, tuple[str, str]]) -> set[str]:
    """Return the project dirs whose portal key another dir also derives.

    Adapted from compat.portal_keys.detect_collisions: returns the colliding
    dirs instead of raising, so each upload leg can refuse only its own.
    """
    by_key: dict[tuple[str, str], list[str]] = {}
    for project_dir, key in keys.items():
        by_key.setdefault(key, []).append(project_dir)
    return {d for dirs in by_key.values() if len(dirs) > 1 for d in dirs}


def parse_exact_pin(pyproject_text: str, distribution: str) -> str | None:
    """Return the version ``distribution`` is pinned to with ``==``/``===``, or ``None``.

    Adapted from compat.pins.parse_pinned_version, which accepts any
    comparator: a lower bound (``~=``, ``>=``) names no single version, since
    the install resolves past it.
    """
    name = re.escape(distribution).replace(r"\-", "[-_.]")
    comparators = "|".join(re.escape(c) for c in _EXACT_COMPARATORS)
    pattern = re.compile(
        rf"(?P<name>{name})"
        rf"(?P<extras>\[[^\]]*\])?"
        rf"\s*(?P<comparator>{comparators})\s*"
        rf"(?P<version>[A-Za-z0-9_.!+*-]+)",
        re.IGNORECASE,
    )
    target = _normalize(distribution)
    for requirement in (
        tomllib.loads(pyproject_text).get("project", {}).get("dependencies", [])
    ):
        match = pattern.match(requirement.strip())
        if match and _normalize(match.group("name")) == target:
            return match.group("version")
    return None


def resolve_pdk_name(
    catalog: list[PdkRecord], pdk_import_name: str, distribution: str
) -> str:
    """Return the portal catalog ``name`` for a sample project.

    Adapted from compat.portal_keys.resolve_pdk_id: same matching, returns
    ``.name`` instead of ``.id``. Matches ``distribution`` against
    ``package_names``; when that is ambiguous (a multi-variant family: an
    umbrella row plus one dedicated row per variant, all one package), narrows
    by an exact ``name`` match first, since the umbrella row's
    ``import_names`` lists every variant too. Raises ``LookupError`` unless
    exactly one record remains.
    """
    target = _normalize(distribution)
    matches = [p for p in catalog if target in {_normalize(n) for n in p.package_names}]
    if len(matches) > 1:
        exact_name_matches = [p for p in matches if p.name == pdk_import_name]
        if len(exact_name_matches) == 1:
            matches = exact_name_matches
        else:
            matches = [p for p in matches if pdk_import_name in p.import_names]
    if len(matches) != 1:
        raise LookupError(
            f"expected exactly one portal PDK for "
            f"(distribution={distribution!r}, pdk_import_name={pdk_import_name!r}), "
            f"found {len(matches)}"
        )
    return matches[0].name


def list_pdks(api_key: str) -> list[PdkRecord]:
    """Fetch the portal's PDK catalog (from compat.portal_client.PortalClient.list_pdks)."""
    import httpx

    response = httpx.get(
        f"{BASE_URL}/api/portal/sdk/v1/pdks/list",
        headers={"x-api-key": api_key},
        timeout=30.0,
    )
    response.raise_for_status()
    return [
        PdkRecord(
            name=item["name"],
            import_names=item["import_names"],
            package_names=item["package_names"],
        )
        for item in response.json()["data"]
    ]


def upload(
    api_key: str, pdk_name: str, pdk_import_name: str, version: str, archive: bytes
) -> str:
    """Upload one archive (from compat.portal_client.PortalClient.upload); return the response body."""
    import httpx

    response = httpx.post(
        f"{BASE_URL}/api/admin/sdk/v1/pdks/sample-projects/upload",
        headers={"x-api-key": api_key},
        data={
            "pdk_name": pdk_name,
            "pdk_import_name": pdk_import_name,
            "version": version,
        },
        files={"file": ("archive.zip", archive, "application/zip")},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.text


def _sample_project_dirs(repo_root: Path) -> list[str]:
    """Same discovery as the workflows' ``find . -maxdepth 3 ... -path '*--sample-projects/*'``."""
    return sorted(
        p.parent.relative_to(repo_root).as_posix()
        for p in repo_root.glob("*--sample-projects/*/pyproject.toml")
    )


def _portal_key(
    catalog: list[PdkRecord], pyproject_text: str, root_name: str
) -> tuple[str, str]:
    distribution = pdk_distribution(pyproject_text, root_name)
    pdk_import_name = read_pdk_import_name(pyproject_text)
    return resolve_pdk_name(catalog, pdk_import_name, distribution), pdk_import_name


def _refuse(message: str) -> None:
    print(f"::error::{message}")
    raise SystemExit(1)


def _gh(endpoint: str, *, pages: bool = False) -> bytes:
    command = ["gh", "api", endpoint]
    if pages:
        command += ["--paginate", "--slurp"]
    return subprocess.run(command, check=True, capture_output=True).stdout


def _items(endpoint: str, key: str) -> list[dict]:
    return [
        item for page in json.loads(_gh(endpoint, pages=True)) for item in page[key]
    ]


def superseding_run(meta: dict, source_run_id: int) -> int | None:
    """Find newer own-main released evidence for this project, not overall run success.

    Called immediately before POST while the workflow holds its project lock.
    A passed artifact can exist while other projects (or uploads) are still running.
    No artifact or source code is extracted or executed. API/invalid-evidence
    errors fail closed; expired evidence cannot establish supersession.
    """
    repo, slug = meta["source_repo"], meta["slug"]
    runs = _items(
        f"repos/{repo}/actions/workflows/sample-projects-released.yml/runs?branch=main&per_page=100",
        "workflow_runs",
    )
    for run in sorted(runs, key=lambda item: item["id"], reverse=True):
        if (
            run["id"] <= source_run_id
            or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("head_branch") != "main"
            or (run.get("head_repository") or {}).get("full_name") != repo
        ):
            continue
        artifacts = _items(
            f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100", "artifacts"
        )
        passed_name = f"sample-project-tested-released-{slug}"
        for artifact in artifacts:
            if artifact["name"] != passed_name or artifact.get("expired"):
                continue
            try:
                archive = _gh(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
            except subprocess.CalledProcessError as error:
                if b"410" not in (error.stderr or b""):
                    raise
                continue
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                passed = json.loads(zf.read("meta.json"))
                version = zf.read("installed-version.txt").decode().strip()
            if (
                passed.get("source_repo") != repo
                or passed.get("source_sha") != run.get("head_sha")
                or not passed.get("source_sha")
                or passed.get("project_dir") != meta["project_dir"]
                or passed.get("slug") != slug
                or not passed.get("tree_sha")
                or not version
            ):
                raise ValueError(
                    f"invalid tested metadata for {repo}/{slug} in run {run['id']}"
                )
            return run["id"]
    return None


def _upload_output(uploaded: bool) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            print(f"uploaded={str(uploaded).lower()}", file=stream)


def _upload(args: argparse.Namespace) -> None:
    api_key = os.environ.get("GFP_API_KEY", "")
    if not api_key:
        _refuse("GFP_API_KEY is not set")

    meta = json.loads(args.meta.read_text())
    project_dir = meta["project_dir"]
    root_name = tomllib.loads((args.repo_root / "pyproject.toml").read_text())[
        "project"
    ]["name"]
    archive = args.archive.read_bytes()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        sample_pyproject = zf.read("pyproject.toml").decode("utf-8")
    distribution = pdk_distribution(sample_pyproject, root_name)
    try:
        version = upload_version(
            parse_exact_pin(sample_pyproject, distribution), args.installed_version
        )
    except ValueError as e:
        _refuse(f"{project_dir}: {distribution} {e} - refusing to upload")

    catalog = list_pdks(api_key)
    pdk_name, pdk_import_name = _portal_key(catalog, sample_pyproject, root_name)
    if args.allowed_pdk_names is not None:
        allowed = {
            name.strip() for name in args.allowed_pdk_names.split(",") if name.strip()
        }
        if pdk_name not in allowed:
            _refuse(
                f"{project_dir}: pdk_name={pdk_name} is not in allowed PDK names {args.allowed_pdk_names}"
            )

    keys = {project_dir: (pdk_name, pdk_import_name)}
    for other in _sample_project_dirs(args.repo_root):
        if other == project_dir:
            continue
        try:
            keys[other] = _portal_key(
                catalog,
                (args.repo_root / other / "pyproject.toml").read_text(),
                root_name,
            )
        except LookupError:
            continue  # cannot resolve, so cannot upload either: no collision
    if project_dir in find_collisions(keys):
        clashing = sorted(d for d, k in keys.items() if k == keys[project_dir])
        _refuse(
            f"{clashing} all resolve to pdk_name={pdk_name} pdk_import_name={pdk_import_name}; "
            "they would overwrite each other's archive - give each a distinct tool.gdsfactoryplus.pdk.name"
        )

    print(
        f"{'[dry-run] Would upload' if args.dry_run else 'Uploading'} {project_dir} ({len(archive)} bytes) as "
        f"pdk_name={pdk_name} pdk_import_name={pdk_import_name} version={version}; "
        f"allowed_pdk_names={args.allowed_pdk_names or '(unrestricted)'}; collisions=none"
    )
    if not args.allow_stale:
        newer = superseding_run(meta, args.source_run_id)
        if newer is not None:
            print(
                f"::notice::{project_dir}: skipping stale upload; superseded by passed released-mode "
                f"run https://github.com/{meta['source_repo']}/actions/runs/{newer}"
            )
            _upload_output(False)
            return
    if not args.dry_run:
        print(upload(api_key, pdk_name, pdk_import_name, version, archive))
        _upload_output(True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upload", help="Upload a tested sample-project ZIP unchanged")
    up.add_argument(
        "--source-run-id",
        type=int,
        required=True,
        help="Run that produced the tested archive",
    )
    up.add_argument(
        "--allow-stale",
        action="store_true",
        help="Explicit source_run dispatch only: bypass freshness",
    )
    up.add_argument("archive", type=Path, help="Tested sample-project ZIP")
    up.add_argument("--meta", type=Path, required=True, help="Package metadata JSON")
    up.add_argument(
        "--installed-version",
        required=True,
        help="PDK version the project was tested against",
    )
    up.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Source checkout used for collision checks",
    )
    up.add_argument(
        "--allowed-pdk-names",
        default=None,
        help="Comma-separated allowed portal PDK names; omit for no restriction",
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print the upload without the POST",
    )
    _upload(parser.parse_args())


if __name__ == "__main__":
    main()
