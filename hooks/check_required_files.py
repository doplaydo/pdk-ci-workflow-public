"""Pre-commit hook: verify required PDK repository files exist."""

from __future__ import annotations

import sys
from pathlib import Path

from hooks._utils import CheckResult

REQUIRED_FILES = [
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "Makefile",
    "pyproject.toml",
    ".gitignore",
    ".pre-commit-config.yaml",
]

REQUIRED_DIRS = [
    "tests",
]

# test_code.yml OR test.yml (quantum-rf-pdk uses test.yml)
TEST_WORKFLOW_CANDIDATES = [
    ".github/workflows/test_code.yml",
    ".github/workflows/test.yml",
]

# Sample-project ``samples/`` directories must be Python packages. A missing
# ``__init__.py`` is created (ruff-style auto-fix) and reported as an error, so
# the first run fails and the re-run passes. It carries a docstring so ruff's
# D104 (missing docstring in public package) does not flag it.
SAMPLES_INIT_GLOB = "*--sample-projects/*/*/samples"
SAMPLES_INIT_CONTENT = '"""Sample cells."""\n'

RECOMMENDED_FILES = [
    ".gitattributes",
    ".changelog.d/changelog_template.jinja",
]


def main() -> int:
    result = CheckResult("check-required-files")

    for f in REQUIRED_FILES:
        if not Path(f).exists():
            result.error(f"Required file missing: {f}")

    for d in REQUIRED_DIRS:
        if not Path(d).is_dir():
            result.error(f"Required directory missing: {d}/")

    # test_code.yml or test.yml — at least one must exist
    if not any(Path(c).exists() for c in TEST_WORKFLOW_CANDIDATES):
        result.error(
            "Required workflow missing: .github/workflows/test_code.yml (or test.yml)"
        )

    for samples in sorted(Path().glob(SAMPLES_INIT_GLOB)):
        init = samples / "__init__.py"
        if samples.is_dir() and not init.exists():
            init.write_text(SAMPLES_INIT_CONTENT, encoding="utf-8")
            result.error(f"Created missing {init}")

    for f in RECOMMENDED_FILES:
        if not Path(f).exists():
            result.warn(f"Recommended file missing: {f}")

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
