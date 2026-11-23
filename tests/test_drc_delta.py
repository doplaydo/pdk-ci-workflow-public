"""Tests for scripts/drc_delta.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import drc_delta  # noqa: E402


def report(items: list[str]) -> str:
    body = "".join(f"<item><category>{name}</category></item>" for name in items)
    return f"<report-database><items>{body}</items></report-database>"


def test_reduce_lists_only_categories_that_moved(tmp_path):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(report(["width"] * 5 + ["space"] + ["via"]))
    head.write_text(report(["width"] * 2 + ["space"] * 3 + ["via"]))

    text = drc_delta.render(base, head)

    assert "reduces DRC errors by 1 (7 on the base, 6 here)." in text
    assert "| `space` | 1 | 3 | +2 |" in text
    assert "| `width` | 5 | 2 | -3 |" in text
    # Unchanged rules stay out of the table, and the larger move is listed first.
    assert "`via`" not in text
    assert text.index("`width`") < text.index("`space`")


def test_add_uses_the_singular_when_one_error_appears(tmp_path):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(report([]))
    head.write_text(report(["width"]))

    text = drc_delta.render(base, head)

    assert "adds 1 DRC error (0 on the base, 1 here)." in text
    assert "| `width` | 0 | 1 | +1 |" in text


def test_unchanged_count_has_no_category_table(tmp_path):
    path = tmp_path / "same.lyrdb"
    path.write_text(report(["width", "width"]))

    text = drc_delta.render(path, path)

    assert "does not change the DRC error count (2)." in text
    assert "| category |" not in text


def test_missing_base_is_not_reported_as_zero(tmp_path):
    head = tmp_path / "head.lyrdb"
    head.write_text(report(["width"]))

    text = drc_delta.render(tmp_path / "missing.lyrdb", head)

    assert "Base DRC error count is unavailable (report is missing)." in text
    assert "This pull request has 1 DRC error." in text
    assert "adds" not in text
    assert "reduces" not in text


def test_invalid_head_report_is_unavailable(tmp_path):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(report(["width"]))
    head.write_text("<report-database/>")

    text = drc_delta.render(base, head)

    assert "unavailable (report has no items list)." in text
    assert "The base has 1 DRC error." in text


def test_category_table_stops_after_the_largest_moves(tmp_path):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(report([]))
    head.write_text(report([f"rule{index:02d}" for index in range(35)]))

    text = drc_delta.render(base, head)

    assert text.count("| `rule") == drc_delta.MAX_CATEGORIES
    assert "5 more categories changed" in text


def test_main_writes_the_markdown(tmp_path, capsys):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    out = tmp_path / "nested" / "drc-delta.md"
    base.write_text(report(["width", "width"]))
    head.write_text(report(["width"]))

    assert (
        drc_delta.main(["--base", str(base), "--head", str(head), "--out", str(out)])
        == 0
    )

    assert "reduces DRC errors by 1" in out.read_text()
    assert "reduces DRC errors by 1" in capsys.readouterr().out


def located(items: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<item><category>{name}</category><values><value>{value}</value></values></item>"
        for name, value in items
    )
    return f"<report-database><items>{body}</items></report-database>"


def packed_gds(path: Path, cells: dict[str, tuple[int, int]]) -> Path:
    """One top cell holding a 10x10 um box cell per name, at the given offset."""
    kdb = pytest.importorskip("klayout.db")
    layout = kdb.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("all_cells")
    layer = layout.layer(1, 0)
    for name, (x, y) in cells.items():
        cell = layout.create_cell(name)
        cell.shapes(layer).insert(kdb.DBox(0, 0, 10, 10))
        top.insert(kdb.DCellInstArray(cell.cell_index(), kdb.DTrans(kdb.DVector(x, y))))
    layout.write(str(path))
    return path


def test_errors_are_attributed_to_the_layout_holding_the_marker(tmp_path):
    gds = packed_gds(tmp_path / "all_cells.gds", {"ring": (0, 0), "mmi": (100, 0)})
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(located([("width", "polygon: (1,1;1,2;2,2;2,1)")]))
    head.write_text(
        located(
            [
                ("width", "polygon: (1,1;1,2;2,2;2,1)"),
                ("space", "edge-pair: (101,1;101,5)|(103,1;103,5)"),
                ("space", "edge-pair: (102,1;102,5)|(104,1;104,5)"),
                ("floorplan", "box: (50,50;60,60)"),
            ]
        )
    )

    text = drc_delta.render(base, head, gds, gds)

    assert "DRC errors by layout: 2 worse, 0 better, 3 with errors" in text
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    layout_rows = [row for row in rows if row.count("|") == 6]
    # Most added errors first; the unchanged layout last, uncoloured.
    assert layout_rows == [
        "| `mmi` | 0 | 2 | 🔴 +2 | `space` 2 (+2) |",
        f"| `{drc_delta.TOP_LEVEL}` | 0 | 1 | 🔴 +1 | `floorplan` 1 (+1) |",
        "| `ring` | 1 | 1 | 0 | `width` 1 |",
    ]


def test_removed_errors_are_green(tmp_path):
    gds = packed_gds(tmp_path / "all_cells.gds", {"ring": (0, 0)})
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(located([("width", "box: (1,1;2,2)")] * 3))
    head.write_text(located([("width", "box: (1,1;2,2)")]))

    text = drc_delta.render(base, head, gds, gds)

    assert "| `ring` | 3 | 1 | 🟢 -2 | `width` 1 (-2) |" in text


def test_without_the_gds_the_comment_says_layouts_are_unresolved(tmp_path):
    base = tmp_path / "base.lyrdb"
    head = tmp_path / "head.lyrdb"
    base.write_text(report(["width"]))
    head.write_text(report(["width", "width"]))

    text = drc_delta.render(
        base, head, tmp_path / "missing.gds", tmp_path / "missing.gds"
    )

    assert "Layouts could not be resolved" in text
    assert "DRC errors by layout" not in text
