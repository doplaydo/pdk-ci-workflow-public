"""Pre-commit hook: enforce .github files match upstream templates.

Ruff-style auto-fix: on drift, rewrites local file from the canonical template
shipped inside this package, then exits 1. Second run sees no drift, exits 0.

Missing local files are created from the canonical template automatically.
Files to enforce are listed in TEMPLATES.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from hooks._utils import CheckResult, apply_sync_markers, is_self_repo

# Paths (relative to PDK repo root) that must match the upstream template of
# the same relative path under `templates/` in pdk-ci-workflow.
TEMPLATES: list[str] = [
    ".github/dependabot.yml",
    ".github/workflows/claude-pr-review.yml",
    ".github/workflows/drc.yml",
    ".github/workflows/issue.yml",
    ".github/workflows/model_coverage.yml",
    ".github/workflows/model_regression.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/test_code.yml",
    ".github/workflows/test_coverage.yml",
    ".github/workflows/update_badges.yml",
    ".github/workflows/code-security.yml",
    "AGENTS.md",
    "CLAUDE.md",
]


# Files previously shipped as templates that are now fetched at runtime by
# reusable workflows.  If they still exist in a PDK repo the hook deletes them
# so stale copies don't shadow the centrally-maintained version.
DEPRECATED_TEMPLATES: list[str] = [
    "build_cell.py",
    "sync_changelog.py",
    ".github/release-drafter.yml",
    ".github/workflows/release-drafter.yml",
    ".github/workflows/auto-label-pdk.yml",
]

# Files that must never be committed to a PDK repo (secrets, local env
# overrides). If found *and tracked by git*, the hook force-deletes them.
# Untracked/gitignored files with the same name on disk (e.g. a local .env
# a developer legitimately keeps) are left alone -- this is a safety net
# against committed secrets, not a substitute for a project's own
# .gitignore, and must never delete a file that was never committed.
FORBIDDEN_FILES: list[str] = [
    ".env",
    ".env.local",
]

# Templates enforced only when a matching directory glob exists in the PDK repo.
# List of (template_path, glob_pattern) pairs.
CONDITIONAL_TEMPLATES: list[tuple[str, str]] = [
    (".github/workflows/sample-projects.yml", "*--sample-projects"),
]


def _diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=2,
        )
    )


def _enforce_template(rel: str, root, result: CheckResult) -> None:
    parts = rel.split("/")
    src = root
    for p in parts:
        src = src.joinpath(p)
    if not src.is_file():
        result.error(f"no canonical template shipped for {rel}")
        return

    src_text = apply_sync_markers(src.read_text(encoding="utf-8"), keep="private")
    local = Path(rel)

    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(src_text, encoding="utf-8")
        result.error(f"created missing {rel} from upstream template")
        return

    local_text = local.read_text(encoding="utf-8")

    if src_text == local_text:
        return

    local.write_text(src_text, encoding="utf-8")
    print(_diff(local_text, src_text, rel))
    result.error(f"rewrote {rel} from upstream template")


def main() -> int:
    result = CheckResult("check-template-drift")

    if is_self_repo():
        return 0

    root = files("templates")

    for rel in TEMPLATES:
        _enforce_template(rel, root, result)

    for rel, glob_pattern in CONDITIONAL_TEMPLATES:
        if not any(Path(".").glob(glob_pattern)):
            continue
        _enforce_template(rel, root, result)

    for rel in DEPRECATED_TEMPLATES:
        local = Path(rel)
        if local.exists():
            local.unlink()
            result.error(f"deleted deprecated template {rel} (now fetched by CI)")

    for rel in FORBIDDEN_FILES:
        local = Path(rel)
        if not local.exists():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            capture_output=True,
            text=True,
        )
        if tracked.returncode != 0:
            continue  # exists on disk but never committed (e.g. gitignored local secret) -- leave it alone
        local.unlink()
        result.error(
            f"deleted forbidden file {rel} "
            "(committed secrets/env files must never be tracked)"
        )

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
