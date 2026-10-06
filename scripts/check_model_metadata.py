"""Validate PDK model metadata and detect legacy-only assignments.

Run in the owning PDK's installed environment. The default strict check requires
SAX/Circulax metadata for registered cells with registry or schematic models.
Cells without model assignments are not required to invent a binding. This is
an interface check, not a simulation or foundry-model accuracy check.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

LANGUAGES = {"sax", "circulax"}
SIDE_ORIENTATIONS = {"left": 180, "right": 0, "top": 90, "bottom": 270}


@dataclass(frozen=True)
class Issue:
    cell: str
    code: str
    detail: str


@dataclass
class AuditReport:
    pdk: str
    registered_cells: int = 0
    registry_modeled_cells: int = 0
    legacy_schematic_modeled_cells: int = 0
    metadata_modeled_cells: int = 0
    other_language_cells: int = 0
    passed_bindings: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues


def metadata_factory(cell_factory: Any) -> Any | None:
    """Find the owning wrapper; do not consult a colliding global name registry."""
    candidates = [cell_factory]
    visited: set[int] = set()
    while candidates:
        candidate = candidates.pop(0)
        if id(candidate) in visited:
            continue
        visited.add(id(candidate))
        if (
            callable(getattr(candidate, "get_metadata", None))
            and callable(getattr(candidate, "has_metadata", None))
        ) or callable(getattr(candidate, "_f_schematic", None)):
            return candidate
        if isinstance(candidate, partial):
            candidates.append(candidate.func)
        for closure_cell in getattr(candidate, "__closure__", None) or ():
            try:
                value = closure_cell.cell_contents
            except ValueError:
                continue
            if callable(getattr(value, "get_metadata", None)) or callable(
                getattr(value, "_f_schematic", None)
            ):
                candidates.append(value)
        wrapped = getattr(candidate, "__wrapped__", None)
        if wrapped is not None:
            candidates.append(wrapped)
    return None


def default_factory_arguments(cell_factory: Any) -> dict[str, Any]:
    """Include arguments already bound by a registered partial factory."""
    if isinstance(cell_factory, partial):
        signature = inspect.signature(cell_factory.func)
        bound = signature.bind_partial(
            *cell_factory.args, **(cell_factory.keywords or {})
        )
    else:
        signature = inspect.signature(cell_factory)
        bound = signature.bind_partial()
    bound.apply_defaults()
    return {
        name: value
        for name, value in bound.arguments.items()
        if signature.parameters[name].kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


def provider_arguments(provider: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Forward only settings accepted by the provider's callable interface."""
    parameters = inspect.signature(provider).parameters
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return settings
    return {name: value for name, value in settings.items() if name in parameters}


def read_metadata(cell_factory: Any) -> dict[str, Any]:
    factory = metadata_factory(cell_factory)
    # Wrapped factories can be falsy; use identity rather than truthiness.
    if (
        factory is None
        or not callable(getattr(factory, "has_metadata", None))
        or not factory.has_metadata()
    ):
        return {"models": [], "ports": []}
    settings = provider_arguments(
        factory.get_metadata, default_factory_arguments(cell_factory)
    )
    metadata = factory.get_metadata(**settings)
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {"models": metadata.models, "ports": metadata.ports}


def legacy_model_entries(cell_factory: Any) -> list[Mapping[str, Any]]:
    """Discover assignments in legacy schematic info without accepting them."""
    factory = metadata_factory(cell_factory)
    provider = getattr(factory, "_f_schematic", None)
    if not callable(provider):
        return []
    defaults = provider_arguments(provider, default_factory_arguments(cell_factory))
    schematic = provider(**defaults)
    info = dict(getattr(schematic, "info", {}) or {})
    models = info.get("models", [])
    if not isinstance(models, (list, tuple)) or not all(
        isinstance(entry, Mapping) for entry in models
    ):
        raise ValueError("legacy schematic models must be a list of dictionaries")
    return list(models)


def string_mapping(entry: Mapping[str, Any], key: str) -> dict[str, str]:
    value = entry.get(key, {})
    if not isinstance(value, Mapping) or not all(
        isinstance(source, str) and isinstance(target, str)
        for source, target in value.items()
    ):
        raise ValueError(f"{key} must map strings to strings")
    return dict(value)


