"""Run scripts/build_cell.py against a fake PDK package and a stub gdsfactoryplus.

The stub's ``get_pdk`` mirrors the upstream contract (nearest pyproject.toml,
``[tool.gdsfactoryplus.pdk] name``), so these tests cover the script's own
logic, not gdsfactoryplus itself.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_cell.py"

FAKE_PDK = textwrap.dedent(
    """\
    from pathlib import Path


    class _Component:
        def __init__(self, name):
            self.name = name

        def write_gds(self, path):
            Path(path).write_text(self.name)


    def straight():
        return _Component("straight")


    class _Pdk:
        cells = {"straight": straight}

        def activate(self):
            Path("activated.txt").write_text("yes")


    PDK = _Pdk()
    """
)


STUB_GFP = textwrap.dedent(
    """\
    import tomllib
    from importlib import import_module
    from pathlib import Path


    def get_pdk():
        cwd = Path.cwd()
        for directory in (cwd, *cwd.parents):
            pyproject = directory / "pyproject.toml"
            if pyproject.is_file():
                config = tomllib.loads(pyproject.read_text())
                name = config["tool"]["gdsfactoryplus"]["pdk"]["name"]
                break
        Path("stub_get_pdk_called.txt").write_text(name)
        pdk = import_module(name).PDK
        pdk.activate()
        return pdk
    """
)


def make_project(tmp_path: Path, pyproject: str, module: str = "fakepdk") -> Path:
    package = tmp_path
    for part in module.split("."):
        package /= part
        package.mkdir()
        (package / "__init__.py").touch()
    (package / "__init__.py").write_text(FAKE_PDK)
    (tmp_path / "pyproject.toml").write_text(pyproject)
    return tmp_path


def make_stub_gfp(tmp_path: Path, source: str = STUB_GFP) -> Path:
    stub_dir = tmp_path / "_stub_site"
    (stub_dir / "gdsfactoryplus").mkdir(parents=True)
    (stub_dir / "gdsfactoryplus/__init__.py").write_text(source)
    return stub_dir


def run(
    cwd: Path, *args: str, pythonpath: Path, gfp_source: str = STUB_GFP
) -> subprocess.CompletedProcess:
    stub_dir = make_stub_gfp(pythonpath, gfp_source)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(stub_dir), str(pythonpath)]),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("module", ["fakepdk", "fakepdk.sub"])
def test_builds_cell_via_gdsfactoryplus_get_pdk(tmp_path: Path, module: str) -> None:
    project = make_project(
        tmp_path, f'[tool.gdsfactoryplus.pdk]\nname = "{module}"\n', module
    )
    nested = project / "notebooks"
    nested.mkdir()

    result = run(nested, "straight", pythonpath=project)

    assert result.returncode == 0, result.stderr
    assert (nested / "build/gds/straight.gds").read_text() == "straight"
    assert (nested / "stub_get_pdk_called.txt").read_text() == module
    assert (nested / "activated.txt").read_text() == "yes"


def test_rglob_fallback_for_unregistered_cell(tmp_path: Path) -> None:
    project = make_project(tmp_path, '[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n')
    (project / "samples").mkdir()
    (project / "samples/sample_drc_errors.py").write_text(
        "from fakepdk import _Component\n\n\ndef sample_drc_errors():\n"
        '    return _Component("sample_drc_errors")\n'
    )

    result = run(project, "sample_drc_errors", pythonpath=project)

    assert result.returncode == 0, result.stderr
    assert (
        project / "build/gds/sample_drc_errors.gds"
    ).read_text() == "sample_drc_errors"


def test_gdsfactoryplus_without_top_level_get_pdk_fails(tmp_path: Path) -> None:
    """gdsfactoryplus releases without the top-level export fail at import."""
    project = make_project(tmp_path, '[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n')

    result = run(project, "straight", pythonpath=project, gfp_source="")

    assert result.returncode != 0
    assert "cannot import name 'get_pdk' from 'gdsfactoryplus'" in result.stderr


def test_unknown_cell_fails(tmp_path: Path) -> None:
    project = make_project(tmp_path, '[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n')

    result = run(project, "nope", pythonpath=project)

    assert result.returncode == 1
    assert "cell 'nope' not found" in result.stderr


STUB_GF = textwrap.dedent(
    """\
    from pathlib import Path


    class Component:
        def __init__(self, name="all_cells"):
            self.name = name

        def write_gds(self, path):
            Path(path).write_text(self.name)


    def pack(components, spacing):
        Path("pack_spacing.txt").write_text(repr(spacing))
        return [Component()]
    """
)


def run_all_cells(project: Path, *args: str) -> subprocess.CompletedProcess:
    stub_gf = project / "_stub_gf" / "gdsfactory"
    stub_gf.mkdir(parents=True)
    (stub_gf / "__init__.py").write_text(STUB_GF)
    stub_dir = make_stub_gfp(project)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(stub_dir), str(stub_gf.parent), str(project)]
        ),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), "all_cells", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


PDK_SECTION = '[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n'


@pytest.mark.parametrize(
    ("extra", "args", "expected"),
    [
        ("", (), 100.0),
        ("\n[tool.pdk-ci-workflow.drc]\npack_spacing = 1000\n", (), 1000.0),
        ("\n[tool.pdk-ci-workflow.drc]\npack_spacing = 250.5\n", (), 250.5),
        (
            "\n[tool.pdk-ci-workflow.drc]\npack_spacing = 1000\n",
            ("--spacing", "50"),
            50.0,
        ),
        ('\n[tool.pdk-ci-workflow.release]\ntarget = "none"\n', (), 100.0),
    ],
    ids=["default", "configured", "float", "cli-overrides", "other-section"],
)
def test_all_cells_pack_spacing(
    tmp_path: Path, extra: str, args: tuple[str, ...], expected: float
) -> None:
    project = make_project(tmp_path, PDK_SECTION + extra)

    result = run_all_cells(project, *args)

    assert result.returncode == 0, result.stderr
    assert float((project / "pack_spacing.txt").read_text()) == expected
    assert (project / "build/gds/all_cells.gds").read_text() == "all_cells"


@pytest.mark.parametrize("value", ["0", "-5", "true", '"wide"', "inf", "nan"])
def test_all_cells_rejects_invalid_pack_spacing(tmp_path: Path, value: str) -> None:
    project = make_project(
        tmp_path,
        PDK_SECTION + f"\n[tool.pdk-ci-workflow.drc]\npack_spacing = {value}\n",
    )

    result = run_all_cells(project)

    assert result.returncode != 0
    assert "pack_spacing" in result.stderr
    assert not (project / "pack_spacing.txt").exists()
    # Fails fast: before the PDK is loaded and its cells are built.
    assert not (project / "stub_get_pdk_called.txt").exists()


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
def test_all_cells_rejects_invalid_cli_spacing(tmp_path: Path, value: str) -> None:
    project = make_project(tmp_path, PDK_SECTION)

    result = run_all_cells(project, "--spacing", value)

    assert result.returncode != 0
    assert "--spacing" in result.stderr
    assert not (project / "stub_get_pdk_called.txt").exists()
