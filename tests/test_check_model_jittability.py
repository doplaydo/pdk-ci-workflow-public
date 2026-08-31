"""Tests for the generic registered-model JAX probe."""

from __future__ import annotations

import importlib.util
import sys
from functools import partial
from pathlib import Path

import pytest
import yaml

from hooks._utils import apply_sync_markers

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_model_jittability.py"
ACTION = REPO_ROOT / "actions" / "check_model_jittability" / "action.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "model_regression.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_model_jittability", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_model_jittability"] = module
    spec.loader.exec_module(module)
    return module


check = _load_module()


class FakeTreeUtil:
    @staticmethod
    def tree_leaves(value):
        if isinstance(value, dict):
            return list(value.values())
        return [value]


class FakeJax:
    tree_util = FakeTreeUtil()

    def __init__(self, fail_on: set[str] | None = None):
        self.fail_on = fail_on or set()

    def jit(self, model):
        def compiled(**kwargs):
            failing = self.fail_on & set(kwargs)
            if failing:
                names = ", ".join(sorted(failing))
                raise RuntimeError(f"cannot trace {names}")
            return model(**kwargs)

        return compiled


@pytest.mark.parametrize(
    "value",
    [1, 1.5, 2 + 3j, (11.0, 1.8), [1, 2.0], ((1.0, 2.0), 3.0)],
)
def test_numeric_values_are_dynamic(value) -> None:
    assert check.is_numeric_value(value)


@pytest.mark.parametrize(
    "value",
    [True, False, "strip", None, (), (1.0, "strip"), {"radius": 10.0}],
)
def test_structural_values_are_static(value) -> None:
    assert not check.is_numeric_value(value)


def test_numeric_array_dtype_is_dynamic() -> None:
    class DType:
        kind = "f"

    class Array:
        dtype = DType()

    assert check.is_numeric_value(Array())


def test_boolean_array_dtype_is_static() -> None:
    class DType:
        kind = "b"

    class Array:
        dtype = DType()

    assert not check.is_numeric_value(Array())


def test_probe_traces_all_numeric_defaults() -> None:
    def model(*, wl=1.55, radius=10.0, cross_section="strip", reciprocal=True):
        return {("o1", "o2"): wl + radius}

    result = check.probe_model("bend", model, FakeJax())

    assert result.status == "passed"
    assert result.dynamic_parameters == ("wl", "radius")


def test_probe_isolates_the_parameter_that_cannot_trace() -> None:
    def model(*, wl=1.55, radius=10.0):
        return {("o1", "o2"): wl + radius}

    result = check.probe_model("bend", model, FakeJax({"radius"}))

    assert result.status == "failed"
    assert result.failing_parameters == ("radius",)
    assert "cannot trace radius" in result.detail


def test_probe_handles_partial_model_aliases() -> None:
    def model(*, wl=1.55, radius=10.0, cross_section="strip"):
        return {("o1", "o2"): wl + radius}

    alias = partial(model, cross_section="nitride")
    result = check.probe_model("bend_nitride", alias, FakeJax({"radius"}))

    assert result.status == "failed"
    assert result.failing_parameters == ("radius",)


def test_probe_honours_structural_numeric_parameters() -> None:
    def model(*, wl=1.55, nmodes=2):
        return {("o1", "o2"): wl * nmodes}

    result = check.probe_model(
        "multimode", model, FakeJax({"nmodes"}), frozenset({"nmodes"})
    )

    assert result.status == "passed"
    assert result.dynamic_parameters == ("wl",)


def test_probe_skips_model_with_required_arguments() -> None:
    def model(radius, *, wl=1.55):
        return {("o1", "o2"): radius + wl}

    result = check.probe_model("bend", model, FakeJax())

    assert result.status == "skipped"
    assert "radius" in result.detail


def test_probe_skips_model_that_fails_before_jit() -> None:
    def model(*, wl=1.55):
        raise FileNotFoundError("characterisation data")

    result = check.probe_model("bend", model, FakeJax())

    assert result.status == "skipped"
    assert result.detail.startswith("concrete call failed")


