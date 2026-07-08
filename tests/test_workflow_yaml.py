"""Tests for workflow YAML validity and conventions.

Validates that all ``.yml`` files in ``.github/workflows/`` and
``templates/.github/workflows/`` are well-formed YAML, follow naming
conventions, and — for templates — act as thin callers into the centralised
``doplaydo/pdk-ci-workflow`` reusable workflows.
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
                assert isinstance(on["workflow_call"], (type(None), dict)), (
                    f"{path.name}: workflow_call value must be null or a mapping"
                )

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
        ``doplaydo/pdk-ci-workflow`` or ``doplaydo/infra``."""
        allowed_orgs = ("doplaydo/pdk-ci-workflow", "doplaydo/infra")
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


# ── pre-commit token-check gating (regression for #240) ────────────


class TestPreCommitTokenGate:
    """Regression test for #240.

    The `token-check` step in the `pre-commit` job must only skip
    pre-commit (green, warning) when the actor is Dependabot. For any
    other actor, a missing token must fail the job loudly instead of
    silently skipping — see doplaydo/pdk-ci-workflow#240.
    """

    def _token_check_step(self) -> dict[str, Any]:
        data = _load_yaml(WORKFLOWS_DIR / "test_code.yml")
        steps = data["jobs"]["pre-commit"]["steps"]
        for step in steps:
            if step.get("id") == "token-check":
                return step
        raise AssertionError(
            "no step with id 'token-check' found in the pre-commit job"
        )

    def test_token_check_distinguishes_dependabot_actor(self) -> None:
        run = self._token_check_step()["run"]
        assert "dependabot[bot]" in run, (
            "token-check must gate the skip path on the Dependabot "
            "actor, not on token-presence alone"
        )

    def test_token_check_fails_for_non_dependabot_missing_token(self) -> None:
        run = self._token_check_step()["run"]
        assert "exit 1" in run, (
            "token-check must fail (exit 1) when the token is missing "
            "for a non-Dependabot actor, instead of silently skipping"
        )

    def test_token_check_still_warns_on_dependabot_skip(self) -> None:
        run = self._token_check_step()["run"]
        assert "::warning::" in run

    def test_token_check_errors_on_non_dependabot_failure(self) -> None:
        run = self._token_check_step()["run"]
        assert "::error::" in run

    def test_token_check_uses_github_actor_env(self) -> None:
        env = self._token_check_step().get("env", {})
        assert env.get("ACTOR") == "${{ github.actor }}", (
            "token-check needs the actor available as $ACTOR to "
            "branch on it in the run script"
        )