def model_callable(entry: Mapping[str, Any]) -> Any:
    module_name = entry.get("module")
    qualname = entry.get("qualname", entry.get("name"))
    if not isinstance(module_name, str) or not module_name:
        raise ValueError("module must be a non-empty import path")
    if not isinstance(qualname, str) or not qualname:
        raise ValueError("qualname or name must identify the model")
    model = importlib.import_module(module_name)
    for part in qualname.split("."):
        if part == "<locals>":
            raise ValueError("model must be importable outside a local function")
        model = getattr(model, part)
    if not callable(model):
        raise TypeError(f"{module_name}.{qualname} is not callable")
    return model


def validate_port_order(entry: Mapping[str, Any]) -> list[str]:
    ports = entry.get("port_order")
    if (
        not isinstance(ports, (list, tuple))
        or not ports
        or not all(isinstance(port, str) and port for port in ports)
    ):
        raise ValueError("port_order must contain non-empty terminal names")
    if len(set(ports)) != len(ports):
        raise ValueError("port_order contains duplicate terminal names")
    return list(ports)


def validate_symbol_ports(metadata: Mapping[str, Any], component: Any) -> set[str]:
    component_ports = {port.name: port for port in component.ports}
    # Model-only providers are valid; symbol ports are optional in kfactory.
    if not metadata.get("ports"):
        return set(component_ports)
    declared: set[str] = set()
    for port in metadata.get("ports", ()):
        name = port["name"]
        if name in declared:
            raise ValueError(f"duplicate metadata terminal {name!r}")
        declared.add(name)
        if name not in component_ports:
            raise ValueError(f"metadata terminal {name!r} does not exist on the cell")
        actual = component_ports[name]
        if port.get("kind") != actual.port_type:
            raise ValueError(f"metadata terminal {name!r} has the wrong kind")
        side = port.get("side")
        if side is not None:
            if side not in SIDE_ORIENTATIONS:
                raise ValueError(f"metadata terminal {name!r} has an unknown side")
            orientation = SIDE_ORIENTATIONS[side]
            if port.get("orientation", orientation) != orientation:
                raise ValueError(
                    f"metadata terminal {name!r} has conflicting orientation"
                )
            if actual.orientation is not None:
                difference = abs(
                    (float(actual.orientation) - orientation + 180) % 360 - 180
                )
                if difference >= 1.0:
                    raise ValueError(
                        f"metadata terminal {name!r} side differs from the cell"
                    )
    return declared


def component_settings(component: Any) -> dict[str, Any]:
    settings = getattr(component, "settings", {})
    if hasattr(settings, "model_dump"):
        return settings.model_dump()
    return dict(settings)


def model_arguments(
    entry: Mapping[str, Any], model: Any, cell_factory: Any, component: Any
) -> dict[str, Any]:
    parameters = inspect.signature(model).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    defaults = entry.get("defaults", {})
    if not isinstance(defaults, Mapping) or not all(
        isinstance(name, str) for name in defaults
    ):
        raise ValueError("defaults must map model parameter names to values")
    params = string_mapping(entry, "params")
    unknown = (set(defaults) | set(params.values())) - set(parameters)
    if unknown and not accepts_kwargs:
        raise ValueError(f"unknown model parameters: {sorted(unknown)}")
    settings = component_settings(component)
    cell_parameters = inspect.signature(cell_factory).parameters
    unknown_sources = set(params) - (set(settings) | set(cell_parameters))
    if unknown_sources:
        raise ValueError(f"unknown cell settings in params: {sorted(unknown_sources)}")
    arguments = dict(defaults)
    # Explicit settings override metadata defaults; params renames, never scales.
    for name, value in settings.items():
        target = params.get(name, name)
        if target in parameters or name in params:
            arguments[target] = value
    for source, target in params.items():
        if source not in settings:
            default = cell_parameters[source].default
            if default is inspect.Parameter.empty:
                raise ValueError(
                    f"no value available for mapped cell setting {source!r}"
                )
            arguments[target] = default
    return arguments


