"""Behavioral tests of metadata enforcement using synthetic PDK interfaces."""

from __future__ import annotations

import importlib.util
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_model_metadata.py"
spec = importlib.util.spec_from_file_location("check_model_metadata", SCRIPT)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


def optical_model(wl=1.55, length_um=10.0):
    return {("in", "out"): wl * length_um}


class Resistor:
    ports = ("p1", "p2")

    def __init__(self, R=100.0):
        self.R = R


class FakeFactory:
    def __init__(self, models=(), *, settings=None, metadata_ports=None):
        self.models = list(models)
        self.settings = settings or {}
        self.ports = metadata_ports or [
            {"name": "in", "kind": "optical", "side": "left"},
            {"name": "out", "kind": "optical", "side": "right"},
        ]

    def __bool__(self):
        return False

    def has_metadata(self):
        return bool(self.models)

    def get_metadata(self):
        return SimpleNamespace(models=self.models, ports=self.ports)

    def __call__(self):
        return SimpleNamespace(
            ports=[
                SimpleNamespace(name="in", port_type="optical", orientation=180),
                SimpleNamespace(name="out", port_type="optical", orientation=0),
            ],
            settings=self.settings,
        )


def sax_entry(**updates):
    return {
        "language": "sax",
        "name": "optical_model",
        "module": __name__,
        "qualname": "optical_model",
        "port_order": ["in", "out"],
        **updates,
    }


def fake_pdk(factory, *, legacy=True):
    return SimpleNamespace(
        cells={"cell": factory},
        models={"cell": optical_model} if legacy else {},
        activate=lambda: None,
    )


def test_legacy_only_assignment_fails_strict_check():
    report = check.audit_pdk(fake_pdk(FakeFactory()), "fixture")
    assert not report.passed
    assert [issue.code for issue in report.issues] == ["missing-metadata"]
    assert report.registry_modeled_cells == 1


def test_metadata_binding_satisfies_requirement_even_with_falsy_factory():
    report = check.audit_pdk(fake_pdk(FakeFactory([sax_entry()])), "fixture")
    assert report.passed
    assert report.metadata_modeled_cells == report.passed_bindings == 1


def test_metadata_only_assignment_is_validated_without_legacy_registration():
    report = check.audit_pdk(
        fake_pdk(FakeFactory([sax_entry()]), legacy=False), "fixture"
    )
    assert report.passed and report.passed_bindings == 1


def test_unmodeled_cells_do_not_require_fake_entries():
    report = check.audit_pdk(fake_pdk(FakeFactory(), legacy=False), "fixture")
    assert report.passed and report.passed_bindings == 0


def test_explicit_expected_cell_guards_empty_metadata_only_discovery():
    report = check.audit_pdk(
        fake_pdk(FakeFactory(), legacy=False), "fixture", expected_cells=("cell",)
    )
    assert [issue.code for issue in report.issues] == ["missing-metadata"]


def test_missing_expected_factory_fails():
    report = check.audit_pdk(
        fake_pdk(FakeFactory(), legacy=False), "fixture", expected_cells=("absent",)
    )
    assert [issue.code for issue in report.issues] == ["missing-cell"]


def test_bindings_only_mode_does_not_require_migration():
    report = check.audit_pdk(fake_pdk(FakeFactory()), "fixture", require_metadata=False)
    assert report.passed and report.passed_bindings == 0


def test_owning_closure_and_partial_are_unwrapped():
    factory = FakeFactory([sax_entry()])

    def wrapper():
        return factory()

    assert check.metadata_factory(wrapper) is factory
    assert check.metadata_factory(partial(wrapper)) is factory
    assert check.audit_pdk(fake_pdk(wrapper), "fixture").passed


def test_bound_partial_settings_resolve_matching_metadata():
    class ReversibleFactory(FakeFactory):
        def __call__(self, flip=False):
            component = super().__call__()
            if flip:
                for port in component.ports:
                    port.orientation = (port.orientation + 180) % 360
            return component

        def get_metadata(self, flip=False):
            metadata = super().get_metadata()
            if flip:
                metadata.ports = [
                    {**port, "side": "right" if port["side"] == "left" else "left"}
                    for port in metadata.ports
                ]
            return metadata

    factory = partial(ReversibleFactory([sax_entry()]), flip=True)
    report = check.audit_pdk(fake_pdk(factory), "fixture")
    assert report.passed and report.passed_bindings == 1


def test_defaulted_cell_settings_work_with_no_argument_metadata_method():
    class FactoryWithSettings(FakeFactory):
        def __call__(self, length=10.0):
            return super().__call__()

    factory = FactoryWithSettings([sax_entry()])
    report = check.audit_pdk(fake_pdk(factory), "fixture")
    assert report.passed and report.passed_bindings == 1


@pytest.mark.parametrize(
    "updates,detail",
    [
        ({"qualname": "absent"}, "AttributeError"),
        ({"port_order": ["in", "in"]}, "duplicate"),
        ({"port_order": ["in", "missing"]}, "not declared"),
        ({"port_order": ["in"]}, "SAX terminals absent"),
        ({"params": {"missing": "length_um"}}, "unknown cell settings"),
        ({"defaults": {"unknown": 3}}, "unknown model parameters"),
        ({"port_map": {"missing": "p1"}}, "outside port_order"),
    ],
)
def test_invalid_bindings_fail_instead_of_using_registry_fallback(updates, detail):
    report = check.audit_pdk(fake_pdk(FakeFactory([sax_entry(**updates)])), "fixture")
    assert not report.passed
    assert report.issues[0].code == "invalid-binding"
    assert detail in report.issues[0].detail


