"""Tests for workflow YAML validity and conventions.

Validates that all ``.yml`` files in ``.github/workflows/`` and
``templates/.github/workflows/`` are well-formed YAML, follow naming
conventions, and — for templates — act as thin callers into the centralised
``doplaydo/pdk-ci-workflow-public`` reusable workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Repo root is two levels up from tests/
REPO_ROOT = Path(__file__).resolve().parent.parent

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TEMPLATES_DIR = REPO_ROOT / "templates" / ".github" / "workflows"


def _load_yaml(path: Path) -> Any:
    """Load a YAML file and return its parsed content."""
    with open(path) as f:
        return yaml.safe_load(f)


def _all_workflow_files(directory: Path) -> list[Path]:
    """Return all .yml files in *directory*, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.yml"))


# ── Centralised workflows (.github/workflows/) ─────────────────────


class TestCentralWorkflowYaml:
    """Validate YAML files in .github/workflows/."""

    def test_workflows_dir_exists(self) -> None:
        assert WORKFLOWS_DIR.is_dir(), f"{WORKFLOWS_DIR} does not exist"

    def test_all_files_are_valid_yaml(self) -> None:
        for path in _all_workflow_files(WORKFLOWS_DIR):
            data = _load_yaml(path)
            assert data is not None, f"{path.name} parsed as empty/null"

    def test_reusable_workflows_have_workflow_call(self) -> None:
        """Reusable workflows (called by templates) must trigger on
        ``workflow_call``."""
        for path in _all_workflow_files(WORKFLOWS_DIR):
            data = _load_yaml(path)
            if data is None:
                continue
            on = data.get("on") or data.get(True)  # YAML parses `on:` as True
            if on is None:
                continue
            # If the workflow has workflow_call, verify it's valid
            if isinstance(on, dict) and "workflow_call" in on:
                # workflow_call can be None or a dict — both are acceptable
                assert isinstance(
                    on["workflow_call"], (type(None), dict)
                ), f"{path.name}: workflow_call value must be null or a mapping"

    def test_every_workflow_has_name(self) -> None:
        for path in _all_workflow_files(WORKFLOWS_DIR):
            if path.name == "README.md":
                continue
            data = _load_yaml(path)
            assert data is not None, f"{path.name} parsed as empty"
            assert "name" in data, f"{path.name} is missing a 'name' field"

    def test_every_workflow_has_on_trigger(self) -> None:
        for path in _all_workflow_files(WORKFLOWS_DIR):
            data = _load_yaml(path)
            if data is None:
                continue
            on = data.get("on") or data.get(True)
            assert on is not None, f"{path.name} is missing an 'on' trigger"


# ── Template workflows (templates/.github/workflows/) ──────────────


class TestTemplateWorkflowYaml:
    """Validate YAML files in templates/.github/workflows/."""

    def test_templates_dir_exists(self) -> None:
        assert TEMPLATES_DIR.is_dir(), f"{TEMPLATES_DIR} does not exist"

    def test_all_templates_are_valid_yaml(self) -> None:
        for path in _all_workflow_files(TEMPLATES_DIR):
            data = _load_yaml(path)
            assert data is not None, f"{path.name} parsed as empty/null"

    def test_every_template_has_name(self) -> None:
        for path in _all_workflow_files(TEMPLATES_DIR):
            data = _load_yaml(path)
            assert data is not None, f"{path.name} parsed as empty"
            assert "name" in data, f"{path.name} is missing a 'name' field"

    def test_templates_use_uses_directive(self) -> None:
        """Template workflows should be thin callers that delegate via
        ``uses:`` in their job definitions."""
        for path in _all_workflow_files(TEMPLATES_DIR):
            data = _load_yaml(path)
            if data is None or "jobs" not in data:
                continue
            jobs = data["jobs"]
            has_uses = any(
                "uses" in job for job in jobs.values() if isinstance(job, dict)
            )
            assert has_uses, (
                f"{path.name} has no job with 'uses:' — templates should be "
                f"thin callers into centralised reusable workflows"
            )

    def test_templates_reference_pdk_ci_workflow(self) -> None:
        """Template ``uses:`` values should reference
        ``doplaydo/pdk-ci-workflow-public`` or ``doplaydo/infra``."""
        allowed_orgs = ("doplaydo/pdk-ci-workflow-public", "doplaydo/infra")
        for path in _all_workflow_files(TEMPLATES_DIR):
            data = _load_yaml(path)
            if data is None or "jobs" not in data:
                continue
            for job_name, job in data["jobs"].items():
                if not isinstance(job, dict):
                    continue
                uses = job.get("uses", "")
                if not uses:
                    continue
                assert any(uses.startswith(org) for org in allowed_orgs), (
                    f"{path.name} job '{job_name}' uses '{uses}' which does "
                    f"not reference {allowed_orgs}"
                )
