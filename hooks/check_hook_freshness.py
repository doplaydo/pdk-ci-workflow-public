"""Pre-commit hook: detect a stale installed pre-commit hook checkout.

`.pre-commit-config.yaml` pins `rev: main` — a mutable ref. pre-commit
resolves it once, clones the hook repo, and caches that clone under
`~/.cache/pre-commit` forever. If `main` moves on, every hook running from
that cache (including this one, and check-template-drift) keeps executing
old code silently until a developer manually runs `pre-commit clean`.

This hook detects that drift: it locates the `.git` checkout pre-commit
leaves alongside the installed venv, and compares the commit it was cloned
from against the current tip of `origin/main`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hooks._utils import CheckResult, is_self_repo


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* to find the pre-commit-cloned hook repo (has .git)."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git(*args: str, cwd: Path, timeout: float = 5.0) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.strip()


def main() -> int:
    result = CheckResult("check-hook-freshness")

    if is_self_repo():
        return result.report()

    repo_root = _find_repo_root(Path(__file__).resolve())
    if repo_root is None:
        result.warn(
            "could not locate the installed hook repo's .git checkout — "
            "skipping freshness check"
        )
        return result.report()

    installed_sha = _git("rev-parse", "HEAD", cwd=repo_root)
    remote_output = _git("ls-remote", "origin", "main", cwd=repo_root, timeout=10.0)
    if not installed_sha or not remote_output:
        result.warn(
            "could not determine installed vs. latest hook commit "
            "(offline, or git unavailable) — skipping freshness check"
        )
        return result.report()

    remote_sha = remote_output.split()[0]
    if installed_sha != remote_sha:
        result.error(
            "installed pre-commit hooks are stale "
            f"(cached at {installed_sha[:7]}, latest main is {remote_sha[:7]}). "
            "Run `pre-commit clean` and re-run to pick up current hooks."
        )

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