def validate_binding(
    entry: Mapping[str, Any],
    cell_factory: Any,
    component: Any,
    declared_ports: set[str],
) -> None:
    port_order = validate_port_order(entry)
    physical_ports = {port.name for port in component.ports}
    missing = set(port_order) - (declared_ports & physical_ports)
    if missing:
        raise ValueError(f"model terminals not declared on the cell: {sorted(missing)}")
    port_map = string_mapping(entry, "port_map")
    if set(port_map) - set(port_order):
        raise ValueError("port_map contains terminals outside port_order")
    model = model_callable(entry)
    arguments = model_arguments(entry, model, cell_factory, component)
    if entry["language"] == "circulax":
        instance = model(**arguments)
        model_ports = tuple(instance.ports)
        mapped_ports = tuple(port_map.get(port, port) for port in port_order)
        if mapped_ports != model_ports:
            raise ValueError(
                f"mapped cell terminal order {mapped_ports} does not match "
                f"Circulax terminals {model_ports}"
            )
    else:
        result = model(**arguments)
        if not isinstance(result, Mapping):
            import sax

            result = sax.sdict(result)
        if not result:
            raise ValueError("SAX model returned an empty SDict")
        for pair in result:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(port, str) for port in pair)
            ):
                raise ValueError(f"invalid SAX SDict terminal pair {pair!r}")
        model_ports = {port for pair in result for port in pair}
        if model_ports - set(port_order):
            raise ValueError(
                f"SAX terminals absent from port_order: {sorted(model_ports - set(port_order))}; "
                "SAX connects by name and needs an adapter for renamed terminals"
            )


def audit_pdk(
    pdk: Any,
    name: str,
    *,
    require_metadata: bool = True,
    expected_cells: tuple[str, ...] = (),
) -> AuditReport:
    """Validate all declared bindings; require metadata for existing assignments."""
    pdk.activate()
    cells = pdk.cells
    modeled_cells = set(cells) & set(getattr(pdk, "models", {}) or {})
    report = AuditReport(name, len(cells), len(modeled_cells))
    required_cells = set(expected_cells) | (
        modeled_cells if require_metadata else set()
    )
    for missing in sorted(required_cells - set(cells)):
        report.issues.append(
            Issue(missing, "missing-cell", "expected cell is not registered")
        )
    for cell_name, cell_factory in sorted(cells.items()):
        try:
            metadata = read_metadata(cell_factory)
            models = metadata.get("models", [])
            if not isinstance(models, (list, tuple)) or not all(
                isinstance(entry, Mapping) for entry in models
            ):
                raise ValueError("metadata.models must be a list of model dictionaries")
            entries = [entry for entry in models if entry.get("language") in LANGUAGES]
        except Exception as error:  # noqa: BLE001 - report provider failures per cell
            report.issues.append(
                Issue(cell_name, "metadata-error", f"{type(error).__name__}: {error}")
            )
            continue
        if not entries:
            try:
                legacy_entries = legacy_model_entries(cell_factory)
            except Exception as error:  # noqa: BLE001 - do not hide legacy discovery failures
                report.issues.append(
                    Issue(
                        cell_name,
                        "legacy-discovery-error",
                        f"{type(error).__name__}: {error}",
                    )
                )
                continue
            legacy_modeled = any(
                entry.get("language") in LANGUAGES for entry in legacy_entries
            )
            if legacy_modeled:
                report.legacy_schematic_modeled_cells += 1
            if any(
                entry.get("language") not in LANGUAGES
                for entry in [*models, *legacy_entries]
            ):
                report.other_language_cells += 1
            if cell_name in required_cells or (require_metadata and legacy_modeled):
                report.issues.append(
                    Issue(
                        cell_name,
                        "missing-metadata",
                        "no SAX/Circulax metadata model; legacy registry or schematic info alone does not satisfy this check",
                    )
                )
            continue
        report.metadata_modeled_cells += 1
        try:
            component = cell_factory()
            declared_ports = validate_symbol_ports(metadata, component)
        except Exception as error:  # noqa: BLE001 - report cell construction failures
            report.issues.append(
                Issue(
                    cell_name,
                    "cell-interface-error",
                    f"{type(error).__name__}: {error}",
                )
            )
            continue
        for entry in entries:
            try:
                validate_binding(entry, cell_factory, component, declared_ports)
            except Exception as error:  # noqa: BLE001 - model errors are failed bindings
                report.issues.append(
                    Issue(
                        cell_name,
                        "invalid-binding",
                        f"{entry['language']} {entry.get('name', '?')}: {type(error).__name__}: {error}",
                    )
                )
            else:
                report.passed_bindings += 1
    return report


def main(argv: list[str] | None = None) -> int:
    from model_metadata_cli import main as run_cli

    return run_cli(argv, audit_pdk, Path(__file__))


if __name__ == "__main__":
    raise SystemExit(main())
