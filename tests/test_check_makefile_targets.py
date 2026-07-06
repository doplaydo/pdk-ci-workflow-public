"""Tests for check_makefile_targets hook."""

from __future__ import annotations

import textwrap
from pathlib import Path

from hooks.check_makefile_targets import main


class TestCheckMakefileTargets:
    def test_valid_makefile_passes(self, pdk_root: Path) -> None:
        assert main() == 0

    def test_no_makefile_fails(self, pdk_root: Path) -> None:
        (pdk_root / "Makefile").unlink()
        assert main() == 1

    def test_missing_install_target_fails(self, pdk_root: Path) -> None:
        (pdk_root / "Makefile").write_text("test:\n\tpytest\n")
        assert main() == 1

    def test_missing_test_target_fails(self, pdk_root: Path) -> None:
        (pdk_root / "Makefile").write_text("install:\n\tuv sync\n")
        assert main() == 1

    def test_pip_install_warns(self, pdk_root: Path) -> None:
        """Using pip instead of uv should not fail but should warn."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tpip install -e .

                test:
                \tpytest
            """)
        )
        # Should still pass (warnings only for pip usage)
        assert main() == 0

    def test_test_without_pytest_warns(self, pdk_root: Path) -> None:
        """test target without pytest should warn but not fail."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tuv sync

                test:
                \tpython -m unittest
            """)
        )
        assert main() == 0

    def test_recommended_targets_missing_only_warns(
        self, pdk_root: Path
    ) -> None:
        """Missing recommended targets should not fail the hook."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tuv sync

                test:
                \tuv run pytest
            """)
        )
        assert main() == 0

    def test_setup_py_build_warns(self, pdk_root: Path) -> None:
        """build target using setup.py should warn but not fail."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tuv sync

                test:
                \tpytest

                build:
                \tpython setup.py sdist
            """)
        )
        assert main() == 0

    def test_dev_curl_autofix_rewrites_and_fails(self, pdk_root: Path) -> None:
        """dev target fetching a non-canonical source gets normalized and exits 1."""
        # sync-public.yml's repo-name sed transform runs before sync markers are
        # resolved and blindly rewrites any literal "doplaydo/pdk-ci-workflow-public" in
        # this file to "doplaydo/pdk-ci-workflow-public" — including inert test
        # fixture text outside any SYNC-PUBLIC region. The public variant below
        # therefore uses a differently-stale URL (missing the doplaydo/ prefix)
        # so it survives that transform unchanged and still exercises the
        # autofix instead of accidentally already matching the canonical output.
        stale_curl = "curl -sf https://raw.githubusercontent.com/some-fork/pdk-ci-workflow/main/templates/.pre-commit-config.yaml -o .pre-commit-config.yaml"
        makefile = pdk_root / "Makefile"
        makefile.write_text(
            textwrap.dedent(f"""\
                install:
                \tuv sync

                test:
                \tuv run pytest

                dev: install
                \t{stale_curl}
                \tuv run pre-commit install
            """)
        )
        assert main() == 1
        rewritten = makefile.read_text()
        assert "curl" in rewritten.split("dev:")[1].split("\n")[1]
        assert "pdk-ci-workflow-public" in rewritten

    def test_dev_gh_api_passes(self, pdk_root: Path) -> None:
        """dev target using gh api passes without error."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tuv sync

                test:
                \tuv run pytest

                dev: install
                \tgh api "repos/doplaydo/pdk-ci-workflow-public/contents/templates/.pre-commit-config.yaml?ref=main" --header "Accept: application/vnd.github.raw+json" > .pre-commit-config.yaml
                \tuv run pre-commit install
            """)
        )
        assert main() == 0

    def test_dev_curl_already_public_passes(self, pdk_root: Path) -> None:
        """dev target already fetching the canonical public repo passes without rewrite."""
        (pdk_root / "Makefile").write_text(
            textwrap.dedent("""\
                install:
                \tuv sync
    
                test:
                \tuv run pytest
    
                dev: install
                \tcurl -sf https://raw.githubusercontent.com/doplaydo/pdk-ci-workflow-public/main/templates/.pre-commit-config.yaml -o .pre-commit-config.yaml
                \tuv run pre-commit install
            """)
        )
        assert main() == 0
