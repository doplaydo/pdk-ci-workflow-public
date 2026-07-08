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

    def test_pinned_rev_skips_without_comparing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repo pinning `rev:` to a SHA/tag isn't tracking `main` — no-op."""
        config = pdk_root / ".pre-commit-config.yaml"
        content = config.read_text()
        content += (
            "- hooks:\n"
            "  - id: check-hook-freshness\n"
            "  repo: https://github.com/doplaydo/pdk-ci-workflow\n"
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
            "  repo: https://github.com/doplaydo/pdk-ci-workflow\n"
            "  rev: main\n"
        )
        config.write_text(content)

        monkeypatch.setattr(
            check_hook_freshness, "_find_repo_root", lambda start: pdk_root
        )
        monkeypatch.setattr(check_hook_freshness, "_git", _fake_git("a" * 40, "b" * 40))
        assert main() == 1
