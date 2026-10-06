"""Reject reference fixtures and Git metadata in PDK wheels."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


REFERENCE_DIRECTORIES = frozenset(
    {
        "reference",
        "references",
        "refs",
        "reference-files",
        "reference_files",
        "pdk-reference-files",
        "pdk_reference_files",
        "gds_ref",
        "gds_ref_models",
    }
)


def forbidden_path_reason(path: str) -> str | None:
    """Check complete path components, including nested reference directories."""
    components = path.replace("\\", "/").split("/")
    for component in components:
        normalized = component.casefold()
        if normalized in REFERENCE_DIRECTORIES:
            return f"reference directory {component!r}"
        if normalized == ".git":
            return "Git metadata"
    return None


def check_build_mappings(config: dict[str, Any]) -> list[str]:
    """Reject Hatch mappings that rename reference fixtures into runtime paths."""
    build = config.get("tool", {}).get("hatch", {}).get("build", {})
    wheel = build.get("targets", {}).get("wheel", {})
    errors = []
    for section_name, section in (("build", build), ("build.targets.wheel", wheel)):
        for mapping_name in (
            "force-include",
            "sources",
            "shared-data",
            "shared-scripts",
            "extra-metadata",
        ):
            mappings = section.get(mapping_name, {})
            if mapping_name == "sources" and isinstance(mappings, list):
                mappings = {source: "" for source in mappings}
            for source, destination in mappings.items():
                for path in (source, destination):
                    reason = forbidden_path_reason(path)
                    if reason:
                        errors.append(
                            f"tool.hatch.{section_name}.{mapping_name}: "
                            f"{source!r} -> {destination!r} includes {reason}"
                        )
                        break
    return errors


def check_wheel(wheel_path: Path) -> list[str]:
    """Inspect archive membership without extracting or importing the PDK."""
    with zipfile.ZipFile(wheel_path) as archive:
        return [
            f"{entry.filename}: {reason}"
            for entry in archive.infolist()
            if (reason := forbidden_path_reason(entry.filename))
        ]


def collect_wheels(paths: list[Path]) -> list[Path]:
    """Require a wheel at every input, rather than passing an empty build."""
    wheels = set()
    for path in paths:
        if path.is_dir():
            matches = list(path.glob("*.whl"))
            if not matches:
                raise ValueError(f"No wheels found in {path}")
            wheels.update(matches)
        elif path.is_file() and path.suffix == ".whl":
            wheels.add(path)
        else:
            raise ValueError(f"Not a wheel file or wheel directory: {path}")
    return sorted(wheels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", type=Path, nargs="+", help="Wheel files or directories"
    )
    parser.add_argument(
        "--pyproject", type=Path, help="Also validate Hatch build mappings"
    )
    arguments = parser.parse_args(argv)
    errors = []
    try:
        if arguments.pyproject:
            with arguments.pyproject.open("rb") as source:
                errors.extend(check_build_mappings(tomllib.load(source)))
        wheels = collect_wheels(arguments.paths)
        for wheel in wheels:
            violations = check_wheel(wheel)
            errors.extend(f"{wheel.name}: {violation}" for violation in violations)
            if not violations:
                print(f"PASS: {wheel.name}")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        errors.append(str(error))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            "Reference fixtures and Git metadata must not ship in wheels. "
            "Move required runtime files into the PDK package, for example "
            "<package>/gds/ or <package>/models/data/, update their readers, "
            "and exclude reference-only directories from the build.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
