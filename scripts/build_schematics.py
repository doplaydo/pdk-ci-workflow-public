"""Build every shipped .gsch through the editor's direct-to-GDS pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def shipped_schematics(archive: Path) -> list[str]:
    """Use the committed archive, not SDK factory discovery or the installed tree."""
    with zipfile.ZipFile(archive) as source:
        return sorted(name for name in source.namelist() if name.endswith(".gsch"))


def validate_report(expected: list[str], report: dict) -> bool:
    if not isinstance(report, dict):
        return False
    results = report.get("results", [])
    if not isinstance(results, list) or not all(
        isinstance(item, dict) for item in results
    ):
        return False
    return (
        report.get("discovered") == expected
        and sorted(item.get("path", "") for item in results) == expected
        and all(item.get("status") == "passed" for item in results)
    )


def build_one(source: Path, output: Path, pdk: str) -> None:
    sys.path.insert(0, str(Path.cwd()))
    from gdsfactoryplus.compile import build_nyancir_gds
    import klayout.db as kdb

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    qualified = pdk if "." in pdk else f"{pdk}.PDK"
    build_nyancir_gds(
        str(source.resolve()),
        str(output),
        qualified,
        str(output.with_suffix(".dschematic")),
    )
    layout = kdb.Layout()
    layout.read(str(output))
    # Inspect recursive polygons, not file size, cell count, or a bounding box.
    if not any(
        not kdb.Region(cell.begin_shapes_rec(layer)).is_empty()
        for cell in layout.top_cells()
        for layer in layout.layer_indexes()
    ):
        raise ValueError("schematic produced empty geometry")


def run(project: Path, archive: Path, out: Path, pdk: str) -> int:
    expected = shipped_schematics(archive)
    report = {"discovered": expected, "results": []}
    out.parent.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(expected):
        output = out.parent / "schematic-builds" / str(index) / "layout.gds"
        result = {"path": name, "status": "failed"}
        print(f"Building schematic: {name}", flush=True)
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--one",
                    name,
                    "--output",
                    str(output.resolve()),
                    "--pdk",
                    pdk,
                ],
                cwd=project,
                check=True,
                timeout=600,
            )
            result["status"] = "passed"
        except (OSError, subprocess.SubprocessError) as error:
            result["error"] = str(error)
        report["results"].append(result)
        print(f"{result['status']}: {name}", flush=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
    out.write_text(json.dumps(report, indent=2) + "\n")
    if not expected:
        print("No shipped .gsch schematics (archive inventory is empty).")
    return int(not validate_report(expected, report))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pdk", required=True)
    args = parser.parse_args()
    if args.one:
        build_one(args.one, args.output, args.pdk)
        return 0
    return run(args.project, args.archive, args.out, args.pdk)


if __name__ == "__main__":
    raise SystemExit(main())
