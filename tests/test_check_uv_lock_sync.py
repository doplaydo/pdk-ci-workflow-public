"""Tests for check_uv_lock_sync hook."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hooks import check_uv_lock_sync
from hooks.check_uv_lock_sync import main


class TestCheckUvLockSync:
    def test_self_repo_skips(self, pdk_root: Path) -> None:
        """Running inside pdk-ci-workflow itself should skip entirely."""
        content = (pdk_root / "pyproject.toml").read_text()
        content = content.replace('name = "my-pdk"', 'name = "ci-pdk-workflows"')
        (pdk_root / "pyproject.toml").write_text(content)
        (pdk_root / "uv.lock").write_text("version = 1\n")
        assert main() == 0

    def test_no_lock_file_skips(self, pdk_root: Path) -> None:
        """A missing lock file is check-uv-lock-tracked's error to report."""
        assert not (pdk_root / "uv.lock").exists()
        assert main() == 0

    def test_in_sync_lock_passes(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(
            check_uv_lock_sync, "_uv_lock_check", lambda *a, **k: (0, "")
        )
        assert main() == 0

    def test_out_of_sync_lock_fails(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pyproject.toml was edited without re-running `uv lock`."""
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(
            check_uv_lock_sync,
            "_uv_lock_check",
            lambda *a, **k: (2, "error: The lockfile is not up-to-date"),
        )
        assert main() == 1

    def test_out_of_sync_lock_reports_uv_output(
        self,
        pdk_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """uv's own diagnostic is surfaced, not swallowed."""
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(
            check_uv_lock_sync,
            "_uv_lock_check",
            lambda *a, **k: (2, "error: `doroutes` version mismatch"),
        )
        assert main() == 1
        assert "doroutes" in capsys.readouterr().out

    def test_uv_unavailable_warns_without_failing(
        self, pdk_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uv can't be shelled out to (missing binary, timeout).

        An environment problem must not read as a dependency problem.
        """
        (pdk_root / "uv.lock").write_text("version = 1\n")
        monkeypatch.setattr(check_uv_lock_sync, "_uv_lock_check", lambda *a, **k: None)
        assert main() == 0


class TestUvLockCheckHelper:
    """Exercises the real subprocess call (not mocked), so a bug in the uv
    invocation itself isn't masked by the mocked tests above."""

    def test_missing_uv_binary_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        assert check_uv_lock_sync._uv_lock_check(cwd=tmp_path) is None

    def test_timeout_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="uv", timeout=1)

        monkeypatch.setattr(subprocess, "run", _raise)
        assert check_uv_lock_sync._uv_lock_check(cwd=tmp_path) is None

    @pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
    def test_real_uv_reports_in_sync_then_out_of_sync_lock(
        self, tmp_path: Path
    ) -> None:
        """A freshly locked project passes; editing it without re-locking fails."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "x"\nversion = "0.1.0"\n'
            'requires-python = ">=3.9"\ndependencies = []\n'
        )
        subprocess.run(["uv", "lock"], cwd=tmp_path, check=True, capture_output=True)

        checked = check_uv_lock_sync._uv_lock_check(cwd=tmp_path)
        assert checked is not None
        assert checked[0] == 0

        pyproject.write_text(
            '[project]\nname = "x"\nversion = "0.2.0"\n'
            'requires-python = ">=3.9"\ndependencies = []\n'
        )
        checked = check_uv_lock_sync._uv_lock_check(cwd=tmp_path)
        assert checked is not None
        assert checked[0] != 0
