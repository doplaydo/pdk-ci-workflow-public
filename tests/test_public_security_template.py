import os
import subprocess
from pathlib import Path

import pytest
import yaml

from hooks._utils import apply_sync_markers

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "templates" / ".github" / "workflows" / "code-security.yml"
REUSABLE = REPO_ROOT / ".github" / "workflows" / "code-security.yml"


def test_public_security_projection_uses_public_scanners():
    """The public projection must not depend on internal reusable workflows."""
    projected = apply_sync_markers(TEMPLATE.read_text(), keep="public")
    workflow = yaml.safe_load(projected)

    security = workflow["jobs"]["security"]
    assert security["uses"].endswith(
        "pdk-ci-workflow-public/.github/workflows/code-security.yml@main"
    )
    assert security["permissions"] == {"contents": "read"}
    assert "doplaydo/infra" not in projected

    reusable = yaml.safe_load(REUSABLE.read_text())
    semgrep = reusable["jobs"]["semgrep"]
    assert "container" not in semgrep
    assert semgrep["env"]["SEMGREP_VERSION"]
    semgrep_run = next(
        s["run"] for s in semgrep["steps"] if "semgrep scan" in s.get("run", "")
    )
    assert "--config p/ci" in semgrep_run
    assert "--config p/secrets" in semgrep_run

    trivy = next(
        s
        for s in reusable["jobs"]["trivy"]["steps"]
        if s.get("uses", "").startswith("aquasecurity/trivy-action@")
    )
    assert trivy["with"]["scanners"] == "vuln,misconfig,secret"
    assert trivy["with"]["severity"] == "HIGH,CRITICAL"
    assert trivy["with"]["ignore-unfixed"] is True
    assert trivy["with"]["exit-code"] == "${{ inputs.fail-on-findings && '1' || '0' }}"

    triggers = reusable.get("on") or reusable[True]
    fail_input = triggers["workflow_call"]["inputs"]["fail-on-findings"]
    assert fail_input["default"] is True


@pytest.mark.parametrize(
    ("fail_on_findings", "expect_error"), [("true", True), ("false", False)]
)
def test_semgrep_error_flag_follows_fail_on_findings(
    tmp_path, fail_on_findings, expect_error
):
    """Semgrep gets --error only when fail-on-findings is true."""
    reusable = yaml.safe_load(REUSABLE.read_text())
    step = next(
        s
        for s in reusable["jobs"]["semgrep"]["steps"]
        if "semgrep scan" in s.get("run", "")
    )
    assert step["env"]["FAIL_ON_FINDINGS"] == "${{ inputs.fail-on-findings }}"

    args_file = tmp_path / "args"
    fake = tmp_path / "semgrep"
    fake.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {args_file}\n')
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAIL_ON_FINDINGS": fail_on_findings,
    }
    subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=True, cwd=tmp_path)

    args = args_file.read_text().splitlines()
    assert args[0] == "scan"
    assert ("--error" in args) is expect_error


