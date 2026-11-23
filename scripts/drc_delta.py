"""Compare two KLayout DRC reports and say whether the change adds violations.

The counts match the DRC badge: one per ``<item>`` in the report, grouped by
the item's category. A missing or unreadable report is reported as unavailable
rather than as zero, so a failed run is not mistaken for a clean layout.

Given the GDS each report was run on, every item is also attributed to the
layout it falls in: the top-level instance of ``all_cells`` whose bounding box
holds the centre of the marker. That needs ``klayout``; without it, or without
the GDS, the comment lists categories only.

Writes markdown and exits 0. The caller decides whether a difference matters.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# A comment that lists every rule of a large deck is harder to read than the
# summary. The biggest moves are the ones a reviewer acts on.
MAX_CATEGORIES = 30
MAX_LAYOUT_ROWS = 50

# A marker whose centre is in no top-level instance: a rule flagging the
# packing of all_cells itself, or a marker spanning several layouts.
TOP_LEVEL = "(not inside one layout)"

_POINT = re.compile(
    r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?),(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)


@dataclass(frozen=True)
class Counts:
    total: int
    by_category: dict[str, int]
    # (layout, category) -> count; empty when layouts could not be resolved.
    by_layout: dict[tuple[str, str], int]


Box = tuple[float, float, float, float]


def load_layouts(gds: Path | None) -> list[tuple[str, Box]] | None:
    """Top-level instances of the packed GDS as (cell name, bbox in um)."""
    if gds is None or not gds.is_file():
        return None
    try:
        import klayout.db as kdb
    except ImportError:
        return None
    layout = kdb.Layout()
    try:
        layout.read(str(gds))
    except RuntimeError:
        return None
    tops = layout.top_cells()
    if len(tops) != 1:
        return None
    boxes = []
    for inst in tops[0].each_inst():
        box = inst.dbbox()
        if box.empty():
            continue
        boxes.append((inst.cell.name, (box.left, box.bottom, box.right, box.top)))
    return boxes


def marker_centre(item: ET.Element) -> tuple[float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for value in item.iter("value"):
        for x, y in _POINT.findall(value.text or ""):
            xs.append(float(x))
            ys.append(float(y))
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def layout_of(item: ET.Element, layouts: list[tuple[str, Box]]) -> str:
    centre = marker_centre(item)
    if centre is None:
        return TOP_LEVEL
    x, y = centre
    holding = [
        (name, (b[2] - b[0]) * (b[3] - b[1]))
        for name, b in layouts
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]
    ]
    if not holding:
        return TOP_LEVEL
    # Packed instances should not overlap; if they do, the tightest one wins.
    return min(holding, key=lambda pair: pair[1])[0]


def category_of(item: ET.Element) -> str:
    node = item.find("category")
    if node is None:
        return "(uncategorized)"
    text = (node.text or "").strip().strip("'\"")
    if text:
        return text
    name = (node.findtext("name") or "").strip()
    return name or "(uncategorized)"


def load_counts(path: Path, gds: Path | None = None) -> tuple[Counts | None, str]:
    """Read a report. The string is why the count is unavailable."""
    if not path.is_file() or path.stat().st_size == 0:
        return None, "report is missing"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None, "report is not valid XML"
    if root.tag != "report-database":
        return None, "report is not a KLayout report-database"
    items = root.find("items")
    if items is None:
        return None, "report has no items list"
    layouts = load_layouts(gds)
    counts: Counter[str] = Counter()
    by_layout: Counter[tuple[str, str]] = Counter()
    for item in items.findall("item"):
        category = category_of(item)
        counts[category] += 1
        if layouts is not None:
            by_layout[(layout_of(item, layouts), category)] += 1
    return Counts(sum(counts.values()), dict(counts), dict(by_layout)), ""


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _errors(count: int) -> str:
    noun = "DRC error" if count == 1 else "DRC errors"
    return f"{count} {noun}"


def _sentence(base: Counts, head: Counts) -> str:
    delta = head.total - base.total
    if delta > 0:
        noun = "DRC error" if delta == 1 else "DRC errors"
        return (
            f"This pull request adds {delta} {noun} "
            f"({base.total} on the base, {head.total} here)."
        )
    if delta < 0:
        removed = -delta
        return (
            f"This pull request reduces DRC errors by {removed} "
            f"({base.total} on the base, {head.total} here)."
        )
    return f"This pull request does not change the DRC error count ({head.total})."


def _change(delta: int) -> str:
    """Signed change, red when it adds errors and green when it removes them."""
    if delta > 0:
        return f"🔴 {_signed(delta)}"
    if delta < 0:
        return f"🟢 {_signed(delta)}"
    return "0"


def _layout_sections(base: Counts, head: Counts, requested: bool) -> list[str]:
    if not base.by_layout and not head.by_layout:
        if requested and (base.total or head.total):
            return [
                "",
                (
                    "_Layouts could not be resolved for these reports, so only "
                    "categories are listed._"
                ),
            ]
        return []
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    rules: dict[str, set[str]] = {}
    for counts, totals in ((base, before), (head, after)):
        for (layout, category), count in counts.by_layout.items():
            totals[layout] += count
            rules.setdefault(layout, set()).add(category)
    # Most added errors first, then the most errors overall.
    ranked = sorted(
        rules,
        key=lambda layout: (
            -(after[layout] - before[layout]),
            -after[layout],
            layout,
        ),
    )
    worse = sum(after[layout] > before[layout] for layout in ranked)
    better = sum(after[layout] < before[layout] for layout in ranked)
    lines = [
        "",
        (
            f"<details><summary>DRC errors by layout: {worse} worse, "
            f"{better} better, {len(ranked)} with errors</summary>"
        ),
        "",
        "| layout | base | head | change | categories (head, change) |",
        "|---|---:|---:|---:|---|",
    ]
    for layout in ranked[:MAX_LAYOUT_ROWS]:
        parts = []
        for category in sorted(rules[layout]):
            old = base.by_layout.get((layout, category), 0)
            new = head.by_layout.get((layout, category), 0)
            moved = f" ({_signed(new - old)})" if new != old else ""
            parts.append(f"`{category}` {new}{moved}")
        lines.append(
            f"| `{layout}` | {before[layout]} | {after[layout]} | "
            f"{_change(after[layout] - before[layout])} | {', '.join(parts)} |"
        )
    hidden = len(ranked) - MAX_LAYOUT_ROWS
    if hidden > 0:
        lines += ["", f"{hidden} more layouts; see the DRC artifact."]
    lines += ["", "</details>"]
    return lines


def render(
    base_path: Path,
    head_path: Path,
    base_gds: Path | None = None,
    head_gds: Path | None = None,
) -> str:
    """Markdown comparing ``base_path`` with ``head_path``."""
    base, base_reason = load_counts(base_path, base_gds)
    head, head_reason = load_counts(head_path, head_gds)
    lines = ["## DRC error change", ""]
    if base is None and head is None:
        lines.append(
            "DRC error counts are unavailable for both the base "
            f"({base_reason}) and this pull request ({head_reason})."
        )
        return "\n".join(lines) + "\n"
    if base is None:
        assert head is not None
        lines.append(
            f"Base DRC error count is unavailable ({base_reason}). "
            f"This pull request has {_errors(head.total)}."
        )
        return "\n".join(lines) + "\n"
    if head is None:
        lines.append(
            f"This pull request's DRC error count is unavailable ({head_reason}). "
            f"The base has {_errors(base.total)}."
        )
        return "\n".join(lines) + "\n"

    lines.append(_sentence(base, head))
    names = sorted(
        set(base.by_category) | set(head.by_category),
        key=lambda name: (
            -abs(head.by_category.get(name, 0) - base.by_category.get(name, 0)),
            name,
        ),
    )
    changed = [
        name
        for name in names
        if head.by_category.get(name, 0) != base.by_category.get(name, 0)
    ]
    if changed:
        lines += [
            "",
            "| category | base | head | change |",
            "|---|---:|---:|---:|",
        ]
        for name in changed[:MAX_CATEGORIES]:
            before = base.by_category.get(name, 0)
            after = head.by_category.get(name, 0)
            lines.append(
                f"| `{name}` | {before} | {after} | {_signed(after - before)} |"
            )
        hidden = len(changed) - MAX_CATEGORIES
        if hidden > 0:
            lines += [
                "",
                f"{hidden} more categories changed; the full reports are in the DRC artifact.",
            ]
    lines += _layout_sections(base, head, base_gds is not None or head_gds is not None)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="Base revision report")
    parser.add_argument("--head", type=Path, required=True, help="Head revision report")
    parser.add_argument("--out", type=Path, required=True, help="Markdown output path")
    parser.add_argument("--base-gds", type=Path, help="GDS the base report was run on")
    parser.add_argument("--head-gds", type=Path, help="GDS the head report was run on")
    args = parser.parse_args(argv)
    text = render(args.base, args.head, args.base_gds, args.head_gds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
