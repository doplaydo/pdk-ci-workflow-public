"""Zip a sample project and upload it to the GDSFactory+ portal.

Run from the PDK repo root against a pristine snapshot of one
``{pdk_package}--sample-projects/{template}--project/`` directory (already
holding its generated ``build/models.nyanlib`` + ``build/symbols/``). The
portal row key is resolved from the portal's own catalog, not from per-repo
config:

- ``pdk_import_name``: ``tool.gdsfactoryplus.pdk.name`` in the sample
  project's pyproject.
- ``pdk_name``: the catalog record whose ``package_names`` contains the PDK
  distribution the sample project depends on.
- ``version``: ``--version`` when given (the release being published),
  otherwise the sample project's exact (``==``) pin on its PDK distribution,
  otherwise ``--installed-version``. It must match ``--installed-version``
  (what the project was actually tested and its nyanlib generated against).

Refuses to upload when another sample project in the repo resolves to the
same ``(pdk_name, pdk_import_name)``, since the two would overwrite each
other. The upload always overwrites; nothing is read back from the portal
first.

``distribution PROJECT_DIR`` prints the PDK distribution a sample project
depends on, so the workflow can read its installed version.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import os
import re
import sys
import tomllib
import zipfile
from pathlib import Path

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


def build_zip(root: Path) -> bytes:
    """Zip every file under ``root``, with paths relative to ``root``.

    From compat.archive.build_zip, minus its inode-seeded timestamps (only
    needed there to tell archives apart in its own tests).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(root).as_posix())
    return buffer.getvalue()


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


def find_pdk_distribution(pyproject_text: str, root_name: str) -> str:
    """Return the dependency naming the PDK repo's own package or a variant of it.

    Same matching test-sample-projects.yml uses to find a variant: the
    dependency is ``root_name`` itself or ``root_name-<variant>``.
    """
    root = _normalize(root_name)
    for requirement in tomllib.loads(pyproject_text).get("project", {}).get("dependencies", []):
        name = _normalize(re.split(r"[\[~><=!;@\s]", requirement.strip())[0])
        if name == root or name.startswith(root + "-"):
            return name
    raise LookupError(f"no dependency on {root_name!r} (or a variant of it) in pyproject.toml")


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


def upload_version(release_version: str, exact_pin: str | None, installed: str) -> str:
    """Return the version to upload at: the release, else the exact pin, else what was installed.

    Raises ``ValueError`` unless it names the version that was installed, so
    content is never labelled with a version it wasn't tested against.
    """
    target = release_version or exact_pin or installed
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
    for requirement in tomllib.loads(pyproject_text).get("project", {}).get("dependencies", []):
        match = pattern.match(requirement.strip())
        if match and _normalize(match.group("name")) == target:
            return match.group("version")
    return None


def resolve_pdk_name(catalog: list[PdkRecord], pdk_import_name: str, distribution: str) -> str:
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


def upload(api_key: str, pdk_name: str, pdk_import_name: str, version: str, archive: bytes) -> str:
    """Upload one archive (from compat.portal_client.PortalClient.upload); return the response body."""
    import httpx

    response = httpx.post(
        f"{BASE_URL}/api/admin/sdk/v1/pdks/sample-projects/upload",
        headers={"x-api-key": api_key},
        data={"pdk_name": pdk_name, "pdk_import_name": pdk_import_name, "version": version},
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


def _portal_key(catalog: list[PdkRecord], pyproject_text: str, root_name: str) -> tuple[str, str]:
    distribution = find_pdk_distribution(pyproject_text, root_name)
    pdk_import_name = read_pdk_import_name(pyproject_text)
    return resolve_pdk_name(catalog, pdk_import_name, distribution), pdk_import_name


def _upload(args: argparse.Namespace) -> None:
    api_key = os.environ.get("GFP_API_KEY", "")
    if not api_key:
        sys.exit("GFP_API_KEY is not set")

    root_name = tomllib.loads(Path("pyproject.toml").read_text())["project"]["name"]
    sample_pyproject = (args.snapshot / "pyproject.toml").read_text()
    distribution = find_pdk_distribution(sample_pyproject, root_name)
    try:
        version = upload_version(
            args.version, parse_exact_pin(sample_pyproject, distribution), args.installed_version
        )
    except ValueError as e:
        sys.exit(f"{args.project_dir}: {distribution} {e} - refusing to upload")

    catalog = list_pdks(api_key)
    pdk_name, pdk_import_name = _portal_key(catalog, sample_pyproject, root_name)
    keys = {args.project_dir: (pdk_name, pdk_import_name)}
    for other in _sample_project_dirs(Path(".")):
        if other == args.project_dir:
            continue
        try:
            keys[other] = _portal_key(catalog, Path(other, "pyproject.toml").read_text(), root_name)
        except LookupError:
            continue  # cannot resolve, so cannot upload either: no collision
    if args.project_dir in find_collisions(keys):
        clashing = sorted(d for d, k in keys.items() if k == keys[args.project_dir])
        sys.exit(
            f"{clashing} all resolve to pdk_name={pdk_name} pdk_import_name={pdk_import_name}; "
            "they would overwrite each other's archive - give each a distinct tool.gdsfactoryplus.pdk.name"
        )

    archive = build_zip(args.snapshot)
    print(
        f"Uploading {args.project_dir} ({len(archive)} bytes) as "
        f"pdk_name={pdk_name} pdk_import_name={pdk_import_name} version={version}"
    )
    print(upload(api_key, pdk_name, pdk_import_name, version, archive))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    dist = sub.add_parser("distribution", help="Print the PDK distribution a sample project depends on")
    dist.add_argument("project_dir", type=Path)
    up = sub.add_parser("upload", help="Zip a sample-project snapshot and upload it (run from the PDK repo root)")
    up.add_argument("snapshot", type=Path, help="Pristine snapshot of the sample project to zip")
    up.add_argument("--project-dir", required=True, help="The sample project's path in the repo")
    up.add_argument("--version", default="", help="Version to upload at; default: exact PDK pin, else installed")
    up.add_argument("--installed-version", default="", help="PDK version the project was tested against")
    args = parser.parse_args()

    if args.command == "distribution":
        root_name = tomllib.loads(Path("pyproject.toml").read_text())["project"]["name"]
        print(find_pdk_distribution((args.project_dir / "pyproject.toml").read_text(), root_name))
    else:
        _upload(args)


if __name__ == "__main__":
    main()