def test_model_evaluation_errors_do_not_pass(monkeypatch):
    def unavailable():
        raise TypeError("model cannot be evaluated")

    monkeypatch.setattr(sys.modules[__name__], "optical_model", unavailable)
    report = check.audit_pdk(fake_pdk(FakeFactory([sax_entry()])), "fixture")
    assert not report.passed
    assert "model cannot be evaluated" in report.issues[0].detail


def test_defaults_and_parameter_mapping_are_applied_with_setting_precedence():
    factory = FakeFactory(settings={"length": 20.0})
    entry = sax_entry(params={"length": "length_um"}, defaults={"length_um": 5.0})
    arguments = check.model_arguments(entry, optical_model, factory, factory())
    assert optical_model(**arguments) == {("in", "out"): 31.0}


def test_circulax_mapping_is_checked_and_constructor_runs():
    entry = sax_entry(
        language="circulax",
        name="Resistor",
        qualname="Resistor",
        port_map={"in": "p1", "out": "p2"},
        defaults={"R": 80.7},
    )
    report = check.audit_pdk(fake_pdk(FakeFactory([entry])), "fixture")
    assert report.passed and report.passed_bindings == 1
    entry["port_map"]["out"] = "unknown"
    report = check.audit_pdk(fake_pdk(FakeFactory([entry])), "fixture")
    assert not report.passed
    assert "Circulax terminals" in report.issues[0].detail


def test_provider_errors_are_reported(monkeypatch):
    factory = FakeFactory([sax_entry()])

    def broken_provider():
        raise ValueError("provider broken")

    monkeypatch.setattr(factory, "get_metadata", broken_provider)
    report = check.audit_pdk(fake_pdk(factory), "fixture")
    assert not report.passed
    assert report.issues[0].code == "metadata-error"


def test_circulax_terminal_order_is_validated():
    entry = sax_entry(
        language="circulax",
        name="Resistor",
        qualname="Resistor",
        port_order=["out", "in"],
        port_map={"in": "p1", "out": "p2"},
    )
    report = check.audit_pdk(fake_pdk(FakeFactory([entry])), "fixture")
    assert not report.passed
    assert "terminal order" in report.issues[0].detail


def test_missing_metadata_api_on_legacy_factory_fails():
    report = check.audit_pdk(fake_pdk(lambda: None), "fixture")
    assert [issue.code for issue in report.issues] == ["missing-metadata"]


def test_legacy_schematic_assignment_without_registry_is_rejected():
    factory = FakeFactory()
    factory._f_schematic = lambda: SimpleNamespace(info={"models": [sax_entry()]})
    report = check.audit_pdk(fake_pdk(factory, legacy=False), "fixture")
    assert report.legacy_schematic_modeled_cells == 1
    assert [issue.code for issue in report.issues] == ["missing-metadata"]


def test_old_factory_without_metadata_api_is_discovered_in_closure():
    legacy = SimpleNamespace(
        _f_schematic=lambda: SimpleNamespace(info={"models": [sax_entry()]})
    )

    def wrapper():
        return legacy

    report = check.audit_pdk(fake_pdk(wrapper, legacy=False), "fixture")
    assert report.legacy_schematic_modeled_cells == 1
    assert not report.passed


def test_model_only_metadata_uses_actual_cell_ports(monkeypatch):
    factory = FakeFactory([sax_entry()])
    monkeypatch.setattr(factory, "ports", [])
    report = check.audit_pdk(fake_pdk(factory), "fixture")
    assert report.passed and report.passed_bindings == 1


def test_spice_schematic_is_outside_sax_circulax_scope():
    factory = FakeFactory()
    factory._f_schematic = lambda: SimpleNamespace(
        info={"models": [{"language": "spice"}]}
    )
    report = check.audit_pdk(fake_pdk(factory, legacy=False), "fixture")
    assert report.passed
    assert report.other_language_cells == 1
    assert report.legacy_schematic_modeled_cells == report.passed_bindings == 0


def test_legacy_provider_receives_defaults_and_errors_are_not_hidden():
    def provider(length):
        assert length == 20
        raise ValueError("legacy provider broken")

    factory = SimpleNamespace(_f_schematic=provider)

    def cell(length=20):
        return factory

    report = check.audit_pdk(fake_pdk(cell, legacy=False), "fixture")
    assert [issue.code for issue in report.issues] == ["legacy-discovery-error"]
    assert "legacy provider broken" in report.issues[0].detail


def test_legacy_provider_receives_positionally_bound_partial_setting():
    def provider(length):
        assert length == 25
        return SimpleNamespace(info={"models": [sax_entry()]})

    legacy = SimpleNamespace(_f_schematic=provider)

    def cell(length):
        return legacy

    report = check.audit_pdk(fake_pdk(partial(cell, 25), legacy=False), "fixture")
    assert report.legacy_schematic_modeled_cells == 1
    assert [issue.code for issue in report.issues] == ["missing-metadata"]
