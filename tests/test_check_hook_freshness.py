"""Tests for check_hook_freshness hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hooks import check_hook_freshness
from hooks.check_hook_freshness import main

HOOK_REPO_URL = "https://github.com/doplaydo/pdk-ci-workflow-public"


def _fake_git(installed: str | None, remote: str | None):
    def fake(*args: str, **kwargs) -> str | None:
        if args[0] == "rev-parse":
            return installed
        if args[0] == "ls-remote":
            return None if remote is None else f"{remote}\trefs/heads/main"
        raise AssertionError(f"unexpected git args: {args}")

    return fake


class TestCheckHookFreshness:
    def test_self_repo_skips(self, pdk_root: Path) -> None:
        """Running inside pdk-ci-workflow itself should skip."""
        content = (pdk_root / "pyproject.toml").read_text()
        content = content.replace('name = "my-pdk"', 'name = "ci-pdk-workflows"')
        (pdk_root / "pyproject.toml").write_text(content)
        assert main() == 0

    def test_matching_sha_passes(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git("a" * 40, "a" * 40))
        assert main() == 0

    def test_stale_sha_fails(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git("a" * 40, "b" * 40))
        assert main() == 1

    def test_network_failure_warns_without_failing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git("a" * 40, None))
        assert main() == 0

    def test_no_installed_sha_warns_without_failing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git(None, "a" * 40))
        assert main() == 0

    def test_no_git_checkout_found_warns_without_failing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_hook_freshness, "_find_repo_root", lambda start: None)
        assert main() == 0

    def test_pinned_rev_skips_without_comparing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repo pinning `rev:` to a SHA/tag isn't tracking `main` — no-op."""
        config = pdk_root / ".pre-commit-config.yaml"
        content = config.read_text()
        content += (
            "- hooks:\n"
            "  - id: check-hook-freshness\n"
            f"  repo: {HOOK_REPO_URL}\n"
            "  rev: v1.2.3\n"
        )
        config.write_text(content)

        def _unexpected_git(*args: str, **kwargs) -> str | None:
            raise AssertionError("should not shell out to git when rev is pinned")

        monkeypatch.setattr(check_hook_freshness, "_git", _unexpected_git)
        assert main() == 0

    def test_rev_main_still_compares(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repo tracking `rev: main` still gets the freshness check."""
        config = pdk_root / ".pre-commit-config.yaml"
        content = config.read_text()
        content += (
            "- hooks:\n"
            "  - id: check-hook-freshness\n"
            f"  repo: {HOOK_REPO_URL}\n"
            "  rev: main\n"
        )
        config.write_text(content)

        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git("a" * 40, "b" * 40))
        assert main() == 1


def test_git_uses_cached_repo_under_commit_hook_environment(tmp_path, monkeypatch):
    """Inherited PDK Git variables must not redirect cached-repository checks."""

    def git(directory, *args):
        return subprocess.run(
            ["git", *args], cwd=directory, check=True, capture_output=True, text=True
        ).stdout.strip()

    revisions = []
    for name in ("consumer", "cached-hooks"):
        directory = tmp_path / name
        directory.mkdir()
        git(directory, "init", "-b", "main")
        git(directory, "config", "user.email", "test@example.com")
        git(directory, "config", "user.name", "Hook test")
        (directory / "content").write_text(name)
        git(directory, "add", "content")
        git(directory, "commit", "-m", name)
        revisions.append(git(directory, "rev-parse", "HEAD"))
    consumer, cached = tmp_path / "consumer", tmp_path / "cached-hooks"
    git(cached, "remote", "add", "origin", str(cached))
    monkeypatch.setenv("GIT_DIR", str(consumer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(consumer))
    monkeypatch.setenv("GIT_INDEX_FILE", str(consumer / ".git/index"))
    monkeypatch.setenv("GIT_PREFIX", "nested/")
    assert revisions[0] != revisions[1]
    assert check_hook_freshness._git("rev-parse", "HEAD", cwd=cached) == revisions[1]
    remote = check_hook_freshness._git("ls-remote", "origin", "main", cwd=cached)
    assert remote.split()[0] == revisions[1]


def test_git_env_keeps_config_and_drops_repo_location(monkeypatch):
    """Transport/credential config survives; repo-location variables don't."""
    monkeypatch.setenv("GIT_DIR", "/consumer/.git")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'http.extraheader=x'")
    env = check_hook_freshness._git_env()
    assert "GIT_DIR" not in env
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_PARAMETERS"] == "'http.extraheader=x'"
