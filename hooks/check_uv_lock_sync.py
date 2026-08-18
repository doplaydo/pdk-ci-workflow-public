"""Pre-commit hook: verify uv.lock is in sync with pyproject.toml.

`check-uv-lock-tracked` guarantees a lock file exists and is committed. It
says nothing about whether that lock file still matches the dependency
declarations it was generated from, and nothing else catches the gap
either: `uv sync` (what PDK `install` targets run, in CI and locally)
silently re-locks when the two disagree, so a hand-edited `pyproject.toml`
never fails anything. The drift just sits in the repo until something else
regenerates the lock — typically a dependency bump, whose PR then arrives
carrying an unrelated lock-only diff and no explanation.

This hook closes that gap by asking uv itself: `uv lock --check` exits
non-zero when the lock file would change if regenerated. Editing
`pyproject.toml` without running `uv lock` now fails at commit time, where
the fix is one command, rather than weeks later in someone else's PR.

Scope mirrors `check-uv-lock-tracked`: it only runs when `uv.lock` exists
and the repo is uv-based (reusing that hook's `_uses_uv` detection), so
pip-based repos — including a stray `uv.lock` left in one — are unaffected,
and a missing lock file stays that hook's error to report rather than being
reported twice.

`uv` itself is supplied through the hook's `additional_dependencies`, so the
check is live wherever pre-commit runs. It degrades to a warning rather than
an error when uv can't be run or doesn't answer in time — an environment
problem shouldn't read as a dependency problem.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hooks._utils import CheckResult, is_self_repo
from hooks.check_uv_lock_tracked import _uses_uv

LOCK_FILE = "uv.lock"

# The common case (already in sync) never needs the registry, so try
# offline first — it's local-only and fails fast. Only a real drift check
# needs to re-resolve against the registry, hence the wider timeout there.
OFFLINE_TIMEOUT = 10.0
CHECK_TIMEOUT = 120.0


def _run_uv_lock_check(
    cmd: list[str], cwd: Path | str, timeout: float
) -> tuple[int, str] | None:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _uv_lock_check(cwd: Path | str = ".") -> tuple[int, str] | None:
    """Run `uv lock --check`, returning (exit code, combined output).

    Exit code 0 means the check passed; non-zero means it didn't — that can
    mean lock drift, but can also mean uv couldn't run the check at all for
    an unrelated reason (see the caller's error message). `None` means uv
    itself couldn't be run (binary missing, timeout) — callers should treat
    that as "can't tell" and warn, not error.

    Tries `--offline` first so the in-sync case never touches the network;
    only re-runs online (with more time budget) when the offline attempt
    itself fails, since that failure alone doesn't distinguish real drift
    from "needs the registry to tell".
    """
    offline = _run_uv_lock_check(
        ["uv", "lock", "--check", "--offline"], cwd, OFFLINE_TIMEOUT
    )
    if offline is None or offline[0] == 0:
        return offline
    return _run_uv_lock_check(["uv", "lock", "--check"], cwd, CHECK_TIMEOUT)


def main() -> int:
    result = CheckResult("check-uv-lock-sync")

    if is_self_repo():
        return result.report()

    if not Path(LOCK_FILE).exists():
        return result.report()

    if not _uses_uv():
        return result.report()

    check = _uv_lock_check()
    if check is None:
        result.warn(
            f"could not run `uv lock --check` (uv unavailable or timed out) "
            f"— skipping the {LOCK_FILE} sync check"
        )
        return result.report()

    returncode, output = check
    if returncode != 0:
        detail = f"\n{output}" if output else ""
        result.error(
            f"`uv lock --check` failed for {LOCK_FILE}.{detail}\n"
            "If this is lock drift, run `uv lock` and commit the updated "
            f"{LOCK_FILE} — `uv sync` just re-locks in place without "
            "failing, so a stale lock otherwise survives until an "
            "unrelated PR regenerates it and inherits the diff."
        )

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
