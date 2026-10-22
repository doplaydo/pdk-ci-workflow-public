"""Run scripts/build_cell.py against a fake PDK package, without gdsfactoryplus."""

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


def make_project(tmp_path: Path, pyproject: str, module: str = "fakepdk") -> Path:
    package = tmp_path
    for part in module.split("."):
        package /= part
        package.mkdir()
        (package / "__init__.py").touch()
    (package / "__init__.py").write_text(FAKE_PDK)
    (tmp_path / "pyproject.toml").write_text(pyproject)
    return tmp_path


def run(cwd: Path, *args: str, pythonpath: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "pyproject,module",
    [
        ('[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n', "fakepdk"),
        ('[tool.gdsfactoryplus]\nname = "fakepdk"\n', "fakepdk"),
        ('[tool.gdsfactoryplus.pdk]\nname = "fakepdk.sub"\n', "fakepdk.sub"),
        (
            '[tool.gdsfactoryplus]\nname = "other"\n\n[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n',
            "fakepdk",
        ),
    ],
)
def test_builds_cell_from_configured_pdk(
    tmp_path: Path, pyproject: str, module: str
) -> None:
    project = make_project(tmp_path, pyproject, module)
    nested = project / "notebooks"
    nested.mkdir()

    result = run(nested, "straight", pythonpath=project)

    assert result.returncode == 0, result.stderr
    assert (nested / "build/gds/straight.gds").read_text() == "straight"
    assert (nested / "activated.txt").read_text() == "yes"
    assert "gdsfactoryplus" not in result.stderr


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


@pytest.mark.parametrize(
    "pyproject",
    ["", "[tool.gdsfactoryplus]\nfoo = 1\n", '[tool.gdsfactoryplus.pdk]\nname = ""\n'],
)
def test_missing_pdk_name_fails_clearly(tmp_path: Path, pyproject: str) -> None:
    project = make_project(tmp_path, pyproject)

    result = run(project, "straight", pythonpath=project)

    assert result.returncode != 0
    assert "[tool.gdsfactoryplus.pdk] name" in result.stderr


def test_unknown_cell_fails(tmp_path: Path) -> None:
    project = make_project(tmp_path, '[tool.gdsfactoryplus.pdk]\nname = "fakepdk"\n')

    result = run(project, "nope", pythonpath=project)

    assert result.returncode == 1
    assert "cell 'nope' not found" in result.stderr
