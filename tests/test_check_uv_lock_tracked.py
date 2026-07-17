"""Tests for check_uv_lock_tracked hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hooks import check_uv_lock_tracked
from hooks.check_uv_lock_tracked import main


class TestCheckUvLockTracked:
    def test_self_repo_skips(self, pdk_root: Path) -> None:
        """Running inside pdk-ci-workflow itself should skip entirely."""
        content = (pdk_root / "pyproject.toml").read_text()
        content = content.replace('name = "my-pdk"', 'name = "ci-pdk-workflows"')
        (pdk_root / "pyproject.toml").write_text(content)
        assert main() == 0

    def test_missing_lock_file_fails_for_uv_repo(self, pdk_root: Path) -> None:
        """The example fixture's Makefile install target runs `uv sync`."""
        assert not (pdk_root / "uv.lock").exists()
        assert main() == 1

    def test_missing_lock_file_skipped_for_non_uv_repo(self, pdk_root: Path) -> None:
        """A pip-based install target isn't forced to adopt uv.lock."""
        makefile = pdk_root / "Makefile"
        content = makefile.read_text().replace("uv sync", "pip install -e .")
        makefile.write_text(content)
        assert not (pdk_root / "uv.lock").exists()
        assert main() == 0

    def test_tracked_lock_file_passes(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(
            check_uv_lock_tracked, "_git_ls_files", lambda *a, **k: "uv.lock\n"
        )
        assert main() == 0

    def test_untracked_lock_file_fails(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uv.lock exists on disk but git doesn't track it (e.g. gitignored)."""
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(check_uv_lock_tracked, "_git_ls_files", lambda *a, **k: "")
        assert main() == 1

    def test_untracked_lock_file_fails_even_for_non_uv_makefile(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A uv.lock present on disk is itself evidence of uv usage, so the
        tracked-file check still applies even if the Makefile's install
        target doesn't mention uv (e.g. a stale pip-era Makefile)."""
        makefile = pdk_root / "Makefile"
        content = makefile.read_text().replace("uv sync", "pip install -e .")
        makefile.write_text(content)
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(check_uv_lock_tracked, "_git_ls_files", lambda *a, **k: "")
        assert main() == 1

    def test_git_unavailable_warns_without_failing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git can't be shelled out to (not a repo, missing binary, timeout)."""
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(
            check_uv_lock_tracked, "_git_ls_files", lambda *a, **k: None
        )
        assert main() == 0


class TestGitLsFilesHelper:
    """Exercises the real subprocess call (not mocked) against a throwaway
    git repo, so a bug in the git invocation itself isn't masked by the
    mocked-`_git_ls_files` tests above."""

    def test_tracked_file_returns_nonempty(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "uv.lock").write_text("x")
        subprocess.run(["git", "add", "uv.lock"], cwd=tmp_path, check=True)
        output = check_uv_lock_tracked._git_ls_files("uv.lock", cwd=tmp_path)
        assert output is not None
        assert "uv.lock" in output.split()

    def test_untracked_file_returns_empty(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "uv.lock").write_text("x")
        output = check_uv_lock_tracked._git_ls_files("uv.lock", cwd=tmp_path)
        assert output == ""

    def test_not_a_git_repo_returns_none(self, tmp_path: Path) -> None:
        output = check_uv_lock_tracked._git_ls_files("uv.lock", cwd=tmp_path)
        assert output is None


class TestUsesUv:
    def test_makefile_uv_install_detected(self, pdk_root: Path) -> None:
        assert check_uv_lock_tracked._uses_uv() is True

    def test_pip_makefile_and_no_tool_uv_section_not_detected(
        self, pdk_root: Path
    ) -> None:
        makefile = pdk_root / "Makefile"
        content = makefile.read_text().replace("uv sync", "pip install -e .")
        makefile.write_text(content)
        assert check_uv_lock_tracked._uses_uv() is False

    def test_tool_uv_section_in_pyproject_detected(self, pdk_root: Path) -> None:
        makefile = pdk_root / "Makefile"
        content = makefile.read_text().replace("uv sync", "pip install -e .")
        makefile.write_text(content)
        pyproject = pdk_root / "pyproject.toml"
        pyproject.write_text(pyproject.read_text() + "\n[tool.uv]\n")
        assert check_uv_lock_tracked._uses_uv() is True
