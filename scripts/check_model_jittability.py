"""Check that registered PDK models accept JAX-traced numeric parameters.

The check discovers the active PDK through ``pyproject.toml``, calls every
registered model once with its concrete defaults, then passes every numeric
default to ``jax.jit``.  Strings, booleans, and other structural settings stay
at their Python defaults and are therefore static during tracing.

Models that cannot be called with defaults are reported as skipped.  A JIT
failure is reported as a failure only after the same model succeeds with
ordinary Python values, keeping unrelated fixture and data errors out of this
compatibility check.

Integer-valued physical parameters are dynamic by default. PDKs can mark
structural numeric parameters as static, or skip models that require custom
fixtures, under ``[tool.gdsfactoryplus.pdk.model_jittability]``.
"""

from __future__ import annotations

import importlib
import inspect
import numbers
import os
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomllib

ProbeStatus = Literal["passed", "failed", "skipped"]


@dataclass(frozen=True)
class ModelProbeResult:
    """Result of tracing one registered model."""

    name: str
    status: ProbeStatus
    dynamic_parameters: tuple[str, ...] = ()
    failing_parameters: tuple[str, ...] = ()
    detail: str = ""


def is_numeric_value(value: Any) -> bool:
    """Return whether *value* should be presented to JAX as a tracer."""
    if isinstance(value, bool):
        return False
    if isinstance(value, numbers.Number):
        return True

    dtype = getattr(value, "dtype", None)
    dtype_kind = getattr(dtype, "kind", None)
    if dtype_kind is not None:
        return dtype_kind in {"i", "u", "f", "c"}

    if isinstance(value, (tuple, list)):
        return bool(value) and all(is_numeric_value(item) for item in value)
    return False


