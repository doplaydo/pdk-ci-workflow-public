"""Tests for portal key resolution and the tested-archive upload seam."""

from __future__ import annotations

import importlib.util
import json
import subprocess
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


def _record(name: str, import_names: list[str], package_names: list[str]):
    return spu.PdkRecord(
        name=name, import_names=import_names, package_names=package_names
    )


def test_resolve_pdk_name_matches_by_package_name() -> None:
    catalog = [
        _record("acme", ["acme"], ["acme-gdsfactory"]),
        _record("examplepdk", ["examplepdk"], ["examplepdk"]),
    ]
    assert spu.resolve_pdk_name(catalog, "acme", "acme-gdsfactory") == "acme"


def test_resolve_pdk_name_normalizes_package_names() -> None:
    catalog = [_record("acme", ["acme"], ["acme_gdsfactory"])]
    assert spu.resolve_pdk_name(catalog, "acme", "Acme-GDSFactory") == "acme"


def test_resolve_pdk_name_prefers_dedicated_row_over_umbrella() -> None:
    # An umbrella row lists every variant's import name, so
    # import_names membership alone matches both it and the dedicated row.
    catalog = [
        _record(
            "examplepdk",
            ["examplepdk", "examplepdk.cband", "examplepdk.oband", "examplepdk.all"],
            ["examplepdk"],
        ),
        _record("examplepdk.cband", ["examplepdk.cband"], ["examplepdk"]),
        _record("examplepdk.oband", ["examplepdk.oband"], ["examplepdk"]),
    ]
    assert (
        spu.resolve_pdk_name(catalog, "examplepdk.cband", "examplepdk")
        == "examplepdk.cband"
    )


def test_resolve_pdk_name_raises_when_nothing_matches() -> None:
    catalog = [_record("examplepdk", ["examplepdk"], ["examplepdk"])]
    with pytest.raises(LookupError):
        spu.resolve_pdk_name(catalog, "acme", "acme-gdsfactory")


def test_read_pdk_import_name_reads_only_pdk_name_key() -> None:
    text = '[tool.gdsfactoryplus]\nname = "other"\npdk.name = "acme"\n'
    assert spu.read_pdk_import_name(text) == "acme"


@pytest.mark.parametrize(
    "requirement", ["acme-gdsfactory[dev]~=0.4.0", "acme-gdsfactory>=0.4.0"]
)
def test_parse_exact_pin_ignores_lower_bound_pins(requirement: str) -> None:
    text = f'[project]\ndependencies = ["{requirement}", "uv"]\n'
    assert spu.parse_exact_pin(text, "acme-gdsfactory") is None


def test_parse_exact_pin_reads_double_equals_with_extras() -> None:
    text = '[project]\ndependencies = ["acme-gdsfactory[dev]==0.4.0"]\n'
    assert spu.parse_exact_pin(text, "acme-gdsfactory") == "0.4.0"


def test_parse_exact_pin_does_not_match_name_prefix() -> None:
    text = '[project]\ndependencies = ["examplepdkextra==1.0.0"]\n'
    assert spu.parse_exact_pin(text, "examplepdk") is None


@pytest.mark.parametrize(
    ("exact_pin", "installed", "expected"),
    [("1.2", "1.2", "1.2"), (None, "1.3", "1.3"), ("4.0", "4.0.0", "4.0")],
)
def test_upload_version(exact_pin: str | None, installed: str, expected: str) -> None:
    assert spu.upload_version(exact_pin, installed) == expected


@pytest.mark.parametrize("exact_pin,installed", [("1.2", "1.3"), (None, "")])
def test_upload_version_refuses_untested_version(
    exact_pin: str | None, installed: str
) -> None:
    with pytest.raises(ValueError):
        spu.upload_version(exact_pin, installed)


@pytest.mark.parametrize(
    ("target", "installed", "match"),
    [
        ("0.4.0", "0.4.0", True),
        ("4.0", "4.0.0", True),
        ("0.4.0", "0.4.3", False),
        ("2.0.2", "2.0.1", False),
    ],
)
def test_versions_match(target: str, installed: str, match: bool) -> None:
    assert spu.versions_match(target, installed) is match


