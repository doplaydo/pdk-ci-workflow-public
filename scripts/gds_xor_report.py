"""Compare two revisions of GDS files and report the per-layer XOR differences.

For every ``<relpath>`` passed on the command line the script reads
``<old-root>/<relpath>`` and ``<new-root>/<relpath>``, pairs up their top
cells, and computes a boolean XOR per layer. A missing file on either side is
treated as an empty layout, so added and deleted GDS files are reported too.

Three kinds of output land in ``--outdir``:

* ``xor/<slug>.xor.gds`` - the XOR shapes, kept on their original
  layer/datatype so the file opens with the PDK layer properties. One top cell
  ``<cell>_XOR`` per differing cell. Beside it, ``<slug>.old.gds`` and
  ``<slug>.new.gds`` so a reviewer can load all three at once.
* ``report.pdf`` - a summary table followed by one page per differing layer:
  old versus new in context, and the XOR alone, zoomed to its bounding box.
* ``summary.md`` / ``summary.json`` - the same numbers for a PR comment or a
  downstream job.

Areas are reported in um^2. Exits 0 whether or not differences are found; the
caller decides what a difference means.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import klayout.db as kdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402

# Rendering more polygons than this per panel is slow and unreadable; the page
# says so rather than silently dropping them.
MAX_POLYGONS_PER_PANEL = 20000

ADDED_COLOR = "#d62728"
REMOVED_COLOR = "#1f77b4"
UNCHANGED_COLOR = "#999999"


@dataclass
class LayerDiff:
    """XOR result for one (cell, layer) pair."""

    layer: str
    added_um2: float
    removed_um2: float
    added_polygons: int
    removed_polygons: int

    @property
    def total_um2(self) -> float:
        return self.added_um2 + self.removed_um2


@dataclass
class CellDiff:
    """XOR results for one top cell, across all its layers."""

    cell: str
    old_cell: str | None
    new_cell: str | None
    layers: list[LayerDiff] = field(default_factory=list)


@dataclass
class FileDiff:
    """XOR results for one GDS file."""

    relpath: str
    status: str  # "modified" | "added" | "deleted"
    cells: list[CellDiff] = field(default_factory=list)
    note: str = ""

    @property
    def has_diff(self) -> bool:
        return any(cell.layers for cell in self.cells)


def slugify(relpath: str) -> str:
    """Flatten a repo-relative path into a single filename component."""
    return relpath.replace("/", "__").removesuffix(".gds")


def load_layout(path: Path) -> kdb.Layout | None:
    """Read a GDS file, or return None when it does not exist on this side."""
    if not path.exists():
        return None
    layout = kdb.Layout()
    layout.read(str(path))
    return layout


def layer_label(info: kdb.LayerInfo) -> str:
    """Human-readable layer name, preferring the name stored in the GDS."""
    if info.name:
        return f"{info.name} ({info.layer}/{info.datatype})"
    return f"{info.layer}/{info.datatype}"


def region_of(
    layout: kdb.Layout | None, cell_name: str | None, info: kdb.LayerInfo
) -> kdb.Region:
    """Merged region for one layer of one cell, flattened through the hierarchy."""
    if layout is None or cell_name is None:
        return kdb.Region()
    cell = layout.cell(cell_name)
    if cell is None:
        return kdb.Region()
    layer_index = layout.find_layer(info)
    if layer_index is None:
        return kdb.Region()
    region = kdb.Region(cell.begin_shapes_rec(layer_index))
    region.merged_semantics = True
    return region.merged()


def scale_region(region: kdb.Region, factor: float) -> kdb.Region:
    """Rescale a region when the two layouts disagree on database units."""
    if math.isclose(factor, 1.0, rel_tol=1e-12):
        return region
    return region.transformed(kdb.ICplxTrans(factor))


def pair_top_cells(
    old: kdb.Layout | None, new: kdb.Layout | None
) -> tuple[list[tuple[str, str | None, str | None]], str]:
    """Pair top cells by name, falling back to position for a lone renamed cell.

    Returns the pairs as ``(label, old_name, new_name)`` plus a note describing
    any fallback that was applied.
    """
    old_tops = sorted(c.name for c in old.top_cells()) if old else []
    new_tops = sorted(c.name for c in new.top_cells()) if new else []

    if len(old_tops) == 1 and len(new_tops) == 1 and old_tops[0] != new_tops[0]:
        label = f"{old_tops[0]} -> {new_tops[0]}"
        return [(label, old_tops[0], new_tops[0])], f"top cell renamed: {label}"

    pairs: list[tuple[str, str | None, str | None]] = []
    for name in sorted(set(old_tops) | set(new_tops)):
        pairs.append(
            (
                name,
                name if name in old_tops else None,
                name if name in new_tops else None,
            )
        )
    only_old = sorted(set(old_tops) - set(new_tops))
    only_new = sorted(set(new_tops) - set(old_tops))
    notes = []
    if only_old:
        notes.append(f"top cells removed: {', '.join(only_old)}")
    if only_new:
        notes.append(f"top cells added: {', '.join(only_new)}")
    return pairs, "; ".join(notes)


def layer_infos(layout: kdb.Layout | None) -> list[kdb.LayerInfo]:
    return list(layout.layer_infos()) if layout else []


def union_layers(old: kdb.Layout | None, new: kdb.Layout | None) -> list[kdb.LayerInfo]:
    """All layers appearing in either layout, deduplicated by layer/datatype."""
    by_key: dict[tuple[int, int], kdb.LayerInfo] = {}
    for info in layer_infos(new) + layer_infos(old):
        key = (info.layer, info.datatype)
        # A named LayerInfo is more useful in the report than an unnamed one.
        if key not in by_key or (info.name and not by_key[key].name):
            by_key[key] = info
    return [by_key[key] for key in sorted(by_key)]


def compare_file(
    old_path: Path, new_path: Path, relpath: str
) -> tuple[FileDiff, kdb.Layout | None, dict]:
    """XOR one GDS file pair; return the diff, the XOR layout, and plot data."""
    old = load_layout(old_path)
    new = load_layout(new_path)

    if old is None and new is None:
        return FileDiff(relpath, "missing", note="file absent on both sides"), None, {}

    status = "modified"
    if old is None:
        status = "added"
    elif new is None:
        status = "deleted"

    reference = new or old
    assert reference is not None
    dbu = reference.dbu
    scale = 1.0
    note = ""
    if (
        old is not None
        and new is not None
        and not math.isclose(old.dbu, new.dbu, rel_tol=1e-12)
    ):
        scale = old.dbu / new.dbu
        note = f"database unit changed {old.dbu} -> {new.dbu}; old geometry rescaled"

    file_diff = FileDiff(relpath, status, note=note)
    xor_layout = kdb.Layout()
    xor_layout.dbu = dbu
    plots: dict[tuple[str, str], dict] = {}

    pairs, pair_note = pair_top_cells(old, new)
    if pair_note:
        file_diff.note = "; ".join(filter(None, [file_diff.note, pair_note]))

    for label, old_name, new_name in pairs:
        cell_diff = CellDiff(cell=label, old_cell=old_name, new_cell=new_name)
        xor_cell: kdb.Cell | None = None

        for info in union_layers(old, new):
            old_region = scale_region(region_of(old, old_name, info), scale)
            new_region = region_of(new, new_name, info)
            added = new_region - old_region
            removed = old_region - new_region
            if added.is_empty() and removed.is_empty():
                continue

            unit_area = dbu * dbu
            cell_diff.layers.append(
                LayerDiff(
                    layer=layer_label(info),
                    added_um2=added.area() * unit_area,
                    removed_um2=removed.area() * unit_area,
                    added_polygons=added.count(),
                    removed_polygons=removed.count(),
                )
            )

            if xor_cell is None:
                xor_cell = xor_layout.create_cell(f"{sanitize_cell_name(label)}_XOR")
            layer_index = xor_layout.layer(info)
            xor_cell.shapes(layer_index).insert(added)
            xor_cell.shapes(layer_index).insert(removed)

            plots[(label, layer_label(info))] = {
                "old": old_region,
                "new": new_region,
                "added": added,
                "removed": removed,
                "dbu": dbu,
            }

        if cell_diff.layers:
            file_diff.cells.append(cell_diff)

    return file_diff, (xor_layout if xor_layout.cells() > 0 else None), plots


def sanitize_cell_name(name: str) -> str:
    """Make a pairing label usable as a GDS cell name."""
    return name.replace(" -> ", "_TO_").replace(" ", "_")


def region_to_patches(
    region: kdb.Region, dbu: float, color: str, alpha: float
) -> tuple[list[PathPatch], bool]:
    """Convert a region to matplotlib patches; also report if it was truncated."""
    patches: list[PathPatch] = []
    truncated = False
    for index, polygon in enumerate(region.each_merged()):
        if index >= MAX_POLYGONS_PER_PANEL:
            truncated = True
            break
        vertices: list[tuple[float, float]] = []
        codes: list[int] = []
        for contour in [list(polygon.each_point_hull())] + [
            list(polygon.each_point_hole(h)) for h in range(polygon.holes())
        ]:
            if not contour:
                continue
            points = [(p.x * dbu, p.y * dbu) for p in contour]
            vertices.extend(points + [points[0]])
            codes.extend(
                [MplPath.MOVETO]
                + [MplPath.LINETO] * (len(points) - 1)
                + [MplPath.CLOSEPOLY]
            )
        if not vertices:
            continue
        patches.append(
            PathPatch(
                MplPath(vertices, codes),
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                linewidth=0.3,
            )
        )
    return patches, truncated


def bbox_um(
    regions: list[kdb.Region], dbu: float
) -> tuple[float, float, float, float] | None:
    """Combined bounding box of several regions, in um."""
    box = kdb.Box()
    for region in regions:
        if not region.is_empty():
            box += region.bbox()
    if box.empty():
        return None
    return (box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu)


def apply_limits(
    axis, bounds: tuple[float, float, float, float] | None, pad_ratio: float = 0.05
) -> None:
    if bounds is None:
        return
    x0, y0, x1, y1 = bounds
    pad = max(x1 - x0, y1 - y0, 1e-6) * pad_ratio
    axis.set_xlim(x0 - pad, x1 + pad)
    axis.set_ylim(y0 - pad, y1 + pad)


def draw_panel(
    axis, entries: list[tuple[kdb.Region, str, float]], dbu: float, title: str
) -> bool:
    truncated_any = False
    for region, color, alpha in entries:
        patches, truncated = region_to_patches(region, dbu, color, alpha)
        truncated_any = truncated_any or truncated
        for patch in patches:
            axis.add_patch(patch)
    axis.set_title(title, fontsize=9)
    axis.set_aspect("equal")
    axis.tick_params(labelsize=6)
    axis.set_xlabel("x (um)", fontsize=7)
    axis.set_ylabel("y (um)", fontsize=7)
    return truncated_any


def render_layer_page(
    pdf: PdfPages, relpath: str, cell: str, layer: str, data: dict
) -> None:
    dbu = data["dbu"]
    old_region, new_region = data["old"], data["new"]
    added, removed = data["added"], data["removed"]

    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 6))

    # Unchanged geometry as a grey backdrop, so the diff is placed in context
    # rather than floating on an empty canvas.
    unchanged = new_region & old_region
    truncated = draw_panel(
        left,
        [
            (unchanged, UNCHANGED_COLOR, 0.35),
            (removed, REMOVED_COLOR, 0.9),
            (added, ADDED_COLOR, 0.9),
        ],
        dbu,
        "whole layer (diff in context)",
    )
    apply_limits(left, bbox_um([old_region, new_region], dbu))
    left.legend(
        handles=[
            Patch(facecolor=UNCHANGED_COLOR, alpha=0.35, label="unchanged"),
            Patch(facecolor=REMOVED_COLOR, alpha=0.9, label="removed (old only)"),
            Patch(facecolor=ADDED_COLOR, alpha=0.9, label="added (new only)"),
        ],
        fontsize=7,
        loc="upper right",
    )

    truncated |= draw_panel(
        right,
        [(removed, REMOVED_COLOR, 0.8), (added, ADDED_COLOR, 0.8)],
        dbu,
        "XOR only (zoomed)",
    )
    apply_limits(right, bbox_um([added, removed], dbu), pad_ratio=0.15)

    added_area = added.area() * dbu * dbu
    removed_area = removed.area() * dbu * dbu
    subtitle = (
        f"added {added_area:.6g} um^2 ({added.count()} polys)   |   "
        f"removed {removed_area:.6g} um^2 ({removed.count()} polys)   |   "
        f"total XOR {added_area + removed_area:.6g} um^2"
    )
    if truncated:
        subtitle += (
            f"\n(only the first {MAX_POLYGONS_PER_PANEL} polygons per panel are drawn)"
        )
    figure.suptitle(f"{relpath}\n{cell} - layer {layer}", fontsize=11)
    figure.tight_layout(rect=(0, 0.09, 1, 0.9))
    figure.text(0.5, 0.025, subtitle, ha="center", va="bottom", fontsize=8)
    pdf.savefig(figure)
    plt.close(figure)


def ellipsize(text: str, limit: int) -> str:
    """Trim from the left, keeping the tail - the distinguishing end of a path
    or a parameterised cell name."""
    return text if len(text) <= limit else "..." + text[-(limit - 3) :]


def render_summary_pages(pdf: PdfPages, diffs: list[FileDiff]) -> None:
    """Cover page listing every differing layer, paginated."""
    rows: list[list[str]] = []
    for file_diff in diffs:
        for cell in file_diff.cells:
            for layer in cell.layers:
                rows.append(
                    [
                        ellipsize(file_diff.relpath, 46),
                        ellipsize(cell.cell, 40),
                        ellipsize(layer.layer, 22),
                        f"{layer.added_um2:.6g}",
                        f"{layer.removed_um2:.6g}",
                        f"{layer.total_um2:.6g}",
                    ]
                )

    header = ["file", "cell", "layer", "added um^2", "removed um^2", "total XOR um^2"]
    col_widths = [0.29, 0.25, 0.14, 0.107, 0.107, 0.107]
    per_page = 28
    pages = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]

    for page_index, page_rows in enumerate(pages):
        figure = plt.figure(figsize=(11, 8.5))
        figure.suptitle("GDS XOR report", fontsize=16, y=0.96)
        if page_index == 0:
            caption = (
                f"{len(diffs)} changed GDS file(s), {len(rows)} layer(s) with a difference.\n"
                "Added = present in the new GDS only. Removed = present in the old GDS only."
            )
            notes = [f"{d.relpath}: {d.note}" for d in diffs if d.note]
            if notes:
                caption += "\n\nNotes:\n" + "\n".join(notes)
            figure.text(0.5, 0.88, caption, ha="center", va="top", fontsize=9)
        axis = figure.add_axes((0.03, 0.05, 0.94, 0.78 if page_index == 0 else 0.86))
        axis.axis("off")
        if page_rows:
            table = axis.table(
                cellText=page_rows,
                colLabels=header,
                colWidths=col_widths,
                loc="upper center",
                cellLoc="left",
            )
            table.auto_set_font_size(False)
            table.set_fontsize(7)
            table.scale(1, 1.3)
        else:
            axis.text(0.5, 0.5, "No XOR differences found.", ha="center", fontsize=12)
        pdf.savefig(figure)
        plt.close(figure)


def write_markdown(diffs: list[FileDiff], path: Path, artifact_hint: str) -> None:
    lines = ["## GDS XOR report", ""]
    rows = [
        (f.relpath, c.cell, layer) for f in diffs for c in f.cells for layer in c.layers
    ]
    if not rows:
        lines.append("No XOR differences found in the changed GDS files.")
    else:
        lines.append(
            "| file | cell | layer | added um^2 | removed um^2 | total XOR um^2 |"
        )
        lines.append("|---|---|---|---:|---:|---:|")
        for relpath, cell, layer in rows:
            lines.append(
                f"| `{relpath}` | `{cell}` | `{layer.layer}` | {layer.added_um2:.6g} "
                f"| {layer.removed_um2:.6g} | {layer.total_um2:.6g} |"
            )
        lines.append("")
        lines.append(
            f"Per-layer images and the XOR GDS are in the **{artifact_hint}** artifact."
        )
    for file_diff in diffs:
        if file_diff.note:
            lines.append(f"- `{file_diff.relpath}`: {file_diff.note}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(diffs: list[FileDiff], path: Path) -> None:
    payload = [
        {
            "file": f.relpath,
            "status": f.status,
            "note": f.note,
            "cells": [
                {
                    "cell": c.cell,
                    "layers": [
                        {
                            "layer": layer.layer,
                            "added_um2": layer.added_um2,
                            "removed_um2": layer.removed_um2,
                            "total_um2": layer.total_um2,
                            "added_polygons": layer.added_polygons,
                            "removed_polygons": layer.removed_polygons,
                        }
                        for layer in c.layers
                    ],
                }
                for c in f.cells
            ],
        }
        for f in diffs
    ]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "relpaths", nargs="*", help="Repo-relative paths of the changed GDS files"
    )
    parser.add_argument(
        "--old-root",
        required=True,
        help="Directory holding the base revision of the GDS files",
    )
    parser.add_argument(
        "--new-root",
        default=".",
        help="Directory holding the head revision (default: cwd)",
    )
    parser.add_argument(
        "--outdir",
        default="build/gds-xor",
        help="Where to write the report and XOR GDS files",
    )
    parser.add_argument(
        "--artifact-name",
        default="gds-xor-report",
        help="Artifact name mentioned in summary.md",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    gds_dir = outdir / "xor"
    gds_dir.mkdir(parents=True, exist_ok=True)

    diffs: list[FileDiff] = []
    all_plots: list[tuple[str, str, str, dict]] = []

    for relpath in args.relpaths:
        old_path = Path(args.old_root) / relpath
        new_path = Path(args.new_root) / relpath
        file_diff, xor_layout, plots = compare_file(old_path, new_path, relpath)

        if not file_diff.has_diff:
            print(f"no XOR difference: {relpath}")
            if file_diff.note:
                print(f"  note: {file_diff.note}")
            continue

        diffs.append(file_diff)
        slug = slugify(relpath)
        if xor_layout is not None:
            xor_layout.write(str(gds_dir / f"{slug}.xor.gds"))
        for source, suffix in ((old_path, "old"), (new_path, "new")):
            if source.exists():
                (gds_dir / f"{slug}.{suffix}.gds").write_bytes(source.read_bytes())
        for (cell, layer), data in plots.items():
            all_plots.append((relpath, cell, layer, data))
        print(f"XOR differences: {relpath} ({len(plots)} layer(s))")

    outdir.mkdir(parents=True, exist_ok=True)
    with PdfPages(outdir / "report.pdf") as pdf:
        render_summary_pages(pdf, diffs)
        for relpath, cell, layer, data in all_plots:
            render_layer_page(pdf, relpath, cell, layer, data)

    write_markdown(diffs, outdir / "summary.md", args.artifact_name)
    write_json(diffs, outdir / "summary.json")

    total_layers = sum(len(c.layers) for f in diffs for c in f.cells)
    print(
        f"{len(diffs)} file(s) with differences across {total_layers} layer(s); report at {outdir / 'report.pdf'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
