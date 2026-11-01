"""Shared schematic coverage must not depend on Python factory discovery."""

import importlib.util
import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_schematics.py"
spec = importlib.util.spec_from_file_location("build_schematics", SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def archive(tmp_path, names):
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w") as output:
        for name in names:
            output.writestr(name, "{}")
    return path


def test_inventory_uses_all_committed_paths(tmp_path):
    path = archive(tmp_path, ["nested/a.gsch", "a.gsch", "_hidden/b.gsch", "cell.py"])
    assert ci.shipped_schematics(path) == ["_hidden/b.gsch", "a.gsch", "nested/a.gsch"]


@pytest.mark.parametrize(
    "results",
    [
        [],
        [{"path": "a.gsch", "status": "failed"}],
        [{"path": "other.gsch", "status": "passed"}],
        [{"path": "a.gsch", "status": "passed"}] * 2,
    ],
)
def test_rejects_omitted_failed_wrong_or_duplicate_results(results):
    assert not ci.validate_report(
        ["a.gsch"], {"discovered": ["a.gsch"], "results": results}
    )


def test_continues_after_failure_and_reports_each_path(tmp_path, monkeypatch):
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(ci.subprocess, "run", execute)
    out = tmp_path / "report.json"
    assert (
        ci.run(
            tmp_path, archive(tmp_path, ["a.gsch", "nested/a.gsch"]), out, "examplepdk"
        )
        == 1
    )
    report = json.loads(out.read_text())
    assert [r["status"] for r in report["results"]] == ["failed", "passed"]
    assert (
        calls[0][calls[0].index("--output") + 1]
        != calls[1][calls[1].index("--output") + 1]
    )


def test_no_schematics_is_explicit_success(tmp_path):
    out = tmp_path / "report.json"
    assert ci.run(tmp_path, archive(tmp_path, ["cell.py"]), out, "examplepdk") == 0
    assert json.loads(out.read_text()) == {"discovered": [], "results": []}


@pytest.mark.parametrize("empty", [True, False])
def test_editor_pipeline_and_recursive_geometry_check(tmp_path, monkeypatch, empty):
    calls = []

    def build(*args):
        calls.append(args)
        Path(args[1]).write_bytes(b"gds")

    monkeypatch.setitem(
        sys.modules,
        "gdsfactoryplus.compile",
        types.SimpleNamespace(build_nyancir_gds=build),
    )

    class Layout:
        def read(self, path):
            assert Path(path).read_bytes() == b"gds"

        def top_cells(self):
            return [
                types.SimpleNamespace(begin_shapes_rec=lambda layer: "recursive shapes")
            ]

        def layer_indexes(self):
            return [1]

    db = types.SimpleNamespace(
        Layout=Layout,
        Region=lambda shapes: types.SimpleNamespace(is_empty=lambda: empty),
    )
    monkeypatch.setitem(sys.modules, "klayout", types.ModuleType("klayout"))
    monkeypatch.setitem(sys.modules, "klayout.db", db)
    source, output = tmp_path / "a.gsch", tmp_path / "a.gds"
    output.write_bytes(b"stale")
    if empty:
        with pytest.raises(ValueError, match="empty geometry"):
            ci.build_one(source, output, "examplepdk")
    else:
        ci.build_one(source, output, "examplepdk")
    assert calls == [
        (
            str(source),
            str(output),
            "examplepdk.PDK",
            str(output.with_suffix(".dschematic")),
        )
    ]


def test_workflow_blocks_both_modes_on_schematic_checks():
    workflow = yaml.safe_load(
        (SCRIPT.parents[1] / ".github/workflows/sample-project-test.yml").read_text()
    )
    steps = {step.get("id"): step for step in workflow["jobs"]["test"]["steps"]}
    assert [name for name in steps if name and name.startswith("schematics")] == [
        "schematics"
    ]
    step = steps["schematics"]
    assert "if" not in step  # shared by editable and released installations
    assert step["timeout-minutes"] == 30
    assert "--archive" in step["run"]
    assert "'schematics'" in steps["result"]["run"]
    assert "validate_report(expected, schematic_report)" in steps["result"]["run"]
    assert "ok = ok and valid" in steps["result"]["run"]
