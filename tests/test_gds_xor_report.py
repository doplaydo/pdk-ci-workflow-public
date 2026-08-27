"""Tests for scripts/gds_xor_report.py.

The script needs klayout and matplotlib, which are installed by the workflow at
runtime rather than being dev dependencies of this repo, so the whole module
skips when they are absent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

kdb = pytest.importorskip("klayout.db", reason="klayout is a runtime-only dependency")
pytest.importorskip("matplotlib", reason="matplotlib is a runtime-only dependency")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gds_xor_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gds_xor_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gds_xor_report"] = module
    spec.loader.exec_module(module)
    return module


gds_xor_report = _load_module()


def write_gds(
    path: Path,
    boxes: dict[tuple[int, int], list[tuple[int, int, int, int]]],
    cell_name: str = "TOP",
) -> None:
    """Write a one-cell GDS from ``{(layer, datatype): [(x1, y1, x2, y2), ...]}``.

    Coordinates are in nm, matching the default 0.001 um database unit.
    """
    layout = kdb.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell(cell_name)
    for (layer, datatype), rects in boxes.items():
        index = layout.layer(layer, datatype)
        for x1, y1, x2, y2 in rects:
            cell.shapes(index).insert(kdb.Box(x1, y1, x2, y2))
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(path))


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    old, new, out = tmp_path / "old", tmp_path / "new", tmp_path / "out"
    old.mkdir()
    new.mkdir()
    return old, new, out


def run(module_args: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["gds_xor_report.py", *module_args])
    return gds_xor_report.main()


def compare(old: Path, new: Path, relpath: str = "a.gds"):
    return gds_xor_report.compare_file(old / relpath, new / relpath, relpath)


def test_identical_layouts_report_no_difference(roots):
    old, new, _ = roots
    boxes = {(1, 0): [(0, 0, 1000, 1000)]}
    write_gds(old / "a.gds", boxes)
    write_gds(new / "a.gds", boxes)

    diff, xor_layout, plots = compare(old, new)

    assert not diff.has_diff
    assert xor_layout is None
    assert plots == {}


def test_hierarchy_is_flattened_before_comparison(roots):
    """The same geometry drawn flat and via an instance must compare equal."""
    old, new, _ = roots

    flat = kdb.Layout()
    flat.dbu = 0.001
    top = flat.create_cell("TOP")
    top.shapes(flat.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    flat.write(str(old / "a.gds"))

    nested = kdb.Layout()
    nested.dbu = 0.001
    child = nested.create_cell("CHILD")
    child.shapes(nested.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    parent = nested.create_cell("TOP")
    parent.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans()))
    nested.write(str(new / "a.gds"))

    diff, _, _ = compare(old, new)

    assert not diff.has_diff


def test_added_geometry_is_reported_with_area(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]})
    write_gds(new / "a.gds", {(1, 0): [(0, 0, 2000, 1000)]})

    diff, xor_layout, plots = compare(old, new)

    assert diff.has_diff
    assert diff.status == "modified"
    (layer,) = diff.cells[0].layers
    assert layer.layer == "1/0"
    # 1 um x 1 um of new material, nothing taken away.
    assert layer.added_um2 == pytest.approx(1.0)
    assert layer.removed_um2 == pytest.approx(0.0)
    assert layer.total_um2 == pytest.approx(1.0)
    assert xor_layout is not None
    assert plots


def test_removed_geometry_is_reported_separately_from_added(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 2000, 1000)]})
    write_gds(new / "a.gds", {(1, 0): [(1000, 0, 4000, 1000)]})

    diff, _, _ = compare(old, new)

    (layer,) = diff.cells[0].layers
    assert layer.removed_um2 == pytest.approx(1.0)
    assert layer.added_um2 == pytest.approx(2.0)
    assert layer.total_um2 == pytest.approx(3.0)


def test_layers_without_a_difference_are_omitted(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)], (2, 0): [(0, 0, 500, 500)]})
    write_gds(new / "a.gds", {(1, 0): [(0, 0, 1000, 2000)], (2, 0): [(0, 0, 500, 500)]})

    diff, _, _ = compare(old, new)

    reported = [layer.layer for layer in diff.cells[0].layers]
    assert reported == ["1/0"]


def test_layer_present_on_only_one_side_is_reported(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]})
    write_gds(
        new / "a.gds", {(1, 0): [(0, 0, 1000, 1000)], (5, 3): [(0, 0, 1000, 1000)]}
    )

    diff, _, _ = compare(old, new)

    (layer,) = diff.cells[0].layers
    assert layer.layer == "5/3"
    assert layer.added_um2 == pytest.approx(1.0)


def test_added_file_treats_the_old_side_as_empty(roots):
    old, new, _ = roots
    write_gds(new / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]})

    diff, _, _ = compare(old, new)

    assert diff.status == "added"
    (layer,) = diff.cells[0].layers
    assert layer.added_um2 == pytest.approx(1.0)
    assert layer.removed_um2 == pytest.approx(0.0)


def test_deleted_file_treats_the_new_side_as_empty(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]})

    diff, _, _ = compare(old, new)

    assert diff.status == "deleted"
    (layer,) = diff.cells[0].layers
    assert layer.removed_um2 == pytest.approx(1.0)


def test_renamed_lone_top_cell_is_paired_and_noted(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]}, cell_name="OLD_TOP")
    write_gds(new / "a.gds", {(1, 0): [(0, 0, 2000, 1000)]}, cell_name="NEW_TOP")

    diff, _, _ = compare(old, new)

    assert "renamed" in diff.note
    assert diff.cells[0].cell == "OLD_TOP -> NEW_TOP"
    # Paired, so only the extra 1 um^2 shows up - not a full delete plus add.
    (layer,) = diff.cells[0].layers
    assert layer.added_um2 == pytest.approx(1.0)
    assert layer.removed_um2 == pytest.approx(0.0)


def test_differing_database_units_are_rescaled(roots):
    """Same physical square, different dbu on each side: no difference."""
    old, new, _ = roots

    coarse = kdb.Layout()
    coarse.dbu = 0.01
    coarse.create_cell("TOP").shapes(coarse.layer(1, 0)).insert(kdb.Box(0, 0, 100, 100))
    coarse.write(str(old / "a.gds"))

    fine = kdb.Layout()
    fine.dbu = 0.001
    fine.create_cell("TOP").shapes(fine.layer(1, 0)).insert(kdb.Box(0, 0, 1000, 1000))
    fine.write(str(new / "a.gds"))

    diff, _, _ = compare(old, new)

    assert "database unit changed" in diff.note
    assert not diff.has_diff


def test_main_writes_report_xor_gds_and_summaries(roots, monkeypatch):
    old, new, out = roots
    write_gds(old / "a.gds", {(1, 0): [(0, 0, 1000, 1000)]})
    write_gds(new / "a.gds", {(1, 0): [(0, 0, 2000, 1000)]})

    assert (
        run(
            [
                "--old-root",
                str(old),
                "--new-root",
                str(new),
                "--outdir",
                str(out),
                "a.gds",
            ],
            monkeypatch,
        )
        == 0
    )

    assert (out / "report.pdf").exists()
    assert (out / "xor" / "a.xor.gds").exists()
    assert (out / "xor" / "a.old.gds").exists()
    assert (out / "xor" / "a.new.gds").exists()

    payload = json.loads((out / "summary.json").read_text())
    assert payload[0]["file"] == "a.gds"
    assert payload[0]["cells"][0]["layers"][0]["added_um2"] == pytest.approx(1.0)

    summary = (out / "summary.md").read_text()
    assert "`a.gds`" in summary
    assert "gds-xor-report" in summary


def test_main_still_writes_an_empty_report_when_nothing_differs(roots, monkeypatch):
    old, new, out = roots
    boxes = {(1, 0): [(0, 0, 1000, 1000)]}
    write_gds(old / "a.gds", boxes)
    write_gds(new / "a.gds", boxes)

    assert (
        run(
            [
                "--old-root",
                str(old),
                "--new-root",
                str(new),
                "--outdir",
                str(out),
                "a.gds",
            ],
            monkeypatch,
        )
        == 0
    )

    assert (out / "report.pdf").exists()
    assert not (out / "xor" / "a.xor.gds").exists()
    assert "No XOR differences found" in (out / "summary.md").read_text()
    assert json.loads((out / "summary.json").read_text()) == []


def test_xor_gds_keeps_shapes_on_their_original_layer(roots):
    old, new, _ = roots
    write_gds(old / "a.gds", {(7, 2): [(0, 0, 1000, 1000)]})
    write_gds(new / "a.gds", {(7, 2): [(0, 0, 2000, 1000)]})

    _, xor_layout, _ = compare(old, new)

    assert xor_layout is not None
    assert [str(info) for info in xor_layout.layer_infos()] == ["7/2"]
    (top,) = xor_layout.top_cells()
    assert top.name == "TOP_XOR"
    region = kdb.Region(top.begin_shapes_rec(xor_layout.layer(7, 2)))
    assert region.area() * xor_layout.dbu**2 == pytest.approx(1.0)


def test_slugify_flattens_nested_reference_paths():
    assert (
        gds_xor_report.slugify("tests/test_x.gds/gds_ref/cross.gds")
        == "tests__test_x.gds__gds_ref__cross"
    )


def test_ellipsize_keeps_the_tail():
    assert gds_xor_report.ellipsize("short", 10) == "short"
    assert gds_xor_report.ellipsize("abcdefghij", 6) == "...hij"
