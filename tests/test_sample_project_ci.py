"""Behavioral tests for scripts/sample_project_ci.py and its CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import tomllib

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sample_project_ci.py"


@pytest.fixture
def ci():
    spec = importlib.util.spec_from_file_location("sample_project_ci", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "acme"\n')
    project = tmp_path / "acme--sample-projects/basic"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\ndependencies = ["acme-photonic[sim]~=1.0"]\n'
        '[tool.gdsfactoryplus]\nname = "not-the-import"\npdk.name = "acme.photonic"\n'
    )
    (project / "build").mkdir()
    (project / "build/committed.txt").write_text("keep me")
    git(tmp_path, "add", ".")
    # A developer's global gitignore may ignore build/; the archive contract
    # deliberately includes committed build output rather than stripping it.
    git(tmp_path, "add", "-f", "acme--sample-projects/basic/build/committed.txt")
    git(tmp_path, "commit", "-qm", "fixture")
    return tmp_path, project


def cli(*args, cwd, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_slug(ci):
    assert (
        ci.slug("acme--sample-projects/acme.x--project")
        == "acme--sample-projects-acme.x--project"
    )
    assert ci.slug("/acme/") == "acme"


def test_discover_finds_projects_and_skips_venv(ci, tmp_path):
    for directory in [
        "acme--sample-projects/b",
        "acme--sample-projects/a",
        "acme--sample-projects/.venv",
        ".venv/acme--sample-projects/a",
        "acme--sample-projects/deep/nested",
        "unrelated/a",
    ]:
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text("")
    assert ci.discover(tmp_path) == [
        {"slug": "acme--sample-projects-a", "project_dir": "acme--sample-projects/a"},
        {"slug": "acme--sample-projects-b", "project_dir": "acme--sample-projects/b"},
    ]


@pytest.mark.parametrize(
    "requirement,root,expected",
    [
        ("acme-photonic[sim]~=1.0", "acme", "acme-photonic"),
        ("Acme_Photonic >= 1.0", "ACME", "acme-photonic"),
        ("Acme.Photonic@ https://example.com/archive.zip", "acme", "acme-photonic"),
        ("acme; python_version >= '3.9'", "acme", "acme"),
    ],
)
def test_pdk_distribution_matches_root_and_variant(ci, requirement, root, expected):
    text = "[project]\ndependencies = " + json.dumps([requirement])
    assert ci.pdk_distribution(text, root) == expected


def test_pdk_distribution_absent(ci):
    with pytest.raises(LookupError):
        ci.pdk_distribution('[project]\ndependencies = ["acmeother", "numpy"]', "acme")


def test_pdk_distribution_editable_pdk_project_is_its_own_pdk(ci):
    text = '[project]\nname = "qpdk"\n[tool.gdsfactoryplus]\npdk.name = "qpdk"\n'
    assert ci.pdk_distribution(text, "qpdk") == "qpdk"
    with pytest.raises(LookupError):
        ci.pdk_distribution(text.replace('name = "qpdk"', 'name = "x"', 1), "qpdk")
    with pytest.raises(LookupError):
        ci.pdk_distribution(text, "other")
    with pytest.raises(LookupError):
        ci.pdk_distribution('[project]\nname = "qpdk"\n', "qpdk")


def test_pdk_distribution_editable_pdk_project_import_name_may_differ(ci):
    text = (
        '[project]\nname = "ihp-gdsfactory"\n[tool.gdsfactoryplus]\npdk.name = "ihp"\n'
    )
    assert ci.pdk_distribution(text, "ihp-gdsfactory") == "ihp-gdsfactory"


@pytest.mark.parametrize(
    "root,distribution,expected",
    [
        ("acme", "acme-photonic", "photonic"),
        ("acme", "acme", None),
        ("Acme_Pdk", "acme.pdk-high-speed", "high-speed"),
        ("acme", "other", None),
    ],
)
def test_variant(ci, root, distribution, expected):
    assert ci.variant(root, distribution) == expected


def test_package_writes_zip_of_committed_files_and_meta(ci, repo, tmp_path):
    root, project = repo
    (project / "untracked.txt").write_text("not archived")
    (root / "pyproject.toml").write_text('[project]\nname = "wrong"\n')
    (project / "pyproject.toml").write_text('[project]\nname = "dirty"\n')
    out = tmp_path / "out"
    meta = ci.package(
        root, "acme--sample-projects/basic", out, "example/repo", "source-sha"
    )
    assert meta == {
        "project_dir": "acme--sample-projects/basic",
        "slug": "acme--sample-projects-basic",
        "pdk_distribution": "acme-photonic",
        "pdk_import_name": "acme.photonic",
        "tree_sha": git(root, "rev-parse", "HEAD:acme--sample-projects/basic"),
        "source_repo": "example/repo",
        "source_sha": "source-sha",
    }
    assert json.loads((out / "meta.json").read_text()) == meta
    with zipfile.ZipFile(out / "sample-project.zip") as archive:
        assert sorted(archive.namelist()) == [
            "build/",
            "build/committed.txt",
            "pyproject.toml",
        ]
        assert archive.read("build/committed.txt") == b"keep me"
        assert b"acme-photonic" in archive.read("pyproject.toml")


def test_package_requires_pdk_import_name(ci, repo, tmp_path):
    root, project = repo
    (project / "pyproject.toml").write_text('[project]\ndependencies = ["acme"]\n')
    git(root, "add", ".")
    git(root, "commit", "-qm", "missing import name")
    with pytest.raises(LookupError):
        ci.package(
            root, "acme--sample-projects/basic", tmp_path / "out", "example/repo", "sha"
        )


def test_set_editable_source_replaces_existing_entry_keeps_others(ci):
    text = '# keep comment\n[tool.uv.sources]\nacme = {path="../.."}\nother = {index="x"}\n'
    edited = ci.set_editable_source(text, "acme", "/checkout")
    assert tomllib.loads(edited)["tool"]["uv"]["sources"] == {
        "acme": {"path": "/checkout", "editable": True},
        "other": {"index": "x"},
    }
    assert "# keep comment" in edited


def test_set_editable_source_creates_table(ci):
    edited = ci.set_editable_source(
        '[project]\nname="sample"\n', "acme-photonic", "/checkout"
    )
    assert tomllib.loads(edited)["tool"]["uv"]["sources"]["acme-photonic"] == {
        "path": "/checkout",
        "editable": True,
    }


def test_add_gfp_constraint_appends(ci):
    edited = ci.add_gfp_constraint(
        '[tool.uv]\nconstraint-dependencies=["numpy<3"]\n', "1.2.3"
    )
    assert tomllib.loads(edited)["tool"]["uv"]["constraint-dependencies"] == [
        "numpy<3",
        "gdsfactoryplus==1.2.3",
    ]
    assert tomllib.loads(ci.add_gfp_constraint("", "2.0"))["tool"]["uv"][
        "constraint-dependencies"
    ] == ["gdsfactoryplus==2.0"]


def test_select_notebooks_excludes_patterns(ci):
    assert ci.select_notebooks(
        [
            "a_meep.ipynb",
            "hfss_x.ipynb",
            "b.ipynb",
            "hfss-x.ipynb",
            "matlab_x.ipynb",
            "matlab-x.ipynb",
            "meep_x.ipynb",
        ]
    ) == (
        ["b.ipynb"],
        [
            "a_meep.ipynb",
            "hfss_x.ipynb",
            "hfss-x.ipynb",
            "matlab_x.ipynb",
            "matlab-x.ipynb",
            "meep_x.ipynb",
        ],
    )


def test_skip_execution_cells_counts_tagged_cells(ci):
    assert (
        ci.skip_execution_cells(
            {
                "cells": [
                    {"metadata": {"tags": ["skip-execution"]}},
                    {"metadata": {"tags": ["other", "skip-execution"]}},
                    {"metadata": {}},
                    {},
                ]
            }
        )
        == 2
    )
    assert ci.skip_execution_cells({}) == 0


def test_gfp_test_skips_parses_skipped_lines(ci):
    # Real pytest -v output, probed during Task 1 Step 0 (pytest 9.1.1).
    line = "test_skip_probe.py::test_build[requires_argument] SKIPPED (Skipping ...) [100%]"
    assert ci.gfp_test_skips(
        f"collected 2 items\n  {line}  \ncell PASSED [50%]\n1 skipped\n"
    ) == [line]


def test_render_summary_lists_failures_and_exclusions(ci):
    summary = ci.render_summary(
        {"build": "success", "notebooks": "failure"},
        ["hfss_x.ipynb", "one tagged cell"],
    )
    assert "| Check | Result | Notes |" in summary
    assert "| build | success |" in summary
    assert "| notebooks | failure |" in summary
    assert "- hfss_x.ipynb" in summary
    assert "- one tagged cell" in summary


def test_cli_discover_package_edit_and_summary(repo, tmp_path):
    root, project = repo
    result = cli("discover", cwd=root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {
            "slug": "acme--sample-projects-basic",
            "project_dir": "acme--sample-projects/basic",
        }
    ]
    out = tmp_path / "out"
    result = cli(
        "package",
        "acme--sample-projects/basic",
        "--out",
        out,
        "--source-repo",
        "example/repo",
        "--source-sha",
        "sha",
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((out / "meta.json").read_text())["source_sha"] == "sha"
    result = cli(
        "edit-pyproject",
        project / "pyproject.toml",
        "--editable-source",
        "acme-photonic=/checkout",
        "--gfp-version",
        "1.2.3",
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    data = tomllib.loads((project / "pyproject.toml").read_text())["tool"]["uv"]
    assert data["sources"]["acme-photonic"] == {"path": "/checkout", "editable": True}
    assert data["constraint-dependencies"] == ["gdsfactoryplus==1.2.3"]
    exclusions = tmp_path / "exclusions.json"
    exclusions.write_text('["gfp cell SKIPPED"]')
    notebooks = tmp_path / "notebooks.json"
    notebooks.write_text(
        json.dumps(
            {
                "run": ["a.ipynb"],
                "excluded": ["hfss_x.ipynb"],
                "failed": [],
                "skip_execution": {"a.ipynb": 2},
            }
        )
    )
    result = cli(
        "summary",
        "--checks",
        '{"test":"failure"}',
        "--notebooks",
        notebooks,
        "--exclusions",
        exclusions,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    for text in ["failure", "gfp cell SKIPPED", "hfss_x.ipynb", "a.ipynb", "2"]:
        assert text in result.stdout


@pytest.mark.parametrize("report_location", ["explicit", "runner", "system"])
def test_cli_run_notebooks_collects_all_failures_and_counts(tmp_path, report_location):
    project = tmp_path / "project"
    project.mkdir()
    for name in ["a.ipynb", "b.ipynb", "c.ipynb", "hfss_x.ipynb"]:
        (project / name).write_text(
            json.dumps({"cells": [{"metadata": {"tags": ["skip-execution"]}}]})
        )
    # nbconvert is external and expensive. A real executable records its exact
    # command/cwd and simulates failure for two notebooks to verify continuation.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f'#!{sys.executable}\nimport json, os, sys\nwith open(os.environ["CALLS"], "a") as f:\n f.write(json.dumps([os.getcwd(), sys.argv[1:]]) + "\\n")\nprint("diagnostic", sys.argv[-1])\nsys.exit(0 if sys.argv[-1] == "c.ipynb" else 1)\n'
    )
    uv.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        CALLS=str(tmp_path / "calls"),
    )
    runner = tmp_path / "runner"
    env["RUNNER_TEMP"] = str(runner)
    args = ["run-notebooks", str(project)]
    report = runner / "notebooks.json"
    if report_location == "explicit":
        report = tmp_path / "reports/custom.json"
        args += ["--out", str(report)]
    elif report_location == "system":
        env.pop("RUNNER_TEMP", None)
        # Isolate the system-temp fallback in this child process.
        env["TMPDIR"] = str(tmp_path)
        report = tmp_path / "notebooks.json"
    result = cli(*args, cwd=tmp_path, env=env)
    assert result.returncode == 1, result.stderr
    data = json.loads(report.read_text())
    assert data == {
        "run": ["a.ipynb", "b.ipynb", "c.ipynb"],
        "excluded": ["hfss_x.ipynb"],
        "failed": ["a.ipynb", "b.ipynb"],
        "skip_execution": {"a.ipynb": 1, "b.ipynb": 1, "c.ipynb": 1, "hfss_x.ipynb": 1},
    }
    assert "a.ipynb" in result.stdout + result.stderr
    assert "b.ipynb" in result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    output_root = tmp_path if report_location == "system" else runner
    assert calls == [
        [
            str(project),
            [
                "run",
                "--no-sync",
                "jupyter",
                "nbconvert",
                "--to",
                "notebook",
                "--execute",
                "--output-dir",
                str(output_root / "nb"),
                "--ExecutePreprocessor.timeout=600",
                name,
            ],
        ]
        for name in ["a.ipynb", "b.ipynb", "c.ipynb"]
    ]
    assert not (project / "notebooks.json").exists()


def test_cli_run_notebooks_empty_project_succeeds(tmp_path):
    report = tmp_path / "report.json"
    result = cli("run-notebooks", tmp_path, "--out", report, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text()) == {
        "run": [],
        "excluded": [],
        "failed": [],
        "skip_execution": {},
    }


@pytest.mark.parametrize("alias", ["Acme-Pdk", "acme_pdk", "acme.pdk"])
def test_set_editable_source_replaces_normalized_aliases(ci, alias):
    text = (
        "# project comment\n[tool.uv.sources]\n"
        f'"{alias}" = {{path="../.."}}\n'
        'acme-pdk = {index="old"}\n'
        '# unrelated source\nother = {index="x"} # keep inline\n'
    )
    edited = ci.set_editable_source(text, "acme-pdk", "/checkout")
    assert tomllib.loads(edited)["tool"]["uv"]["sources"] == {
        "acme-pdk": {"path": "/checkout", "editable": True},
        "other": {"index": "x"},
    }
    for comment in ["# project comment", "# unrelated source", "# keep inline"]:
        assert comment in edited


def test_cli_run_notebooks_prunes_dependency_venvs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "demo.ipynb").write_text('{"cells": []}')
    for directory in [".venv/lib/vendor", "nested/.venv/lib/vendor"]:
        vendor = project / directory
        vendor.mkdir(parents=True)
        (vendor / "failing.ipynb").write_text("not a notebook")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        'with open(os.environ["CALLS"], "a") as f: f.write(sys.argv[-1] + "\\n")\n'
        'sys.exit(0 if sys.argv[-1] == "demo.ipynb" else 1)\n'
    )
    uv.chmod(0o755)
    report = tmp_path / "report.json"
    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        CALLS=str(tmp_path / "calls"),
        RUNNER_TEMP=str(tmp_path / "runner"),
    )
    result = cli("run-notebooks", project, "--out", report, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text() == "demo.ipynb\n"
    assert json.loads(report.read_text()) == {
        "run": ["demo.ipynb"],
        "excluded": [],
        "failed": [],
        "skip_execution": {"demo.ipynb": 0},
    }
