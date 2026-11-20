"""Exercise changelog fragment enforcement against real Git trees."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from hooks._utils import apply_sync_markers
from hooks.check_no_new_changelog_fragments import TEMPLATE, main

LEGACY = ".changelog.d/old.changed.md"
NEW = ".changelog.d/nested/new.changed.md"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def stage(path):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("test changelog content\n")
    git("add", "--", path)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    stage("CHANGELOG.md")
    stage(LEGACY)
    git("commit", "-qm", "Historical entries")
    return tmp_path


def set_event(monkeypatch, repo, name, payload):
    path = repo / "event.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))


def test_staged_fragment_is_blocked_but_template_and_history_are_allowed(repo, capsys):
    stage(TEMPLATE)
    stage("CHANGELOG.md")
    assert main() == 0
    stage(NEW)
    assert main() == 1
    output = capsys.readouterr().out
    assert NEW in output and "Use a release-note-ready PR title" in output
    assert TEMPLATE not in output and LEGACY not in output


def test_removing_legacy_path_is_allowed(repo):
    git("rm", LEGACY)
    assert main() == 0


def test_moving_an_existing_file_into_changelog_is_an_addition(repo, capsys):
    git("mv", LEGACY, ".changelog.d/renamed.changed.md")
    assert main() == 1
    assert "renamed.changed.md" in capsys.readouterr().out


def test_copy_into_changelog_is_an_addition(repo, capsys):
    path = ".changelog.d/copied.changed.md"
    Path(path).write_bytes(Path(LEGACY).read_bytes())
    git("add", path)
    assert main() == 1
    assert path in capsys.readouterr().out


def test_committed_feature_branch_addition_in_local_all_files_run(repo, capsys):
    git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git("checkout", "-qb", "feature")
    stage(NEW)
    git("commit", "-qm", "Commit fragment")
    assert not git("diff", "--cached", "--name-only")
    assert main() == 1
    assert NEW in capsys.readouterr().out


def test_pr_detects_committed_addition_but_not_updated_base_history(
    repo, monkeypatch, capsys
):
    git("checkout", "-qb", "feature")
    stage(NEW)
    git("commit", "-qm", "Feature fragment")
    git("checkout", "-q", "main")
    stage(".changelog.d/already-on-base.changed.md")
    git("commit", "-qm", "Base advanced")
    base = git("rev-parse", "HEAD")
    git("merge", "--no-ff", "-qm", "Synthetic PR merge", "feature")
    set_event(
        monkeypatch, repo, "pull_request", {"pull_request": {"base": {"sha": base}}}
    )
    assert main() == 1
    output = capsys.readouterr().out
    assert NEW in output
    assert "already-on-base.changed.md" not in output


def test_push_checks_entire_range(repo, monkeypatch, capsys):
    before = git("rev-parse", "HEAD")
    stage(NEW)
    git("commit", "-qm", "Feature fragment")
    stage("other.txt")
    git("commit", "-qm", "Unrelated follow-up")
    set_event(monkeypatch, repo, "push", {"before": before})
    assert main() == 1
    assert NEW in capsys.readouterr().out


def test_first_push_uses_empty_tree(repo, monkeypatch, capsys):
    stage(TEMPLATE)
    git("commit", "-qm", "Add allowed template")
    set_event(
        monkeypatch,
        repo,
        "push",
        {
            "before": "0" * 40,
            "ref": "refs/heads/main",
            "repository": {"default_branch": "main"},
        },
    )
    assert main() == 1
    output = capsys.readouterr().out
    assert LEGACY in output and TEMPLATE not in output


def test_first_feature_branch_push_uses_default_history(repo, monkeypatch, capsys):
    git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
    git("checkout", "-qb", "feature")
    stage(NEW)
    git("commit", "-qm", "Feature fragment")
    set_event(
        monkeypatch,
        repo,
        "push",
        {
            "before": "0" * 40,
            "ref": "refs/heads/feature",
            "repository": {"default_branch": "main"},
        },
    )
    assert main() == 1
    output = capsys.readouterr().out
    assert NEW in output and LEGACY not in output


def test_missing_ci_base_fails_closed(repo, monkeypatch, capsys):
    set_event(monkeypatch, repo, "push", {"before": "1" * 40})
    assert main() == 1
    output = capsys.readouterr().out
    assert "Could not compare changelog paths" in output
    assert "GitHub 'push' event" in output
    assert "fatal:" in output
    assert "1" * 40 in output


def test_missing_event_path_reports_environment_key(repo, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert main() == 1
    assert "Details: 'GITHUB_EVENT_PATH'" in capsys.readouterr().out


def test_registration_and_checkout_history_survive_public_projection():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/.pre-commit-config.yaml").read_text()
    workflow = (root / ".github/workflows/test_code.yml").read_text()
    for target in ("private", "public"):
        config = yaml.safe_load(apply_sync_markers(template, keep=target))
        assert {"id": "check-no-new-changelog-fragments"} in config["repos"][0]["hooks"]
        jobs = yaml.safe_load(apply_sync_markers(workflow, keep=target))["jobs"]
        assert jobs["pre-commit"]["steps"][0]["with"]["fetch-depth"] == 0
