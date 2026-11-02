"""Pre-commit hook: flag dead nav entries in zensical.toml.

Zensical does not fail the build when a ``nav`` entry points at a page that
doesn't exist — not even with ``--strict``. The entry still renders in the
sidebar and links to a 404, so a renamed or deleted notebook leaves a dead
link on the published site with nothing in CI to notice.

Most PDK doc pages are generated at build time (``write_cells.py``,
``nbconvert`` over the sample-project notebooks, ...), so "the file isn't on
disk" alone is not enough to call an entry dead. An entry is reported only
when its target is missing from ``docs_dir`` **and** nothing in the repo
appears to produce it. A target counts as produced when any of these hold:

  - a tracked file shares its stem (``10_layout.ipynb`` -> ``10_layout.md``),
  - the stem is spelled out in a Makefile or a ``.py``/``.sh``/``.mk`` /
    ``.toml``/``.yml``/``.yaml``/``.j2`` file (``cp references/drc.md
    docs/drc_summary.md``),
  - a prefix of the stem is followed by a template placeholder in one of
    those files (``f"models_{band}.md"`` covers ``models_cband.md``,
    ``oband_"$$(basename ...)"`` covers ``oband_1_layout.md``).

That errs towards passing: a generator that mentions the stem but no longer
writes the file will slip through. It never flags a page that is generated.

No-ops when the repo has no ``zensical.toml`` (at the root or under
``docs/``) or the file has no ``nav``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from hooks._utils import CheckResult, load_toml

CONFIG_CANDIDATES = (Path("zensical.toml"), Path("docs/zensical.toml"))
GENERATOR_SUFFIXES = {".py", ".sh", ".mk", ".toml", ".yml", ".yaml", ".j2"}
# Doc-site configs list nav entries themselves, so a mention there proves nothing.
SKIP_NAMES = {"zensical.toml", "mkdocs.yml", "mkdocs-pdf.yml"}
# What follows a prefix when the rest of the name is filled in at runtime:
# f-string / str.format "{", shell "$", or a closing quote before "$" ('"$$').
PLACEHOLDER = r"""(?:\{|\$|["']\$)"""


def _nav_targets(nav: Any) -> list[str]:
    """Flatten a zensical nav tree into its page paths, in order."""
    out: list[str] = []
    if isinstance(nav, str):
        out.append(nav)
    elif isinstance(nav, list):
        for item in nav:
            out.extend(_nav_targets(item))
    elif isinstance(nav, dict):
        for value in nav.values():
            out.extend(_nav_targets(value))
    return out


def _tracked_files() -> list[Path]:
    """Return git-tracked files, or every file under CWD outside git."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [p for p in Path().rglob("*") if p.is_file() and ".git" not in p.parts]
    return [Path(p) for p in proc.stdout.split("\0") if p]


def _generator_text(files: list[Path]) -> str:
    """Concatenate every file that could plausibly write a docs page."""
    chunks = []
    for path in files:
        if path.name in SKIP_NAMES:
            continue
        if path.name != "Makefile" and path.suffix not in GENERATOR_SUFFIXES:
            continue
        try:
            chunks.append(path.read_text(errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _is_generated(stem: str, stems: set[str], text: str) -> bool:
    if stem in stems:
        return True
    if re.search(rf"(?<![\w-]){re.escape(stem)}(?![\w-])", text):
        return True
    parts = stem.split("_")
    for i in range(1, len(parts)):
        prefix = "_".join(parts[:i]) + "_"
        if re.search(rf"(?<![\w-]){re.escape(prefix)}{PLACEHOLDER}", text):
            return True
    return False


def main() -> int:
    result = CheckResult("check-zensical-nav")

    config = next((c for c in CONFIG_CANDIDATES if c.is_file()), None)
    if config is None:
        return 0

    try:
        data = load_toml(str(config)) or {}
    except ValueError as exc:  # tomllib/tomli TOMLDecodeError subclasses it
        result.error(f"{config}: invalid TOML ({exc})")
        return result.report()

    project = data.get("project", {})
    nav = project.get("nav")
    if not nav:
        return 0

    docs_dir = config.parent / project.get("docs_dir", "docs")
    files = _tracked_files()
    stems = {f.stem for f in files}
    text = _generator_text(files)

    for target in _nav_targets(nav):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # https:, mailto:, ...
            continue
        path = docs_dir / target.split("#", 1)[0]
        if path.exists():
            continue
        if _is_generated(Path(target).stem, stems, text):
            continue
        result.error(
            f"{config}: nav entry '{target}' -> {path} does not exist "
            "and nothing in the repo generates it"
        )

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
