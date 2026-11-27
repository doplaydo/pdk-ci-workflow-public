"""Build a cell and write it to build/gds/<cell_name>.gds.

When cell_name is "all_cells", builds every PDK-owned cell that can be
instantiated with default arguments and packs them into a single GDS.
Cells from installed packages (site-packages / .venv), cells located
under a ``samples/`` directory (demo/tapeout cells that reuse BB cells
and would cause cellname collisions), cells whose names start with "_",
and cells that require positional arguments are skipped automatically.

Use ``--skip cell1 cell2 ...`` to skip additional cells by name or
glob pattern (e.g. ``--skip "TEMPLATE_*"`` skips all cells starting
with ``TEMPLATE_``).

The cells of all_cells are packed 100 um apart. A PDK whose DRC deck has
longer-range spacing rules between separate cells (for example a 1000 um
dicing-channel spacing) can raise this in its pyproject.toml, so the pack
does not create violations between unrelated cells::

    [tool.pdk-ci-workflow.drc]
    pack_spacing = 1000

``--spacing`` overrides the setting.
"""

import argparse
import fnmatch
import importlib.util
import inspect
import math
import sys
import tomllib
from pathlib import Path

from gdsfactoryplus import get_pdk

DEFAULT_PACK_SPACING = 100.0


def is_valid_spacing(value: object) -> bool:
    """Return True for a finite, positive int or float (bool excluded)."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def configured_pack_spacing(start: Path) -> float:
    """Return [tool.pdk-ci-workflow.drc] pack_spacing from the nearest pyproject.toml.

    Falls back to DEFAULT_PACK_SPACING when the nearest pyproject.toml has no
    such setting. Exits on a value that is not a finite positive number.
    """
    for directory in (start, *start.parents):
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            value = (
                config.get("tool", {})
                .get("pdk-ci-workflow", {})
                .get("drc", {})
                .get("pack_spacing", DEFAULT_PACK_SPACING)
            )
            if not is_valid_spacing(value):
                sys.exit(
                    f"Error: [tool.pdk-ci-workflow.drc] pack_spacing in {pyproject} "
                    f"must be a finite positive number, got {value!r}"
                )
            return float(value)
    return DEFAULT_PACK_SPACING


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("cell_name", help="Cell to build, or 'all_cells'")
parser.add_argument("--skip", nargs="*", default=[], help="Cell names to skip when building all_cells")
parser.add_argument(
    "--spacing",
    type=float,
    default=None,
    help="Spacing between packed cells for all_cells, in um (default: "
    "[tool.pdk-ci-workflow.drc] pack_spacing, else 100)",
)
args = parser.parse_args()

cell_name = args.cell_name
skip_cells = set(args.skip)

# Resolve and validate the pack spacing before the slow step of building
# every cell, so a bad setting fails fast.
if cell_name == "all_cells":
    if args.spacing is not None:
        if not is_valid_spacing(args.spacing):
            sys.exit(
                f"Error: --spacing must be a finite positive number, got {args.spacing}"
            )
        spacing = args.spacing
    else:
        spacing = configured_pack_spacing(Path.cwd())

Path("build/gds").mkdir(parents=True, exist_ok=True)
pdk = get_pdk()

if cell_name == "all_cells":
    import gdsfactory as gf

    components = []
    for name, func in sorted(pdk.cells.items()):
        if any(fnmatch.fnmatchcase(name, pat) for pat in skip_cells):
            print(f"Skipping {name}: matches --skip pattern")
            continue

        if name.startswith("_"):
            print(f"Skipping {name}: starts with '_'")
            continue

        # Skip cells from installed packages (not PDK-owned)
        try:
            src = inspect.getfile(inspect.unwrap(func))
        except TypeError:
            continue
        if ".venv" in src or "site-packages" in src:
            continue

        # Skip demo/tapeout cells under a samples/ directory: they reuse BB
        # cells already registered as top-level PDK cells, which would cause
        # cellname collisions when packed together.
        if "/samples/" in src or src.endswith("/samples"):
            print(f"Skipping {name}: in samples/")
            continue

        # Skip cells that require positional arguments
        sig = inspect.signature(func)
        required = [
            p
            for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        if required:
            print(f"Skipping {name}: requires arguments {[p.name for p in required]}")
            continue

        try:
            components.append(func())
        except Exception as e:  # noqa: BLE001
            print(f"Error instantiating cell {name}: {e}")

    if spacing != DEFAULT_PACK_SPACING:
        print(f"Packing all_cells with spacing {spacing:g} um")

    if components:
        c = gf.pack(components, spacing=spacing)[0]
        c.name = "all_cells"
    else:
        c = gf.Component("all_cells")

    try:
        c.write_gds(f"build/gds/{cell_name}.gds")
    except RuntimeError as e:
        # This string is raised by kfactory's `KCell.write` path in
        # `src/kfactory/kcell.py`, where it delegates to
        # `self._base.kdb_cell.write(...)` for the actual save.
        if "more than one cell" not in str(e):
            raise
        print(f"Warning: {e}")
        print("Flattening to resolve duplicate subcell names...")
        c.flatten()
        c.write_gds(f"build/gds/{cell_name}.gds")
else:
    candidate_names = [cell_name, f"_{cell_name}"]
    cell_func = None

    for candidate_name in candidate_names:
        cell_func = pdk.cells.get(candidate_name)
        if cell_func:
            break

    if cell_func is None:
        # Cell not auto-discovered (e.g. nested in a samples/ subpackage or a
        # directory with hyphens that aren't valid Python identifiers).
        for py_file in sorted(Path(".").rglob(f"{cell_name}.py")):
            spec = importlib.util.spec_from_file_location(cell_name, py_file)
            if spec is None or spec.loader is None:
                continue
            try:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:  # noqa: BLE001
                continue
            for candidate_name in candidate_names:
                cell_func = getattr(mod, candidate_name, None)
                if cell_func:
                    break
            if cell_func:
                break

    if cell_func is None:
        print(f"Error: cell '{cell_name}' not found", file=sys.stderr)
        sys.exit(1)

    c = cell_func()
    c.write_gds(f"build/gds/{cell_name}.gds")