def test_find_collisions_reports_dirs_sharing_a_portal_key() -> None:
    keys = {
        "x--sample-projects/cband--project": ("examplepdk", "examplepdk"),
        "x--sample-projects/oband--project": ("examplepdk", "examplepdk"),
        "x--sample-projects/other--project": ("acme", "acme"),
    }
    assert spu.find_collisions(keys) == {
        "x--sample-projects/cband--project",
        "x--sample-projects/oband--project",
    }


SAMPLE = '[project]\ndependencies = ["examplepdk==1.2"]\n[tool.gdsfactoryplus.pdk]\nname = "acme"\n'
PROJECT_DIR = "examplepdk--sample-projects/acme--project"


@pytest.fixture
def upload_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    project = repo / PROJECT_DIR
    project.mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "examplepdk"\n')
    (project / "pyproject.toml").write_text("not the tested pyproject")
    archive = tmp_path / "sample-project.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pyproject.toml", SAMPLE)
        zf.writestr("build/committed.txt", "keep in portal")
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "project_dir": PROJECT_DIR,
                "slug": PROJECT_DIR.replace("/", "-"),
                "pdk_distribution": "examplepdk",
                "pdk_import_name": "acme",
                "tree_sha": "abc",
                "source_repo": "example/acme",
                "source_sha": "def",
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GFP_API_KEY", "fake-key")
    monkeypatch.setattr(
        spu, "list_pdks", lambda key: [_record("acme", ["acme"], ["examplepdk"])]
    )
    monkeypatch.setattr(
        spu, "_gh", lambda endpoint, **kwargs: b'[ {"workflow_runs": []} ]'
    )
    uploads = []
    monkeypatch.setattr(spu, "upload", lambda *args: uploads.append(args) or "ok")
    argv = [
        str(SCRIPT),
        "upload",
        str(archive),
        "--source-run-id",
        "10",
        "--meta",
        str(meta),
        "--installed-version",
        "1.2",
        "--repo-root",
        str(repo),
    ]
    return repo, archive, argv, uploads