def numeric_default_parameters(
    model: Callable[..., Any],
    static_parameters: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return traceable defaults and required arguments for *model*."""
    signature = inspect.signature(model)
    dynamic_arguments: dict[str, Any] = {}
    required_arguments: list[str] = []

    for parameter in signature.parameters.values():
        if parameter.name in static_parameters:
            continue
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.default is inspect.Parameter.empty:
            required_arguments.append(parameter.name)
            continue
        if not is_numeric_value(parameter.default):
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            required_arguments.append(f"{parameter.name} (positional-only)")
            continue
        dynamic_arguments[parameter.name] = parameter.default

    return dynamic_arguments, tuple(required_arguments)


def _force_ready(jax_module: Any, value: Any) -> None:
    """Wait for asynchronous JAX results so compilation errors surface."""
    for leaf in jax_module.tree_util.tree_leaves(value):
        block_until_ready = getattr(leaf, "block_until_ready", None)
        if block_until_ready is not None:
            block_until_ready()


def _run_jitted(
    model: Callable[..., Any], dynamic_arguments: dict[str, Any], jax_module: Any
) -> Any:
    result = jax_module.jit(model)(**dynamic_arguments)
    _force_ready(jax_module, result)
    return result


def _format_exception(error: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__, limit=12)
    ).strip()


def probe_model(
    name: str,
    model: Callable[..., Any],
    jax_module: Any,
    static_parameters: frozenset[str] = frozenset(),
) -> ModelProbeResult:
    """Call and JIT one model, isolating numeric parameters after a failure."""
    if not callable(model):
        return ModelProbeResult(
            name, "skipped", detail="registered value is not callable"
        )

    try:
        dynamic_arguments, required_arguments = numeric_default_parameters(
            model, static_parameters
        )
    except (TypeError, ValueError) as error:
        return ModelProbeResult(
            name,
            "skipped",
            detail=f"signature is unavailable: {type(error).__name__}: {error}",
        )

    parameter_names = tuple(dynamic_arguments)
    if required_arguments:
        return ModelProbeResult(
            name,
            "skipped",
            parameter_names,
            detail=f"requires arguments without defaults: {', '.join(required_arguments)}",
        )
    if not dynamic_arguments:
        return ModelProbeResult(
            name, "skipped", detail="no numeric default parameters to trace"
        )

    try:
        concrete_result = model(**dynamic_arguments)
    except Exception as error:  # noqa: BLE001 - third-party PDK model boundary
        return ModelProbeResult(
            name,
            "skipped",
            parameter_names,
            detail=f"concrete call failed: {type(error).__name__}: {error}",
        )
    if not isinstance(concrete_result, Mapping) or not concrete_result:
        return ModelProbeResult(
            name,
            "skipped",
            parameter_names,
            detail="concrete call did not return a non-empty mapping",
        )

    try:
        jitted_result = _run_jitted(model, dynamic_arguments, jax_module)
        if not isinstance(jitted_result, Mapping) or not jitted_result:
            return ModelProbeResult(
                name,
                "failed",
                parameter_names,
                detail="JIT call did not return a non-empty mapping",
            )
    except Exception as error:  # noqa: BLE001 - report JAX/PDK tracing failures
        failing_parameters: list[str] = []
        for parameter_name, default_value in dynamic_arguments.items():
            try:
                _run_jitted(model, {parameter_name: default_value}, jax_module)
            except Exception:  # noqa: BLE001 - parameter isolation probe
                failing_parameters.append(parameter_name)

        return ModelProbeResult(
            name,
            "failed",
            parameter_names,
            tuple(failing_parameters),
            _format_exception(error),
        )

    return ModelProbeResult(name, "passed", parameter_names)


def pdk_module_name(pyproject_path: Path) -> str:
    """Read the configured GDSFactory+ PDK import path."""
    with pyproject_path.open("rb") as pyproject_file:
        config = tomllib.load(pyproject_file)
    name = (
        config.get("tool", {}).get("gdsfactoryplus", {}).get("pdk", {}).get("name", "")
    )
    if not name:
        raise ValueError("No [tool.gdsfactoryplus.pdk] name found in pyproject.toml")
    return name


def model_jittability_settings(pyproject_path: Path) -> Mapping[str, Any]:
    """Read optional model skips and structural numeric parameters."""
    with pyproject_path.open("rb") as pyproject_file:
        config = tomllib.load(pyproject_file)
    settings = (
        config.get("tool", {})
        .get("gdsfactoryplus", {})
        .get("pdk", {})
        .get("model_jittability", {})
    )
    if not isinstance(settings, Mapping):
        raise TypeError("[tool.gdsfactoryplus.pdk.model_jittability] must be a table")
    skip_models = settings.get("skip_models", [])
    if not isinstance(skip_models, list) or not all(
        isinstance(name, str) for name in skip_models
    ):
        raise TypeError("model_jittability.skip_models must be an array of strings")
    static_parameters = settings.get("static_parameters", {})
    if not isinstance(static_parameters, Mapping) or not all(
        isinstance(name, str)
        and isinstance(parameters, list)
        and all(isinstance(parameter, str) for parameter in parameters)
        for name, parameters in static_parameters.items()
    ):
        raise TypeError(
            "model_jittability.static_parameters must map model names to string arrays"
        )
    return settings


def model_is_skipped(name: str, settings: Mapping[str, Any]) -> bool:
    """Return whether *name* is explicitly excluded from this probe."""
    return name in settings.get("skip_models", [])


def static_parameters_for_model(
    name: str, settings: Mapping[str, Any]
) -> frozenset[str]:
    """Return configured structural parameters for one model."""
    configured = settings.get("static_parameters", {})
    return frozenset(configured.get("*", [])) | frozenset(configured.get(name, []))


def load_registered_models(pyproject_path: Path) -> Mapping[str, Any]:
    """Import and activate the configured PDK, then return its models."""
    module = importlib.import_module(pdk_module_name(pyproject_path))
    pdk = module.PDK
    pdk.activate()
    return getattr(pdk, "models", {}) or {}


def _summary_detail(result: ModelProbeResult) -> str:
    if result.status == "failed" and result.failing_parameters:
        return f"fails when tracing: {', '.join(result.failing_parameters)}"
    if result.status == "failed":
        return "combined numeric trace failed"
    if result.status == "skipped":
        return result.detail
    return ""


def write_step_summary(results: list[ModelProbeResult], summary_path: Path) -> None:
    """Append a compact compatibility table to the GitHub step summary."""
    passed = sum(result.status == "passed" for result in results)
    failed = sum(result.status == "failed" for result in results)
    skipped = sum(result.status == "skipped" for result in results)

    with summary_path.open("a", encoding="utf-8") as summary:
        summary.write("## JAX model jittability\n\n")
        summary.write(f"Passed: {passed} · Failed: {failed} · Skipped: {skipped}\n\n")
        if not results:
            summary.write("No registered models.\n")
            return
        summary.write("| Model | Result | Numeric parameters | Detail |\n")
        summary.write("|---|---|---|---|\n")
        for result in results:
            parameters = ", ".join(result.dynamic_parameters)
            detail = _summary_detail(result).replace("|", "\\|").replace("\n", " ")
            summary.write(
                f"| `{result.name}` | {result.status} | `{parameters}` | {detail} |\n"
            )


def _annotation_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def report_result(result: ModelProbeResult, annotation_level: str = "warning") -> None:
    """Print one result, including a GitHub error annotation on failure."""
    parameters = ", ".join(result.dynamic_parameters) or "none"
    if result.status == "passed":
        print(f"PASS {result.name}: traced {parameters}")
        return
    if result.status == "skipped":
        print(f"SKIP {result.name}: {result.detail}")
        return

    failing = ", ".join(result.failing_parameters) or "combined arguments"
    annotation = _annotation_escape(
        f"{result.name} is not JIT-traceable; failing parameters: {failing}"
    )
    print(f"::{annotation_level} title=JAX model jittability::{annotation}")
    print(f"FAIL {result.name}: {failing}\n{result.detail}")


def _annotation_level() -> str:
    enforce = os.environ.get("PDK_MODEL_JIT_ENFORCE", "false").lower() == "true"
    return "error" if enforce else "warning"


def _write_optional_summary(results: list[ModelProbeResult]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        write_step_summary(results, Path(summary))


def _report_setup_failure(error: BaseException, annotation_level: str) -> int:
    detail = f"setup failed: {type(error).__name__}: {error}"
    result = ModelProbeResult("setup", "skipped", detail=detail)
    annotation = _annotation_escape(detail)
    print(f"::{annotation_level} title=JAX model jittability setup::{annotation}")
    report_result(result, annotation_level)
    _write_optional_summary([result])
    print("JAX jittability: setup failed; no models were probed")
    return 1


def main() -> int:
    """Probe every registered model and fail if any concrete model cannot JIT."""
    pyproject_path = Path("pyproject.toml")
    annotation_level = _annotation_level()
    try:
        settings = model_jittability_settings(pyproject_path)
        models = load_registered_models(pyproject_path)
    except Exception as error:  # noqa: BLE001 - report PDK setup failures
        return _report_setup_failure(error, annotation_level)

    if not models:
        print("No registered models; JAX jittability check passes.")
        results: list[ModelProbeResult] = []
    else:
        try:
            jax_module = importlib.import_module("jax")
        except Exception as error:  # noqa: BLE001 - report JAX setup failures
            return _report_setup_failure(error, annotation_level)

        results = []
        for name, model in sorted(models.items()):
            if model_is_skipped(name, settings):
                results.append(
                    ModelProbeResult(name, "skipped", detail="configured skip")
                )
                continue
            static_parameters = static_parameters_for_model(name, settings)
            results.append(probe_model(name, model, jax_module, static_parameters))
        for result in results:
            report_result(result, annotation_level)

    _write_optional_summary(results)

    failed = sum(result.status == "failed" for result in results)
    passed = sum(result.status == "passed" for result in results)
    skipped = sum(result.status == "skipped" for result in results)
    print(f"JAX jittability: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
