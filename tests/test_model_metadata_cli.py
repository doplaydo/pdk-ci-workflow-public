"""Exercise CLI isolation, setup reporting, and shared workflow integration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from hooks._utils import apply_sync_markers

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_model_metadata.py"
WORKFLOW = ROOT / ".github" / "workflows" / "model_regression.yml"
ACTION = ROOT / "actions" / "check_model_metadata" / "action.yml"
CALLER = ROOT / "templates" / ".github" / "workflows" / "model_regression.yml"


def run_checker(tmp_path, *arguments, enforce=False):
    environment = dict(
        os.environ,
        PYTHONPATH=str(tmp_path),
        PDK_MODEL_METADATA_ENFORCE=str(enforce).lower(),
    )
    environment["GITHUB_STEP_SUMMARY"] = str(tmp_path / "summary.md")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def package(tmp_path, name, source):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "__init__.py").write_text(source)


def test_all_configured_variants_are_isolated_and_deduplicated(tmp_path):
    package(
        tmp_path,
        "first",
        "from types import SimpleNamespace\nimport sys\nsys.modules['variant_sentinel'] = object()\nPDK = SimpleNamespace(cells={}, models={}, activate=lambda: None)\n",
    )
    package(
        tmp_path,
        "second",
        "from types import SimpleNamespace\nimport sys\nassert 'variant_sentinel' not in sys.modules\nPDK = SimpleNamespace(cells={}, models={}, activate=lambda: None)\n",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.gdsfactoryplus.pdk]\nname = "first"\noptions = ["first", "second"]\n'
    )
    result = run_checker(tmp_path, "--json", "report.json")
    assert result.returncode == 0, result.stdout + result.stderr
    reports = json.loads((tmp_path / "report.json").read_text())["pdks"]
    assert [report["pdk"] for report in reports] == ["first", "second"]
    assert all(report["status"] == "not-applicable" for report in reports)
    assert (
        "No SAX/Circulax metadata bindings were validated"
        in (tmp_path / "summary.md").read_text()
    )


@pytest.mark.parametrize("enforce,level", [(False, "warning"), (True, "error")])
def test_setup_failures_have_explicit_reports_and_annotations(tmp_path, enforce, level):
    package(tmp_path, "broken", "raise RuntimeError('provider import failed')\n")
    result = run_checker(
        tmp_path, "--pdk", "broken", "--json", "report.json", enforce=enforce
    )
    assert result.returncode == 2
    assert f"::{level} title=PDK model metadata::" in result.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "setup-failed"
    assert "provider import failed" in report["detail"]


def test_one_bad_variant_does_not_prevent_later_variants(tmp_path):
    package(tmp_path, "broken", "raise RuntimeError('broken')\n")
    package(
        tmp_path,
        "clean",
        "from types import SimpleNamespace\nPDK = SimpleNamespace(cells={}, models={}, activate=lambda: None)\n",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.gdsfactoryplus.pdk]\nname = "broken"\noptions = ["clean"]\n'
    )
    result = run_checker(tmp_path, "--json", "report.json")
    assert result.returncode == 2
    reports = json.loads((tmp_path / "report.json").read_text())["pdks"]
    assert [report["status"] for report in reports] == [
        "setup-failed",
        "not-applicable",
    ]


def test_invalid_configuration_reports_setup_failure(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.gdsfactoryplus.pdk]\nname = "first"\noptions = "not-a-list"\n'
    )
    result = run_checker(tmp_path, "--json", "report.json")
    assert result.returncode == 2
    assert (
        json.loads((tmp_path / "report.json").read_text())["status"] == "setup-failed"
    )


@pytest.mark.parametrize("enforce,level", [(False, "warning"), (True, "error")])
def test_legacy_only_assignment_fails_in_both_reporting_modes(tmp_path, enforce, level):
    package(
        tmp_path,
        "legacy",
        "from types import SimpleNamespace\n"
        "PDK = SimpleNamespace(cells={'cell': lambda: None}, "
        "models={'cell': lambda: {}}, activate=lambda: None)\n",
    )
    result = run_checker(
        tmp_path, "--pdk", "legacy", "--json", "report.json", enforce=enforce
    )
    assert result.returncode == 1
    assert f"::{level} title=PDK model metadata::" in result.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["issues"][0]["code"] == "missing-metadata"


def test_bindings_only_mode_does_not_claim_legacy_assignments_are_absent(tmp_path):
    package(
        tmp_path,
        "legacy",
        "from types import SimpleNamespace\n"
        "PDK = SimpleNamespace(cells={'cell': lambda: None}, "
        "models={'cell': lambda: {}}, activate=lambda: None)\n",
    )
    result = run_checker(
        tmp_path, "--pdk", "legacy", "--bindings-only", "--json", "report.json"
    )
    assert result.returncode == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "not-applicable"
    assert report["registry_modeled_cells"] == 1
    assert report["passed_bindings"] == 0
    assert "Legacy assignments, if any, are counted above" in result.stdout


def test_action_preserves_installed_pdk_environment_and_exports_report():
    action = yaml.safe_load(ACTION.read_text())
    (step,) = action["runs"]["steps"]
    assert "uv run --no-sync python" in step["run"]
    assert "--json build/model-metadata.json" in step["run"]
    assert step["env"]["PDK_MODEL_METADATA_ENFORCE"] == "${{ inputs.enforce }}"
    assert action["inputs"]["enforce"]["default"] == "false"
    assert step["run"].endswith('|| [ "$PDK_MODEL_METADATA_ENFORCE" != "true" ]')


def test_reusable_workflow_defaults_to_audit_mode_and_uploads_reports():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    trigger = workflow.get("on") or workflow[True]
    assert (
        trigger["workflow_call"]["inputs"]["enforce-model-metadata"]["default"] is False
    )
    steps = workflow["jobs"]["model-regression"]["steps"]
    check = next(step for step in steps if step.get("name") == "Check model metadata")
    expected_action = "doplaydo/pdk-ci-workflow-public/actions/check_model_metadata@main"
    assert check["uses"] == expected_action
    # Expression continue-on-error is ignored on composite steps; the action
    # itself exits zero in audit mode instead.
    assert "continue-on-error" not in check
    assert check["with"]["enforce"] == "${{ inputs.enforce-model-metadata }}"
    upload = next(
        step for step in steps if step.get("name") == "Upload model metadata report"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "build/model-metadata.json"


def test_public_workflow_uses_public_action():
    workflow = yaml.safe_load(apply_sync_markers(WORKFLOW.read_text(), keep="public"))
    steps = workflow["jobs"]["model-regression"]["steps"]
    check = next(step for step in steps if step.get("name") == "Check model metadata")
    assert (
        check["uses"]
        == "doplaydo/pdk-ci-workflow-public/actions/check_model_metadata@main"
    )


@pytest.mark.parametrize("view", ["private", "public"])
def test_canonical_caller_preserves_repository_enforcement_option(view):
    workflow = yaml.safe_load(apply_sync_markers(CALLER.read_text(), keep=view))
    job = workflow["jobs"]["model-regression"]
    assert job["with"]["enforce-model-metadata"] == (
        "${{ vars.ENFORCE_MODEL_METADATA == 'true' }}"
    )
    assert ("pdk-ci-workflow-public/" in job["uses"]) == (view == "public")
