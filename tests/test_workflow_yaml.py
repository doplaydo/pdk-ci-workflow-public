"""Tests for workflow YAML validity and conventions.

Validates that all ``.yml`` files in ``.github/workflows/`` and
``templates/.github/workflows/`` are well-formed YAML, follow naming
conventions, and — for templates — act as thin callers into the centralised
reusable workflows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from hooks._utils import apply_sync_markers

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
        """Template ``uses:`` values should reference the central CI
        workflows repo or ``doplaydo/infra``."""
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


# ── Marker resolution ──────────────────────────────────────────────


@pytest.mark.parametrize("keep", ["private", "public"])
@pytest.mark.parametrize(
    "path",
    _all_workflow_files(WORKFLOWS_DIR) + _all_workflow_files(TEMPLATES_DIR),
    ids=lambda p: f"{p.parent.parent.parent.name}-{p.name}",
)
def test_workflows_stay_valid_yaml_after_marker_resolution(
    path: Path, keep: str
) -> None:
    """A workflow that parses in this repo can still be broken for the other
    audience: wrapping only the *entries* under a mapping key (``secrets:``,
    ``with:``) in SYNC-PRIVATE leaves that key dangling once the region is
    dropped. Wrap the key itself in that case.
    """
    resolved = apply_sync_markers(path.read_text(), keep=keep)
    # Only the marker syntax must be gone; prose that merely names the markers
    # (as release.yml does in a comment) is fine.
    markers = (
        "SYNC-PRIVATE:begin",
        "SYNC-PRIVATE:end",
        "SYNC-PUBLIC:begin",
        "SYNC-PUBLIC:end",
    )
    leftover = [marker for marker in markers if marker in resolved]
    assert not leftover, (
        f"{path.name}: {leftover} survived resolution for the {keep} audience"
    )
    try:
        data = yaml.safe_load(resolved)
    except yaml.YAMLError as error:  # pragma: no cover - failure path
        raise AssertionError(
            f"{path.name} is not valid YAML for the {keep} audience: {error}"
        ) from error
    assert data is not None, (
        f"{path.name} resolved to an empty document for the {keep} audience"
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
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=work, check=True
    )
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
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "reached rev-parse check" in result.stdout
    assert "branch missing" in result.stdout


def test_editable_caller_job_matches_release_gate() -> None:
    data = _load_yaml(TEMPLATES_DIR / "sample-projects-editable.yml")
    job = data["jobs"]["test-sample-projects"]
    assert "name" not in job
    assert job["with"]["pdk_source"] == "editable"
    assert data["name"] == "Sample projects · editable PDK"


def test_released_caller_does_not_match_release_gate() -> None:
    data = _load_yaml(TEMPLATES_DIR / "sample-projects-released.yml")
    assert list(data["jobs"]) == ["released"]
    assert data["jobs"]["released"]["with"]["pdk_source"] == "released"
    assert data["name"] == "Sample projects · released PDK"


def test_public_sample_projects_has_no_upload_job() -> None:
    text = apply_sync_markers(
        (WORKFLOWS_DIR / "sample-projects.yml").read_text(), keep="public"
    )
    data = yaml.safe_load(text)
    assert "upload" not in data["jobs"]
    assert "upload" not in data[True]["workflow_call"]["inputs"]
    assert "upload_dry_run" not in data[True]["workflow_call"]["inputs"]
    assert "PDK_CI_WORKFLOW_TOKEN" not in text




def test_shared_suite_collects_checks_before_passed_artifact() -> None:
    data = _load_yaml(WORKFLOWS_DIR / "sample-project-test.yml")
    job = data["jobs"]["test"]
    assert job["if"] == "inputs.projects != '[]'"
    assert job["strategy"]["fail-fast"] is False
    assert job["name"] == "Test ${{ matrix.project.project_dir }}"
    steps = job["steps"]
    result = next(step for step in steps if step.get("id") == "result")
    passed = next(step for step in steps if step.get("id") == "passed")
    assert steps.index(result) < steps.index(passed)
    assert "steps.result.outputs.ok == 'true'" in passed["if"]
    assert passed["with"]["retention-days"] == 7
    assert 'sample_project_ci.py" summary' in steps[-1]["run"]
    for step in steps[: steps.index(result)]:
        if "id" in step:
            assert step.get("continue-on-error") is True, step
    assert "--no-sources" not in (WORKFLOWS_DIR / "sample-project-test.yml").read_text()


@pytest.mark.parametrize("pdk_source", ["editable", "released"])
@pytest.mark.parametrize("fixture", ["true", "false"])
def test_sample_project_suite_result_gate_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdk_source: str, fixture: str
) -> None:
    """Run the actual collector/final shell; every required failure closes the gate."""
    import json
    import os
    import shutil

    steps = _load_yaml(WORKFLOWS_DIR / "sample-project-test.yml")["jobs"]["test"][
        "steps"
    ]
    collector = next(step for step in steps if step.get("id") == "result")
    final = steps[-1]
    shutil.copy(REPO_ROOT / "scripts/sample_project_ci.py", tmp_path)
    (tmp_path / "tested").mkdir()
    (tmp_path / "bin").mkdir()
    uv = tmp_path / "bin/uv"
    uv.write_text(
        '#!/bin/bash\nshift 4\nexec "' + shutil.which("python3") + '" "${@:2}"\n'
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("PDK_SOURCE", pdk_source)
    monkeypatch.setenv("PROJECT_DIR", "examplepdk--sample-projects/demo")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    outcomes = {
        step["id"]: {"outcome": "success"}
        for step in steps[: steps.index(collector)]
        if "id" in step
    }
    outcomes["drc-fixture"]["outputs"] = {"present": fixture}
    if pdk_source == "released":
        for name in ("checkout", "editable"):
            outcomes[name]["outcome"] = "skipped"
    if fixture == "false":
        for name in ("build-cell", "drc", "drc-verify", "drc-report"):
            outcomes[name]["outcome"] = "skipped"
    (tmp_path / "test-all.log").write_text("  SKIPPED cell_requires_args  \n")
    (tmp_path / "notebooks.json").write_text(
        json.dumps(
            {
                "run": ["demo.ipynb"],
                "excluded": ["hfss_demo.ipynb"],
                "failed": [],
                "skip_execution": {"demo.ipynb": 2},
            }
        )
    )

    collector_stdout = []

    def collect() -> dict:
        monkeypatch.setenv("STEPS", json.dumps(outcomes))
        result = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", collector["run"]],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        collector_stdout.append(result.stdout)
        return json.loads((tmp_path / "tested/summary.json").read_text())

    report = collect()
    assert (tmp_path / "output").read_text().endswith("ok=true\n")
    assert "SKIPPED cell_requires_args" in report["exclusions"]
    assert ("DRC: no sample_drc_errors.py fixture" in report["exclusions"]) == (
        fixture == "false"
    )
    drc_checks = {"drc-fixture", "redact"}
    if fixture == "true":
        drc_checks.update({"build-cell", "drc", "drc-verify", "drc-report"})
    assert drc_checks <= report["checks"].keys()
    passed = next(step for step in steps if step.get("id") == "passed")
    assert "steps.result.outputs.ok == 'true'" in passed["if"]
    for name in sorted(drc_checks):
        for outcome in ("failure", "skipped", "missing"):
            if outcome == "missing":
                saved = outcomes.pop(name)
            else:
                outcomes[name]["outcome"] = outcome
            failed_report = collect()
            # This is the same output that enables passed-test artifact publication.
            assert (tmp_path / "output").read_text().endswith("ok=true\n"), name
            label = "failed" if outcome == "failure" else outcome
            assert failed_report["checks"][name] == f"{label} (not blocking)"
            warning = collector_stdout[-1]
            assert "::warning::examplepdk--sample-projects/demo" in warning
            assert f"DRC step {name}: {label} (not blocking)" in warning
            monkeypatch.setenv("OK", "true")
            monkeypatch.setenv("PUBLISHED", "success")
            result = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", final["run"]],
                cwd=tmp_path,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert (
                f"| {name} | {label} (not blocking) |"
                in (tmp_path / "summary").read_text()
            )
            if outcome == "missing":
                outcomes[name] = saved
            outcomes[name]["outcome"] = "success"
    for name in report["checks"]:
        if name in drc_checks:
            continue
        for failure in ("failure", "skipped"):
            outcomes[name]["outcome"] = failure
            collect()
            assert (tmp_path / "output").read_text().endswith("ok=false\n"), name
        outcomes[name]["outcome"] = "success"
        missing = outcomes.pop(name)
        collect()
        assert (tmp_path / "output").read_text().endswith("ok=false\n"), name
        outcomes[name] = missing
    collect()
    for ok, published, expected in [
        ("true", "success", 0),
        ("false", "skipped", 1),
        ("true", "failure", 1),
    ]:
        monkeypatch.setenv("OK", ok)
        monkeypatch.setenv("PUBLISHED", published)
        result = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", final["run"]], cwd=tmp_path, check=False
        )
        assert result.returncode == expected
    summary = (tmp_path / "summary").read_text()
    assert "hfss_demo.ipynb" in summary
    assert "demo.ipynb: 2 skip-execution cells" in summary
    assert "SKIPPED cell_requires_args" in summary


SAMPLE_PROJECT_AUDIENCES = ["public"]


@pytest.mark.parametrize("keep", SAMPLE_PROJECT_AUDIENCES)
def test_sample_project_drc_steps_run_in_both_modes(keep: str) -> None:
    text = apply_sync_markers(
        (WORKFLOWS_DIR / "sample-project-test.yml").read_text(), keep=keep
    )
    steps = {
        step["id"]: step
        for step in yaml.safe_load(text)["jobs"]["test"]["steps"]
        if "id" in step
    }
    for name in (
        "drc-fixture",
        "build-cell",
        "drc",
        "drc-verify",
        "redact",
        "drc-report",
    ):
        assert "pdk_source" not in steps[name].get("if", "")
        assert "PDK_SOURCE" not in steps[name].get("if", "")
        assert steps[name]["continue-on-error"] is True


def test_sample_project_suite_install_modes_and_runtime_pins() -> None:
    text = (WORKFLOWS_DIR / "sample-project-test.yml").read_text()
    data = yaml.safe_load(text)
    steps = {step["id"]: step for step in data["jobs"]["test"]["steps"] if "id" in step}
    assert steps["checkout"]["with"]["ref"] == "${{ github.sha }}"
    assert steps["checkout"]["if"] == "inputs.pdk_source == 'editable'"
    assert steps["editable"]["if"] == "inputs.pdk_source == 'editable'"
    assert "meta['pdk_distribution']}={checkout}" in steps["editable"]["run"]
    assert "uv sync --no-dev" in steps["install-minimal"]["run"]
    assert "uv sync --all-extras --all-groups" in steps["install-all"]["run"]
    assert steps["test-minimal"]["run"].startswith("uv run --no-sync gfp test -v")
    assert steps["test-all"]["run"].startswith("uv run --no-sync gfp test -v")
    assert '--out "$RUNNER_TEMP/notebooks.json"' in steps["notebooks"]["run"]
    for keep in SAMPLE_PROJECT_AUDIENCES:
        resolved = apply_sync_markers(text, keep=keep)
        assert "$SCRIPT_REF" in resolved
        if keep == "private":
            assert "?ref=$SCRIPT_REF" in resolved
        else:
            assert "PDK_CI_WORKFLOW_TOKEN" not in resolved
            assert "/$SCRIPT_REF/scripts/$script" in resolved


def test_sample_project_suite_prepares_variant_and_replaces_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import os
    import shutil

    import tomllib

    steps = _load_yaml(WORKFLOWS_DIR / "sample-project-test.yml")["jobs"]["test"][
        "steps"
    ]
    editable = next(step for step in steps if step.get("id") == "editable")
    checkout = tmp_path / "pdk"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "examplepdk"\n')
    (checkout / "Makefile").write_text(
        "prepare-cband:\n\tprintf '[project]\\nname = \"examplepdk-cband\"\\n' > pyproject.toml\n"
    )
    (tmp_path / "package").mkdir()
    (tmp_path / "package/meta.json").write_text(
        json.dumps({"pdk_distribution": "examplepdk-cband"})
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n[tool.uv.sources]\n'
        'examplepdk-cband = {path = "../.."}\nother = {path = "untouched"}\n'
    )
    shutil.copy(REPO_ROOT / "scripts/sample_project_ci.py", tmp_path)
    (tmp_path / "bin").mkdir()
    uv = tmp_path / "bin/uv"
    uv.write_text(
        '#!/bin/bash\nshift 4\nexec "' + shutil.which("python3") + '" "${@:2}"\n'
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", editable["run"]],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (
        tomllib.loads((checkout / "pyproject.toml").read_text())["project"]["name"]
        == "examplepdk-cband"
    )
    sources = tomllib.loads((project / "pyproject.toml").read_text())["tool"]["uv"][
        "sources"
    ]
    assert sources["examplepdk-cband"] == {"path": str(checkout), "editable": True}
    assert sources["other"] == {"path": "untouched"}


@pytest.mark.parametrize("keep", SAMPLE_PROJECT_AUDIENCES)
def test_sample_project_secrets_match_contract(keep: str) -> None:
    private = keep == "private"
    for filename in ("sample-project-test.yml", "sample-projects.yml"):
        data = yaml.safe_load(
            apply_sync_markers((WORKFLOWS_DIR / filename).read_text(), keep=keep)
        )
        secrets = data[True]["workflow_call"]["secrets"]
        expected = {"GFP_API_KEY": {"required": False}}
        if private:
            expected["PDK_CI_WORKFLOW_TOKEN"] = {"required": True}
            if filename == "sample-projects.yml":
                expected["GSP_SYNC_TOKEN"] = {"required": False}
        assert secrets == expected
    for mode, job_id in [
        ("editable", "test-sample-projects"),
        ("released", "released"),
    ]:
        data = yaml.safe_load(
            apply_sync_markers(
                (TEMPLATES_DIR / f"sample-projects-{mode}.yml").read_text(), keep=keep
            )
        )
        job = data["jobs"][job_id]
        expected = {"GFP_API_KEY": "${{ secrets.GFP_API_KEY }}"}
        if private:
            expected["PDK_CI_WORKFLOW_TOKEN"] = "${{ secrets.PDK_CI_WORKFLOW_TOKEN }}"
            if mode == "released":
                expected["GSP_SYNC_TOKEN"] = "${{ secrets.PDK_CI_WORKFLOW_TOKEN }}"
        assert job["secrets"] == expected
        assert job["with"].get("upload", False) == (private and mode == "released")


@pytest.mark.parametrize(
    "items,allow_zero,expected",
    [(None, False, 1), (0, False, 1), (0, True, 0), (2, False, 0)],
)
def test_sample_project_drc_verification_and_log_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    items: int | None,
    allow_zero: bool,
    expected: int,
) -> None:
    steps = {
        step["id"]: step
        for step in _load_yaml(WORKFLOWS_DIR / "sample-project-test.yml")["jobs"][
            "test"
        ]["steps"]
        if "id" in step
    }
    (tmp_path / "build/lyrdb").mkdir(parents=True)
    if items is not None:
        (tmp_path / "build/lyrdb/sample_drc_errors.lyrdb").write_text(
            "<item>\n" * items
        )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.gdsfactoryplus.drc]\nallow-zero-errors = "
        + str(allow_zero).lower()
        + "\n"
    )
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", steps["drc-verify"]["run"]],
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == expected
    monkeypatch.setenv("GFP_API_KEY", "test-only-secret")
    log = tmp_path / "build/drc_attempt_1.log"
    log.write_text("request test-only-secret response")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", steps["redact"]["run"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "test-only-secret" not in result.stdout
    assert log.read_text() == "request *** response"
    assert "steps.redact.outcome == 'success'" in steps["drc-report"]["if"]
