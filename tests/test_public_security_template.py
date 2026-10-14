from pathlib import Path

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
    semgrep_run = next(s["run"] for s in semgrep["steps"] if "semgrep scan" in s.get("run", ""))
    assert "--config p/ci" in semgrep_run
    assert "--config p/secrets" in semgrep_run
    assert "--error" not in semgrep_run

    trivy = next(
        s for s in reusable["jobs"]["trivy"]["steps"]
        if s.get("uses", "").startswith("aquasecurity/trivy-action@")
    )
    assert trivy["with"]["scanners"] == "vuln,misconfig,secret"
    assert trivy["with"]["exit-code"] == "0"


