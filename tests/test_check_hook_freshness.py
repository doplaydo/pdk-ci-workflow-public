"""Tests for check_hook_freshness hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks import check_hook_freshness
from hooks.check_hook_freshness import main


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
