"""Tests for the ``.pre-commit-hooks.yaml`` definition file.

Validates structure, required fields, and consistency between declared hook
entry points and the actual scripts in ``hooks/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_YAML = REPO_ROOT / ".pre-commit-hooks.yaml"
HOOKS_DIR = REPO_ROOT / "hooks"


def _load_hooks() -> list[dict[str, Any]]:
    """Load and return the hooks list from .pre-commit-hooks.yaml."""
    with open(HOOKS_YAML) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, list), ".pre-commit-hooks.yaml must be a YAML list"
    return data


# IDs of the PDK-specific hooks (not third-party wrappers).
# Third-party wrappers (trailing-whitespace, ruff-lint, etc.) have entry points
# provided by their additional_dependencies, not by scripts in hooks/.
_PDK_HOOK_IDS = {
    "check-required-files",
    "check-pyproject-sections",
    "check-package-init",
    "check-cells-structure",
    "check-tech-structure",
    "check-pdk-object",
    "check-test-structure",
    "check-makefile-targets",
    "check-workflows",
    "check-multi-band",
    "check-no-raw-layers",
    "check-no-main-in-cells",
    "check-precommit-config",
    "check-version-sync",
    "check-template-drift",
    "check-uv-lock-tracked",
}


class TestPrecommitHooksYaml:
    """Structural tests for .pre-commit-hooks.yaml."""

    def test_file_exists(self) -> None:
        assert HOOKS_YAML.is_file(), ".pre-commit-hooks.yaml not found"

    def test_is_valid_yaml(self) -> None:
        hooks = _load_hooks()
        assert len(hooks) > 0, "Hooks list is empty"

    def test_each_hook_has_required_fields(self) -> None:
        required = {"id", "name", "entry", "language"}
        for hook in _load_hooks():
            hook_id = hook.get("id", "<unknown>")
            missing = required - set(hook.keys())
            assert not missing, (
                f"Hook '{hook_id}' is missing required fields: {missing}"
            )

    def test_pdk_hooks_have_always_run(self) -> None:
        """PDK validation hooks must have ``always_run: true``."""
        for hook in _load_hooks():
            if hook["id"] in _PDK_HOOK_IDS:
                assert hook.get("always_run") is True, (
                    f"Hook '{hook['id']}' should have always_run: true"
                )

    def test_pdk_hooks_have_pass_filenames_false(self) -> None:
        """PDK validation hooks must have ``pass_filenames: false``."""
        for hook in _load_hooks():
            if hook["id"] in _PDK_HOOK_IDS:
                assert hook.get("pass_filenames") is False, (
                    f"Hook '{hook['id']}' should have pass_filenames: false"
                )

    def test_pdk_hook_entry_matches_script(self) -> None:
        """Each PDK hook entry point should correspond to a script in hooks/.

        The entry point name uses underscores (e.g. ``check_cells_structure``),
        and the script file is ``hooks/check_cells_structure.py``.
        """
        for hook in _load_hooks():
            if hook["id"] not in _PDK_HOOK_IDS:
                continue
            entry = hook["entry"]
            # Entry might be just the command name (e.g. "check_cells_structure")
            script_name = entry.split()[0]  # strip any args
            script_path = HOOKS_DIR / f"{script_name}.py"
            assert script_path.is_file(), (
                f"Hook '{hook['id']}' entry '{entry}' has no matching script "
                f"at {script_path}"
            )

    def test_all_hooks_have_description(self) -> None:
        """Every hook should have a description field."""
        for hook in _load_hooks():
            assert "description" in hook, (
                f"Hook '{hook['id']}' is missing a 'description' field"
            )

    def test_hook_ids_are_unique(self) -> None:
        hooks = _load_hooks()
        ids = [h["id"] for h in hooks]
        duplicates = [x for x in ids if ids.count(x) > 1]
        assert not duplicates, f"Duplicate hook IDs: {set(duplicates)}"

    def test_all_pdk_scripts_have_hooks(self) -> None:
        """Every ``check_*.py`` script in hooks/ should have a corresponding
        entry in .pre-commit-hooks.yaml."""
        hooks = _load_hooks()
        hook_entries = {h["entry"].split()[0] for h in hooks}
        for script in sorted(HOOKS_DIR.glob("check_*.py")):
            script_stem = script.stem
            assert script_stem in hook_entries, (
                f"Script hooks/{script.name} has no matching hook entry in "
                f".pre-commit-hooks.yaml"
            )

    def test_all_hooks_have_language(self) -> None:
        """Every hook must declare a language."""
        for hook in _load_hooks():
            assert hook.get("language"), (
                f"Hook '{hook['id']}' has no 'language' field"
            )
