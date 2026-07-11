"""Pre-commit hook: validate Makefile has required targets."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from hooks._utils import CheckResult

REQUIRED_TARGETS = {"install", "test"}
RECOMMENDED_TARGETS = {"test-force", "docs", "build", "update-pre", "dev"}


def _get_target_bodies(content: str) -> dict[str, str]:
    """Parse Makefile and return target_name -> body text for each target."""
    targets: dict[str, str] = {}
    current_target: str | None = None
    body_lines: list[str] = []

    for line in content.split("\n"):
        # New target definition
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:", line)
        if m:
            if current_target is not None:
                targets[current_target] = "\n".join(body_lines)
            current_target = m.group(1)
            body_lines = []
        elif current_target is not None:
            body_lines.append(line)

    if current_target is not None:
        targets[current_target] = "\n".join(body_lines)

    return targets


def main() -> int:
    result = CheckResult("check-makefile-targets")

    makefile = Path("Makefile")
    if not makefile.exists():
        result.error("Makefile not found")
        return result.report()

    original_content = makefile.read_text()
    content = original_content
    targets = _get_target_bodies(content)
    target_names = set(targets.keys())

    for t in REQUIRED_TARGETS:
        if t not in target_names:
            result.error(f"Makefile missing required target: {t}")

    for t in RECOMMENDED_TARGETS:
        if t not in target_names:
            result.warn(f"Makefile missing recommended target: {t}")

    # Content checks for existing targets
    if "install" in targets:
        body = targets["install"]
        if "uv" not in body and "pip" in body:
            result.warn(
                "Makefile 'install' target uses pip — consider migrating to 'uv sync'"
            )

    if "test" in targets:
        body = targets["test"]
        if "pytest" not in body:
            result.warn("Makefile 'test' target does not appear to run pytest")

    if "build" in targets:
        body = targets["build"]
        if "setup.py" in body:
            result.warn(
                "Makefile 'build' target uses setup.py — "
                "consider migrating to 'uv build' or 'python -m build'"
            )

    if "dev" in targets:
        body = targets["dev"]

        curl_line = re.compile(
            r"^\t[^\n]*curl[^\n]*pdk-ci-workflow[^\n]*pre-commit-config\.yaml[^\n]*$",
            re.MULTILINE,
        )
        curl_match = curl_line.search(body)
        if curl_match:
            # pdk-ci-workflow-public is public — plain curl needs no auth
            replacement_line = (
                "\tcurl -sf"
                " https://raw.githubusercontent.com/doplaydo/pdk-ci-workflow-public/main/templates/.pre-commit-config.yaml"
                " -o .pre-commit-config.yaml"
            )
            if curl_match.group(0) != replacement_line:
                content = curl_line.sub(replacement_line, content)
                result.error(
                    "rewrote Makefile dev target: normalized pre-commit config fetch command"
                )

        install_line = re.compile(
            r"^\t[^\n]*\bpre-commit install\b[^\n]*$", re.MULTILINE
        )
        clean_line = re.compile(r"^\t[^\n]*\bpre-commit clean\b[^\n]*$", re.MULTILINE)
        install_match = install_line.search(body)
        if install_match and not clean_line.search(body):
            insertion = f"\tuv run pre-commit clean\n{install_match.group(0)}"
            new_body = body.replace(install_match.group(0), insertion, 1)
            content = content.replace(body, new_body, 1)
            result.error(
                "rewrote Makefile dev target: added `pre-commit clean` before "
                "`pre-commit install` — a stale local hook-repo cache otherwise "
                "hard-fails every commit once a new hook is added upstream (#244)"
            )

        if content != original_content:
            makefile.write_text(content)

    return result.report()


if __name__ == "__main__":
    sys.exit(main())