def test_probe_skips_non_sdict_concrete_result() -> None:
    def model(*, wl=1.55):
        return lambda: wl

    result = check.probe_model("invalid", model, FakeJax())

    assert result.status == "skipped"
    assert "non-empty mapping" in result.detail


def test_step_summary_lists_failures_and_skips(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.md"
    results = [
        check.ModelProbeResult("straight", "passed", ("wl", "length")),
        check.ModelProbeResult(
            "bend", "failed", ("wl", "radius"), ("radius",), "traceback"
        ),
        check.ModelProbeResult("fixed", "skipped", detail="no numeric defaults"),
    ]

    check.write_step_summary(results, summary_path)

    summary = summary_path.read_text()
    assert "Passed: 1 · Failed: 1 · Skipped: 1" in summary
    assert "fails when tracing: radius" in summary
    assert "no numeric defaults" in summary


def test_model_jittability_settings_support_skips_and_static_parameters(
    tmp_path: Path,
) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[tool.gdsfactoryplus.pdk.model_jittability]
skip_models = ["fixture_model"]

[tool.gdsfactoryplus.pdk.model_jittability.static_parameters]
"*" = ["nmodes"]
bend = ["npoints"]
"""
    )

    settings = check.model_jittability_settings(pyproject_path)

    assert check.model_is_skipped("fixture_model", settings)
    assert check.static_parameters_for_model("bend", settings) == frozenset(
        {"nmodes", "npoints"}
    )


def test_setup_failure_writes_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.gdsfactoryplus.pdk]\nname = "fixture"\n'
    )
    summary_path = tmp_path / "summary.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr(
        check,
        "load_registered_models",
        lambda _path: (_ for _ in ()).throw(ModuleNotFoundError("fixture")),
    )

    assert check.main() == 1
    assert "setup failed: ModuleNotFoundError: fixture" in summary_path.read_text()


def test_audit_annotations_keep_punctuation(capsys: pytest.CaptureFixture[str]) -> None:
    result = check.ModelProbeResult(
        "bend",
        "failed",
        ("radius", "width"),
        ("radius", "width"),
        "traceback",
    )

    check.report_result(result)

    output = capsys.readouterr().out
    assert "::warning title=JAX model jittability::" in output
    assert "failing parameters: radius, width" in output
    assert "%3A" not in output


def test_composite_action_runs_the_probe_in_the_pdk_environment() -> None:
    action = yaml.safe_load(ACTION.read_text())

    assert action["runs"]["using"] == "composite"
    (step,) = action["runs"]["steps"]
    assert step["shell"] == "bash"
    assert "uv run python" in step["run"]
    assert "check_model_jittability.py" in step["run"]
    assert step["env"]["PDK_MODEL_JIT_ENFORCE"] == "${{ inputs.enforce }}"
    assert action["inputs"]["enforce"]["default"] == "false"


def test_model_regression_workflow_runs_probe_in_audit_mode() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    workflow_call = workflow.get("on") or workflow[True]
    enforcement_input = workflow_call["workflow_call"]["inputs"][
        "enforce-model-jittability"
    ]
    steps = workflow["jobs"]["model-regression"]["steps"]
    step = next(
        item for item in steps if item.get("name") == "Check model JAX jittability"
    )

    assert step["uses"] == (
        "doplaydo/pdk-ci-workflow/actions/check_model_jittability@main"
    )
    assert enforcement_input["default"] is False
    assert step["with"]["enforce"] == "${{ inputs.enforce-model-jittability }}"
    assert step["continue-on-error"] == ("${{ ! inputs.enforce-model-jittability }}")


def test_public_workflow_uses_public_jittability_action() -> None:
    resolved = apply_sync_markers(WORKFLOW.read_text(), keep="public")
    workflow = yaml.safe_load(resolved)
    steps = workflow["jobs"]["model-regression"]["steps"]
    step = next(
        item for item in steps if item.get("name") == "Check model JAX jittability"
    )

    assert step["uses"] == (
        "doplaydo/pdk-ci-workflow-public/actions/check_model_jittability@main"
    )
