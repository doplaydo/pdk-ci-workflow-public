"""Tests for workflow YAML validity and conventions.

Validates that all ``.yml`` files in ``.github/workflows/`` and
``templates/.github/workflows/`` are well-formed YAML, follow naming
conventions, and — for templates — act as thin callers into the centralised
``doplaydo/pdk-ci-workflow`` reusable workflows.
"""

from __future__ import annotations

import subprocess
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




def test_badge_push_fetch_is_guarded_against_missing_remote_branch() -> None:
    """The badge-push retry loop's ``git fetch origin badges`` must not
    abort the whole step under GitHub Actions' default errexit shell when
    the ``badges`` branch doesn't exist yet on the remote (first-ever run
    for a repo) -- see #249.

    ``git fetch origin badges`` exits 128 if the remote ref is missing.
    Only ``2>/dev/null`` (stderr) was ever redirected -- the exit code was
    not -- so under ``set -e`` the script aborted on that line before the
    next line's ``git rev-parse --verify origin/badges`` fallback check
    (which already correctly distinguishes "branch exists" from "branch
    missing") ever ran. ``|| true`` on the fetch is the fix; the fallback
    logic below it needs no change.
    """
    affected_files = (
        WORKFLOWS_DIR / "drc.yml",
        WORKFLOWS_DIR / "update_badges.yml",
    )
    for path in affected_files:
        text = path.read_text()
        assert "git fetch origin badges 2>/dev/null || true" in text, (
            f"{path.name} is missing the `|| true` guard on "
            "`git fetch origin badges` -- errexit will abort the badge-push "
            "step on a repo's first-ever run, before the orphan-branch "
            "fallback ever executes (see #249)"
        )


def test_badge_push_fetch_guard_survives_errexit(tmp_path: Path) -> None:
    """Execute the actual guarded fetch line under GitHub Actions' default
    errexit shell (`bash -eo pipefail`) against a scratch remote with no
    `badges` ref, proving the fix survives real bash semantics -- not just
    a substring match in the YAML text (see #249 review discussion)."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=work, check=True
    )
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=work, check=True)

    snippet = """
git fetch origin badges 2>/dev/null || true
echo "reached rev-parse check"
if git rev-parse --verify origin/badges >/dev/null 2>&1; then
  echo "branch exists"
else
  echo "branch missing -- would create orphan"
fi
"""
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", snippet],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "reached rev-parse check" in result.stdout
    assert "branch missing" in result.stdout