def test_upload_sends_tested_zip_bytes_as_is(
    upload_case, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, archive, argv, uploads = upload_case
    monkeypatch.setattr(sys, "argv", argv)
    spu.main()
    assert uploads == [("fake-key", "acme", "acme", "1.2", archive.read_bytes())]


@pytest.mark.parametrize("dry_run", [False, True])
def test_refuses_pdk_name_not_allowed(
    upload_case, monkeypatch: pytest.MonkeyPatch, capsys, dry_run: bool
) -> None:
    _, _, argv, uploads = upload_case
    monkeypatch.setattr(
        sys,
        "argv",
        argv
        + ["--allowed-pdk-names", "examplepdk"]
        + (["--dry-run"] if dry_run else []),
    )
    with pytest.raises(SystemExit) as exc:
        spu.main()
    assert exc.value.code == 1
    assert "::error::" in capsys.readouterr().out
    assert uploads == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_refuses_explicit_empty_allowlist(
    upload_case, monkeypatch: pytest.MonkeyPatch, capsys, dry_run: bool
) -> None:
    _, _, argv, uploads = upload_case
    monkeypatch.setattr(
        sys,
        "argv",
        argv + ["--allowed-pdk-names", ""] + (["--dry-run"] if dry_run else []),
    )
    with pytest.raises(SystemExit) as exc:
        spu.main()
    assert exc.value.code == 1
    assert "::error::" in capsys.readouterr().out
    assert uploads == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_collision_check_reads_repo_root(
    upload_case, monkeypatch: pytest.MonkeyPatch, capsys, dry_run: bool
) -> None:
    repo, _, argv, uploads = upload_case
    sibling = repo / "examplepdk--sample-projects/other--project"
    sibling.mkdir()
    (sibling / "pyproject.toml").write_text(SAMPLE)
    monkeypatch.setattr(sys, "argv", argv + (["--dry-run"] if dry_run else []))
    with pytest.raises(SystemExit) as exc:
        spu.main()
    assert exc.value.code == 1
    assert "overwrite" in capsys.readouterr().out
    assert uploads == []


def test_dry_run_resolves_without_post(
    upload_case, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _, _, argv, uploads = upload_case
    monkeypatch.setattr(
        sys, "argv", argv + ["--allowed-pdk-names", "examplepdk,acme", "--dry-run"]
    )
    spu.main()
    output = capsys.readouterr().out
    for value in [
        "dry-run",
        "pdk_name=acme",
        "pdk_import_name=acme",
        "version=1.2",
        "examplepdk,acme",
        "collisions=none",
    ]:
        assert value in output
    assert uploads == []


def test_dry_run_refuses_untested_version(
    upload_case, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _, _, argv, uploads = upload_case
    argv[argv.index("--installed-version") + 1] = "1.3"
    monkeypatch.setattr(sys, "argv", argv + ["--dry-run"])
    with pytest.raises(SystemExit) as exc:
        spu.main()
    assert exc.value.code == 1
    assert "tested against" in capsys.readouterr().out
    assert uploads == []


def test_upload_helper_does_not_require_tomlkit() -> None:
    code = f"""
import sys
sys.modules['tomlkit'] = None
sys.path.insert(0, {str(SCRIPT.parent)!r})
import sample_project_upload
import sample_project_ci
assert sample_project_ci.pdk_distribution({SAMPLE!r}, 'examplepdk') == 'examplepdk'
"""
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.fixture
def freshness(upload_case, monkeypatch):
    """Newer run finishes first; the older candidate still holds its passed ZIP."""
    import io

    _, _, argv, uploads = upload_case
    repo = "example/acme"
    slug = PROJECT_DIR.replace("/", "-")
    newer = {
        "id": 20,
        "head_sha": "new-sha",
        "event": "push",
        "head_branch": "main",
        "head_repository": {"full_name": repo},
        "status": "completed",
        "conclusion": "failure",
    }
    meta = json.loads(Path(argv[argv.index("--meta") + 1]).read_text())
    meta["source_sha"] = "new-sha"
    artifacts = [
        {"id": 101, "name": f"sample-project-tested-released-{slug}", "expired": False}
    ]
    calls = []

    def gh(endpoint, *, pages=False):
        calls.append(endpoint)
        if "/workflows/" in endpoint:
            assert pages
            return json.dumps([{"workflow_runs": [newer]}]).encode()
        if "/artifacts?" in endpoint:
            assert pages
            return json.dumps([{"artifacts": artifacts}]).encode()
        assert endpoint.endswith("/101/zip")
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as zf:
            zf.writestr("meta.json", json.dumps(meta))
            zf.writestr("installed-version.txt", "1.2\n")
            zf.writestr("untrusted.py", 'raise RuntimeError("do not execute")')
        return data.getvalue()

    monkeypatch.setattr(spu, "_gh", gh, raising=False)
    return argv, uploads, newer, meta, artifacts, calls


@pytest.mark.parametrize("cross_run", [False, True])
def test_upload_skips_older_candidate_after_newer_project_passed(
    freshness, monkeypatch, capsys, tmp_path, cross_run
):
    argv, uploads, _, _, _, calls = freshness
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10"])
    # Both in-run and scheduled use the same source ID; cross-run is not force.
    monkeypatch.setenv("GITHUB_RUN_ID", "99" if cross_run else "10")
    spu.main()
    assert uploads == []
    assert output.read_text() == "uploaded=false\n"
    notice = capsys.readouterr().out
    assert "::notice::" in notice
    assert "https://github.com/example/acme/actions/runs/20" in notice
    assert any(
        "sample-projects-released.yml/runs?branch=main" in call for call in calls
    )


@pytest.mark.parametrize(
    "change",
    ["fork", "branch", "pr", "older", "failed-project", "expired", "wrong-project"],
)
def test_newer_ineligible_or_failed_project_does_not_supersede(
    freshness, monkeypatch, change
):
    argv, uploads, newer, meta, artifacts, _ = freshness
    if change == "fork":
        newer["head_repository"]["full_name"] = "fork/acme"
    elif change == "branch":
        newer["head_branch"] = "feature"
    elif change == "pr":
        newer["event"] = "pull_request"
    elif change == "older":
        newer["id"] = 9
    elif change == "failed-project":
        artifacts[0]["name"] = "sample-project-tested-released-other"
    elif change == "expired":
        artifacts[0]["expired"] = True
    elif change == "wrong-project":
        meta.update(project_dir="other", slug="other")
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10"])
    if change == "wrong-project":
        with pytest.raises(ValueError, match="metadata"):
            spu.main()
        assert uploads == []
    else:
        spu.main()
        assert len(uploads) == 1


@pytest.mark.parametrize(
    "field,value",
    [("source_repo", "fork/acme"), ("source_sha", "wrong"), ("tree_sha", "")],
)
def test_freshness_rejects_invalid_passed_artifact_metadata(
    freshness, monkeypatch, field, value
):
    argv, uploads, _, meta, _, _ = freshness
    meta[field] = value
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10"])
    with pytest.raises(ValueError, match="metadata"):
        spu.main()
    assert uploads == []


def test_newer_run_still_uploading_with_passed_project_supersedes(
    freshness, monkeypatch, capsys
):
    argv, uploads, newer, _, _, _ = freshness
    newer.update(status="in_progress", conclusion=None)
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10"])
    spu.main()
    assert uploads == []
    assert "/actions/runs/20" in capsys.readouterr().out


def test_explicit_allow_stale_override_can_reupload(freshness, monkeypatch):
    argv, uploads, _, _, _, calls = freshness
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10", "--allow-stale"])
    spu.main()
    assert len(uploads) == 1
    assert calls == []


def test_older_completion_cannot_overwrite_newer_upload(freshness, monkeypatch):
    argv, uploads, *_ = freshness
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "20"])
    spu.main()
    assert len(uploads) == 1
    monkeypatch.setattr(sys, "argv", argv + ["--source-run-id", "10"])
    spu.main()
    assert len(uploads) == 1


@pytest.mark.parametrize("failure", ["api", "invalid-zip", "expired-race"])
def test_freshness_artifact_download_failures(freshness, monkeypatch, failure):
    argv, uploads, _, _, _, _ = freshness
    gh = spu._gh

    def download(endpoint, **kwargs):
        if endpoint.endswith("/zip"):
            if failure == "invalid-zip":
                return b"not zip"
            raise subprocess.CalledProcessError(
                1,
                ["gh"],
                stderr=b"410 Gone" if failure == "expired-race" else b"403 Forbidden",
            )
        return gh(endpoint, **kwargs)

    monkeypatch.setattr(spu, "_gh", download)
    monkeypatch.setattr(sys, "argv", argv)
    if failure == "expired-race":
        spu.main()
        assert len(uploads) == 1
    else:
        with pytest.raises((subprocess.CalledProcessError, zipfile.BadZipFile)):
            spu.main()
        assert uploads == []


@pytest.mark.parametrize("outcome", ["success", "post-failure", "dry-run"])
def test_only_successful_post_emits_upload_marker_output(
    upload_case, monkeypatch, tmp_path, outcome
):
    _, _, argv, uploads = upload_case
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    if outcome == "post-failure":

        def fail(*args):
            raise RuntimeError("POST failed")

        monkeypatch.setattr(spu, "upload", fail)
    monkeypatch.setattr(
        sys, "argv", argv + (["--dry-run"] if outcome == "dry-run" else [])
    )
    if outcome == "post-failure":
        with pytest.raises(RuntimeError, match="POST failed"):
            spu.main()
    else:
        spu.main()
    if outcome == "success":
        assert len(uploads) == 1
        assert output.read_text() == "uploaded=true\n"
    else:
        assert not output.exists()
        assert uploads == []
