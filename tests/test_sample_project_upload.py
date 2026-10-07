"""Tests for the pure functions in scripts/sample_project_upload.py.

The portal HTTP calls are a few lines each and are exercised by a real
workflow run, not mocked here.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sample_project_upload.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sample_project_upload", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["sample_project_upload"] = module
    spec.loader.exec_module(module)
    return module


spu = _load_module()


def test_build_zip_puts_project_files_at_archive_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("build/\n")

    with zipfile.ZipFile(io.BytesIO(spu.build_zip(tmp_path))) as zf:
        assert sorted(zf.namelist()) == [".gitignore", "pkg/__init__.py", "pyproject.toml"]
        assert zf.read("pkg/__init__.py") == b"x = 1\n"


def _record(name: str, import_names: list[str], package_names: list[str]):
    return spu.PdkRecord(name=name, import_names=import_names, package_names=package_names)


def test_resolve_pdk_name_matches_by_package_name() -> None:
    catalog = [
        _record("acme", ["acme"], ["acme-gdsfactory"]),
        _record("cspdk", ["cspdk"], ["cspdk"]),
    ]
    assert spu.resolve_pdk_name(catalog, "acme", "acme-gdsfactory") == "acme"


def test_resolve_pdk_name_normalizes_package_names() -> None:
    catalog = [_record("acme", ["acme"], ["acme_gdsfactory"])]
    assert spu.resolve_pdk_name(catalog, "acme", "Acme-GDSFactory") == "acme"


def test_resolve_pdk_name_prefers_dedicated_row_over_umbrella() -> None:
    # An umbrella row lists every variant's import name, so
    # import_names membership alone matches both it and the dedicated row.
    catalog = [
        _record("fab18", ["fab18", "fab18.cband", "fab18.oband", "fab18.all"], ["fab18"]),
        _record("fab18.cband", ["fab18.cband"], ["fab18"]),
        _record("fab18.oband", ["fab18.oband"], ["fab18"]),
    ]
    assert spu.resolve_pdk_name(catalog, "fab18.cband", "fab18") == "fab18.cband"


def test_resolve_pdk_name_raises_when_nothing_matches() -> None:
    catalog = [_record("cspdk", ["cspdk"], ["cspdk"])]
    with pytest.raises(LookupError):
        spu.resolve_pdk_name(catalog, "acme", "acme-gdsfactory")


def test_read_pdk_import_name_reads_only_pdk_name_key() -> None:
    text = '[tool.gdsfactoryplus]\nname = "other"\npdk.name = "acme"\n'
    assert spu.read_pdk_import_name(text) == "acme"


@pytest.mark.parametrize("requirement", ["acme-gdsfactory[dev]~=0.4.0", "acme-gdsfactory>=0.4.0"])
def test_parse_exact_pin_ignores_lower_bound_pins(requirement: str) -> None:
    text = f'[project]\ndependencies = ["{requirement}", "uv"]\n'
    assert spu.parse_exact_pin(text, "acme-gdsfactory") is None


def test_parse_exact_pin_reads_double_equals_with_extras() -> None:
    text = '[project]\ndependencies = ["acme-gdsfactory[dev]==0.4.0"]\n'
    assert spu.parse_exact_pin(text, "acme-gdsfactory") == "0.4.0"


def test_parse_exact_pin_does_not_match_name_prefix() -> None:
    text = '[project]\ndependencies = ["fab18da==1.0.0"]\n'
    assert spu.parse_exact_pin(text, "fab18") is None


@pytest.mark.parametrize(
    ("release_version", "exact_pin", "installed", "expected"),
    [
        ("", None, "0.4.3", "0.4.3"),  # lower-bound pin: what was tested
        ("0.5.0", None, "0.5.0", "0.5.0"),
        ("", "4.0", "4.0.0", "4.0"),  # exact pin string, as the backfill keyed it
    ],
)
def test_upload_version(release_version: str, exact_pin: str | None, installed: str, expected: str) -> None:
    assert spu.upload_version(release_version, exact_pin, installed) == expected


@pytest.mark.parametrize(
    ("release_version", "exact_pin", "installed"),
    [("0.5.0", None, "0.4.3"), ("", "1.0.0", "1.0.1"), ("0.5.0", None, "")],
)
def test_upload_version_refuses_untested_version(release_version: str, exact_pin: str | None, installed: str) -> None:
    with pytest.raises(ValueError):
        spu.upload_version(release_version, exact_pin, installed)


@pytest.mark.parametrize(
    ("dependency", "expected"),
    [
        ("acme-gdsfactory[dev]~=0.4.0", "acme-gdsfactory"),
        ("fab18-cband==3.11.0", "fab18-cband"),
    ],
)
def test_find_pdk_distribution(dependency: str, expected: str) -> None:
    root = "acme-gdsfactory" if expected.startswith("acme") else "fab18"
    text = f'[project]\ndependencies = ["numpy", "{dependency}"]\n'
    assert spu.find_pdk_distribution(text, root) == expected


@pytest.mark.parametrize(
    ("target", "installed", "match"),
    [("0.4.0", "0.4.0", True), ("4.0", "4.0.0", True), ("0.4.0", "0.4.3", False), ("2.0.2", "2.0.1", False)],
)
def test_versions_match(target: str, installed: str, match: bool) -> None:
    assert spu.versions_match(target, installed) is match


def test_find_collisions_reports_dirs_sharing_a_portal_key() -> None:
    keys = {
        "x--sample-projects/cband--project": ("fab200", "fab200"),
        "x--sample-projects/oband--project": ("fab200", "fab200"),
        "x--sample-projects/other--project": ("fab50", "fab50"),
    }
    assert spu.find_collisions(keys) == {
        "x--sample-projects/cband--project",
        "x--sample-projects/oband--project",
    }
