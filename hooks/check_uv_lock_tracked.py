"""Pre-commit hook: verify uv.lock exists and is tracked by git.

If a repo manages dependencies with uv but doesn't commit `uv.lock`, CI
resolves dependencies fresh on every run instead of reproducing a pinned
set. A dependency release can silently change behavior between CI runs with
no code change on your side.

This hook only applies to repos that actually use uv (detected via the
Makefile's `install` target invoking `uv`, a `[tool.uv]` table in
pyproject.toml, or a `uv.lock` already present on disk) — `check-makefile-
targets` only *warns* (never errors) when an `install` target uses pip
instead of uv, so a pip-based install is a supported, passing configuration
today, and this hook must not force uv adoption on repos that legitimately
don't use it.

For uv-based repos, this hook fails if:
  - `uv.lock` doesn't exist on disk at all, or
  - `uv.lock` exists but git doesn't have it tracked (e.g. it's gitignored,
    or was simply never `git add`ed).

It intentionally does NOT parse `.gitignore` for a matching pattern — a
hand-written gitignore-glob matcher is its own source of false positives
(an unrelated `*.lock` entry) and false negatives (an untracked file with no
`.gitignore` entry at all, or a legitimately force-added file that still
happens to have a stale `.gitignore` line). Asking git directly whether the
file is tracked is the actual invariant that determines CI reproducibility,
and it's unambiguous.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from hooks._utils import CheckResult, is_self_repo

LOCK_FILE = "uv.lock"


def _uses_uv(
    makefile_path: Path = Path("Makefile"), pyproject_path: Path = Path("pyproject.toml")
) -> bool:
    """Best-effort check for whether this repo manages dependencies with uv.

    Looks for either signal: the Makefile's `install` target invoking `uv`,
    or a `[tool.uv]` table in pyproject.toml. Either is enough to consider
    the repo uv-based and subject to the tracked-lock-file requirement.
    """
    try:
        makefile_content = makefile_path.read_text()
    except OSError:
        makefile_content = ""

    install_match = re.search(
        r"^install\s*:.*?(?=^\S|\Z)", makefile_content, re.MULTILINE | re.DOTALL
    )
    if install_match and re.search(r"\buv\b", install_match.group(0)):
        return True

    try:
        pyproject_content = pyproject_path.read_text()
    except OSError:
        pyproject_content = ""

    return "[tool.uv]" in pyproject_content


def _git_ls_files(
    path: str, cwd: Path | str = ".", timeout: float = 5.0
) -> str | None:
    """Return `git ls-files -- <path>` stdout, or None if git couldn't run.

    Empty string means git ran fine and reports the path as untracked.
    Non-empty (containing the path) means it's tracked. `None` means the
    command itself failed (not a git repo, git binary missing, timeout) —
    callers should treat that as "can't tell" and warn, not error.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", path],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout


def main() -> int:
    result = CheckResult("check-uv-lock-tracked")

    if is_self_repo():
        return result.report()

    lock_path = Path(LOCK_FILE)
    if not lock_path.exists():
        if not _uses_uv():
            return result.report()
        result.error(
            f"{LOCK_FILE} not found. PDK repos must commit a {LOCK_FILE} so "
            "CI resolves dependencies deterministically instead of doing a "
            f"fresh resolution on every run. Run `uv lock` and commit the "
            f"generated {LOCK_FILE}."
        )
        return result.report()

    tracked_output = _git_ls_files(LOCK_FILE)
    if tracked_output is None:
        result.warn(
            f"could not determine whether {LOCK_FILE} is tracked by git "
            "(git unavailable or this isn't a git repository) — skipping "
            "the tracked-file check"
        )
        return result.report()

    if LOCK_FILE not in tracked_output.split():
        result.error(
            f"{LOCK_FILE} exists but is not tracked by git. Check "
            f".gitignore for an entry matching {LOCK_FILE} and remove it, "
            f"then `git add {LOCK_FILE}` and commit it. Without a committed "
            "lock file, CI resolves dependencies fresh on every run, so a "
            "new dependency release can silently change behavior and break "
            "tests with no code change on your side."
        )

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
