"""Regression tests for reference fixtures found in released PDK wheels."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest
import yaml
from hooks._utils import apply_sync_markers

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_wheel_contents.py"
spec = importlib.util.spec_from_file_location("check_wheel_contents", SCRIPT)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def build_wheel(directory: Path, paths: list[str], name: str = "pdk.whl") -> Path:
    wheel = directory / name
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in paths:
            archive.writestr(path, b"fixture")
    return wheel


@pytest.mark.parametrize(
    "path",
    [
        "references/vendor.gds",
        "tps/references/gds/wg_cross_CDNS_7294944683378.gds",
        "ph18da/data/references/ph18da_all_device_sample_v3.7.gds",
        "pdk/Refs/vendor.pdf",
        "pdk/pdk-reference-files/vendor.zip",
        "pdk/gds_ref/cell.gds",
        "pdk\\references\\vendor.gds",
        "sky130/src/sky130_fd_pr/.git",
        "pdk/.git/config",
        "pdk/references/",
    ],
)
def test_rejects_reference_and_git_paths(tmp_path: Path, path: str) -> None:
    wheel = build_wheel(tmp_path, [path])
    assert checker.main([str(wheel)]) == 1
    assert path in checker.check_wheel(wheel)[0]


def test_allows_runtime_assets_inside_pdk(tmp_path: Path) -> None:
    wheel = build_wheel(
        tmp_path,
        [
            "pdk/gds/cell.gds",
            "pdk/models/data/model.xlsx",
            "pdk/models/data/straight_modes.csv.gz",
            "pdk/data/reference_models.json",
            "pdk/layers.yaml",
            "pdk-1.0.dist-info/RECORD",
            "pdk--sample-projects/demo/build/models.nyanlib",
        ],
    )
    assert checker.main([str(wheel)]) == 0


def test_checks_every_platform_wheel(tmp_path: Path) -> None:
    build_wheel(tmp_path, ["pdk/gds/cell.gds"], "pdk-linux.whl")
    build_wheel(tmp_path, ["pdk/references/vendor.gds"], "pdk-windows.whl")
    assert checker.main([str(tmp_path)]) == 1


def test_requires_a_built_wheel(tmp_path: Path) -> None:
    assert checker.main([str(tmp_path)]) == 1
    assert checker.main([str(tmp_path / "missing.whl")]) == 1


def test_corrupt_wheel_fails(tmp_path: Path) -> None:
    wheel = tmp_path / "corrupt.whl"
    wheel.write_bytes(b"not a zip archive")
    assert checker.main([str(wheel)]) == 1


@pytest.mark.parametrize(
    "section", ["tool.hatch.build", "tool.hatch.build.targets.wheel"]
)
@pytest.mark.parametrize(
    "mapping_name",
    ["force-include", "sources", "shared-data", "shared-scripts", "extra-metadata"],
)
@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("references/gds", "pdk/gds"),
        ("pdk/gds", "pdk/references/gds"),
        ("vendor/.git", "pdk/data"),
    ],
)
def test_rejects_build_mapping_remapping(
    tmp_path: Path, section: str, mapping_name: str, source: str, destination: str
) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(f'[{section}.{mapping_name}]\n"{source}" = "{destination}"\n')
    wheel = build_wheel(tmp_path, ["pdk/gds/cell.gds"])
    assert checker.main(["--pyproject", str(config), str(wheel)]) == 1


@pytest.mark.parametrize(
    "section", ["tool.hatch.build", "tool.hatch.build.targets.wheel"]
)
@pytest.mark.parametrize(
    ("sources", "exit_code"), [('["src"]', 0), ('["references"]', 1)]
)
def test_handles_source_prefix_list(
    tmp_path: Path, section: str, sources: str, exit_code: int
) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(f"[{section}]\nsources = {sources}\n")
    wheel = build_wheel(tmp_path, ["pdk/gds/cell.gds"])
    assert checker.main(["--pyproject", str(config), str(wheel)]) == exit_code


def test_allows_runtime_force_include(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        '[tool.hatch.build.targets.wheel.force-include]\n"pdk/gds" = "pdk/gds"\n'
    )
    wheel = build_wheel(tmp_path, ["pdk/gds/cell.gds"])
    assert checker.main(["--pyproject", str(config), str(wheel)]) == 0


def test_invalid_config_fails(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text("[invalid")
    wheel = build_wheel(tmp_path, ["pdk/gds/cell.gds"])
    assert checker.main(["--pyproject", str(config), str(wheel)]) == 1


@pytest.mark.parametrize("audience", ["private", "public"])
def test_routine_ci_builds_and_checks_wheel_without_skipping(audience: str) -> None:
    source = ROOT / ".github" / "workflows" / "test_code.yml"
    workflow = yaml.safe_load(apply_sync_markers(source.read_text(), keep=audience))
    job = workflow["jobs"]["wheel-contents"]
    assert "if" not in job
    steps = job["steps"]
    build = next(
        index
        for index, step in enumerate(steps)
        if step.get("run", "").startswith("uv build")
    )
    check = next(
        index
        for index, step in enumerate(steps)
        if "actions/check_wheel_contents@" in step.get("uses", "")
    )
    assert build < check
    assert "continue-on-error" not in steps[check]
    assert "if" not in steps[check]


