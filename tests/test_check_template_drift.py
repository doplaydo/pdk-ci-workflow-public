"""Tests for check_template_drift hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.check_template_drift import main


class TestCheckTemplateDrift:
    def test_self_repo_skips(self, pdk_root: Path) -> None:
        """Running inside pdk-ci-workflow itself should skip."""
        # The fixture's pyproject.toml uses name="my-pdk".
        # Override to the self-repo name.
        content = (pdk_root / "pyproject.toml").read_text()
        content = content.replace('name = "my-pdk"', 'name = "ci-pdk-workflows"')
        (pdk_root / "pyproject.toml").write_text(content)
        assert main() == 0

    def test_no_local_files_fails_and_creates_them(self, pdk_root: Path) -> None:
        """If none of the template files exist locally, hook fails and creates them."""
        # Remove all .github files
        import shutil

        if (pdk_root / ".github").exists():
            shutil.rmtree(pdk_root / ".github")

        # Should fail because it creates them
        assert main() == 1

        # Verify at least one was created
        from hooks.check_template_drift import TEMPLATES

        assert (pdk_root / TEMPLATES[0]).exists()

    def test_matching_template_passes(self, pdk_root: Path) -> None:
        """If local file matches the template exactly, hook passes."""
        from importlib.resources import files
        from hooks.check_template_drift import TEMPLATES
        from hooks._utils import apply_sync_markers

        root = files("templates")

        # Ensure ALL template files exist locally and match the template
        # (the resolved, private-view content — the same text _enforce_template
        # itself writes — not the raw canonical source with markers intact).
        for rel in TEMPLATES:
            local = pdk_root / rel
            local.parent.mkdir(parents=True, exist_ok=True)

            parts = rel.split("/")
            src = root
            for p in parts:
                src = src.joinpath(p)

            if src.is_file():
                resolved = apply_sync_markers(
                    src.read_text(encoding="utf-8"), keep="private"
                )
                local.write_text(resolved)

        assert main() == 0

    def test_drifted_file_gets_rewritten(self, pdk_root: Path) -> None:
        """A drifted file should be rewritten and hook should return 1."""
        from importlib.resources import files
        from hooks.check_template_drift import TEMPLATES
        from hooks._utils import apply_sync_markers

        root = files("templates")

        # Find a template file that exists
        for rel in TEMPLATES:
            parts = rel.split("/")
            src = root
            for p in parts:
                src = src.joinpath(p)
            if src.is_file():
                # Create local file with different content
                local = pdk_root / rel
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text("# this content is different\n")

                # Note: other files in TEMPLATES might still be missing in pdk_root
                # and would also cause failure. But that's fine, result should be 1.
                result = main()
                assert result == 1
                # After rewrite, local file should match template
                # (the resolved, private-view content — the same text _enforce_template writes)
                resolved = apply_sync_markers(
                    src.read_text(encoding="utf-8"), keep="private"
                )
                assert local.read_text() == resolved
                return

        pytest.skip("No template files found in package")

    def test_second_run_after_rewrite_passes(self, pdk_root: Path) -> None:
        """After rewriting, a second run should pass (Ruff-style auto-fix)."""
        from importlib.resources import files
        from hooks.check_template_drift import TEMPLATES

        root = files("templates")

        # To ensure the second run passes, we must handle ALL templates
        for rel in TEMPLATES:
            local = pdk_root / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text("# drifted content\n")

        # First run: rewrite (should return 1 because many files are "fixed")
        assert main() == 1
        # Second run: should pass (all match now)
        assert main() == 0

    def test_deprecated_release_drafter_files_get_deleted(self, pdk_root: Path) -> None:
        """release-drafter files, if still present locally, get force-deleted."""
        stale_workflow = pdk_root / ".github" / "workflows" / "release-drafter.yml"
        stale_config = pdk_root / ".github" / "release-drafter.yml"
        stale_workflow.parent.mkdir(parents=True, exist_ok=True)
        stale_workflow.write_text("# stale\n")
        stale_config.write_text("# stale\n")

        assert main() == 1
        assert not stale_workflow.exists()
        assert not stale_config.exists()

    def test_forbidden_env_files_get_deleted(self, pdk_root: Path) -> None:
        """.env and .env.local, if actually committed, get force-deleted."""
        import subprocess

        env_file = pdk_root / ".env"
        env_local_file = pdk_root / ".env.local"
        env_file.write_text("SECRET=shh\n")
        env_local_file.write_text("SECRET=shh\n")

        subprocess.run(["git", "init", "-q"], cwd=pdk_root, check=True)
        # -f: force-add regardless of the developer machine's global gitignore
        # (e.g. a global core.excludesfile commonly ignores .env); the test
        # needs these files genuinely tracked, independent of local git config.
        subprocess.run(
            ["git", "add", "-f", ".env", ".env.local"], cwd=pdk_root, check=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@test.com",
                "-c",
                "user.name=test",
                "commit",
                "-q",
                "-m",
                "add env files",
            ],
            cwd=pdk_root,
            check=True,
        )

        assert main() == 1
        assert not env_file.exists()
        assert not env_local_file.exists()

    def test_untracked_env_file_is_not_deleted(self, pdk_root: Path) -> None:
        """An on-disk .env that was never committed (e.g. gitignored) must survive.

        This is the regression the git-tracked gate exists to prevent: a repo
        that correctly gitignores `.env` and uses it locally must not lose it
        the next time pre-commit runs.
        """
        import subprocess

        env_file = pdk_root / ".env"
        env_file.write_text("SECRET=shh\n")

        # A git repo exists (pre-commit always runs inside one) but the file
        # itself was never staged/committed -- e.g. it's gitignored.
        subprocess.run(["git", "init", "-q"], cwd=pdk_root, check=True)

        main()

        assert env_file.exists()
        assert env_file.read_text() == "SECRET=shh\n"

    def test_forbidden_file_message_is_distinct_from_deprecated_template(
        self, pdk_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The forbidden-file message must not reuse "deprecated template" wording."""
        import subprocess

        (pdk_root / ".env").write_text("SECRET=shh\n")
        subprocess.run(["git", "init", "-q"], cwd=pdk_root, check=True)
        subprocess.run(["git", "add", "-f", ".env"], cwd=pdk_root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@test.com",
                "-c",
                "user.name=test",
                "commit",
                "-q",
                "-m",
                "add env file",
            ],
            cwd=pdk_root,
            check=True,
        )

        assert main() == 1
        captured = capsys.readouterr()
        assert (
            "deleted forbidden file .env "
            "(committed secrets/env files must never be tracked)"
        ) in captured.out
        assert "deprecated template" not in captured.out

    def test_no_forbidden_files_present_leaves_hook_unaffected(
        self, pdk_root: Path
    ) -> None:
        """Hook passes on the FORBIDDEN_FILES check when no such file exists."""
        from importlib.resources import files

        from hooks._utils import apply_sync_markers
        from hooks.check_template_drift import TEMPLATES

        root = files("templates")
        for rel in TEMPLATES:
            local = pdk_root / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            parts = rel.split("/")
            src = root
            for p in parts:
                src = src.joinpath(p)
            if src.is_file():
                resolved = apply_sync_markers(
                    src.read_text(encoding="utf-8"), keep="private"
                )
                local.write_text(resolved)

        assert not (pdk_root / ".env").exists()
        assert not (pdk_root / ".env.local").exists()
        assert main() == 0


class TestSyncMarkerStripping:
    def test_markers_stripped_from_enforced_content(self, pdk_root: Path) -> None:
        """Downstream PDK repos never see literal SYNC-PRIVATE/SYNC-PUBLIC marker text."""
        from importlib.resources import files
        from hooks.check_template_drift import TEMPLATES, main

        root = files("templates")

        for rel in TEMPLATES:
            local = pdk_root / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            parts = rel.split("/")
            src = root
            for p in parts:
                src = src.joinpath(p)
            if src.is_file():
                # Force drift so _enforce_template rewrites the local file.
                local.write_text("stale content\n")

        main()

        for rel in TEMPLATES:
            local = pdk_root / rel
            if local.exists():
                assert "SYNC-PRIVATE:" not in local.read_text()
                assert "SYNC-PUBLIC:" not in local.read_text()
