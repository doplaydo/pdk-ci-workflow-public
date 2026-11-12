"""scripts/drc_project.py against a fake portal, and its use in the DRC workflows."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest
import yaml
from hooks._utils import apply_sync_markers

REPO_ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "drc_project", REPO_ROOT / "scripts/drc_project.py"
)
drc_project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drc_project)


class FakePortal:
    def __init__(self, projects=(), pdks=(), create_ok=True):
        self.projects = set(projects)
        self.pdks = list(pdks)
        self.create_ok = create_ok
        self.created = []

    def urlopen(self, request, timeout):
        assert request.get_header("X-api-key") == "key"
        url = request.full_url
        if "get_by_slug" in url:
            slug = url.rsplit("slug=", 1)[1]
            if slug not in self.projects:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return io.BytesIO(b'{"slug": "%s"}' % slug.encode())
        if url.endswith("v1/pdks/list"):
            return io.BytesIO(json.dumps({"data": self.pdks}).encode())
        if url.endswith("v2/projects/create"):
            body = json.loads(request.data)
            self.created.append(body)
            if not self.create_ok:
                raise urllib.error.HTTPError(
                    url, 422, "Unprocessable", {}, io.BytesIO(b"denied")
                )
            self.projects.add(body["slug"])
            return io.BytesIO(b"{}")
        raise AssertionError(url)


def run(monkeypatch, tmp_path, capsys, portal, *args, gfp="2.3.0", key="key"):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "My_PDK"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GFP_API_KEY", key)
    monkeypatch.setattr(drc_project.importlib.metadata, "version", lambda _: gfp)
    monkeypatch.setattr(drc_project.urllib.request, "urlopen", portal.urlopen)
    monkeypatch.setattr("sys.argv", ["drc_project.py", *args])
    code = 0
    try:
        drc_project.main()
    except SystemExit as exit:
        code = exit.code
    out, err = capsys.readouterr()
    return code, out.strip(), err


def test_skipped_before_gfp2(monkeypatch, tmp_path, capsys):
    portal = FakePortal()
    code, out, _ = run(
        monkeypatch, tmp_path, capsys, portal, "--slug", "ci-x", gfp="1.8.24"
    )
    assert (code, out, portal.created) == (0, "", [])


def test_reuses_existing_project(monkeypatch, tmp_path, capsys):
    portal = FakePortal(projects={"ci-my-pdk-all-cells"})
    code, out, _ = run(
        monkeypatch, tmp_path, capsys, portal, "--slug", "ci-My-PDK-all-cells"
    )
    assert (code, out, portal.created) == (0, "ci-my-pdk-all-cells", [])


@pytest.mark.parametrize(
    "args,package",
    [((), "my_pdk"), (("--distribution", "my-pdk-variant"), "My.Pdk_Variant")],
)
def test_creates_project_for_distribution(
    monkeypatch, tmp_path, capsys, args, package
):
    portal = FakePortal(
        pdks=[
            {"id": "other", "package_names": ["x"]},
            {"id": "p1", "package_names": [package]},
        ]
    )
    code, out, _ = run(monkeypatch, tmp_path, capsys, portal, "--slug", "ci-a", *args)
    assert (code, out) == (0, "ci-a")
    assert portal.created == [{"name": "ci-a", "slug": "ci-a", "pdk_id": "p1"}]


@pytest.mark.parametrize(
    "pdks,create_ok,key,error",
    [
        ([], True, "key", "No DRC PDK for My_PDK is available"),
        (
            [{"id": "p1", "package_names": ["my-pdk"]}],
            False,
            "key",
            "Creating DRC project ci-a failed: denied",
        ),
        ([], True, "", "GFP_API_KEY is not set"),
    ],
    ids=["no-pdk", "create-refused", "no-key"],
)
def test_errors(monkeypatch, tmp_path, capsys, pdks, create_ok, key, error):
    portal = FakePortal(pdks=pdks, create_ok=create_ok)
    code, out, err = run(
        monkeypatch, tmp_path, capsys, portal, "--slug", "ci-a", key=key
    )
    assert (code, out) == (1, "")
    assert f"::error::{error}" in err


def steps(workflow: str, job: str, keep: str) -> dict:
    path = REPO_ROOT / ".github/workflows" / workflow
    text = apply_sync_markers(path.read_text(), keep=keep)
    return {
        s.get("id") or s.get("name"): s
        for s in yaml.safe_load(text)["jobs"][job]["steps"]
    }


@pytest.mark.parametrize("keep", ["private", "public"])
def test_workflows_use_shared_script(keep):
    drc = steps("drc.yml", "drc", keep)
    assert "drc_project.py" in drc["Download helper scripts"]["run"]
    assert '--slug "ci-${GITHUB_REPOSITORY#*/}-all-cells"' in drc["drc-project"]["run"]
    sample = steps("sample-project-test.yml", "test", keep)
    assert "drc_project.py" in sample["scripts"]["run"]
    assert '"$RUNNER_TEMP/drc_project.py"' in sample["drc"]["run"]
    for run in (drc["drc-project"]["run"], sample["drc"]["run"]):
        assert "api.gdsfactory.com" not in run
