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

    def test_recommended_targets_missing_only_warns(self, pdk_root: Path) -> None:
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
        """dev target fetching the canonical config gets normalized and exits 1."""
        stale_curl = "curl -sf https://raw.githubusercontent.com/doplaydo/pdk-ci-workflow/main/templates/.pre-commit-config.yaml -o .pre-commit-config.yaml"
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
                \tgh api "repos/doplaydo/pdk-ci-workflow/contents/templates/.pre-commit-config.yaml?ref=main" --header "Accept: application/vnd.github.raw+json" > .pre-commit-config.yaml
                \tuv run pre-commit clean
                \tuv run pre-commit install
            """)
        )
        assert main() == 0

    def test_dev_missing_precommit_clean_autofix_and_fails(
        self, pdk_root: Path
    ) -> None:
        """dev target missing `pre-commit clean` gets it inserted before install, exits 1."""
        makefile = pdk_root / "Makefile"
        makefile.write_text(
            textwrap.dedent("""\
                install:
                \tuv sync

                test:
                \tuv run pytest

                dev: install
                \tgh api "repos/doplaydo/pdk-ci-workflow/contents/templates/.pre-commit-config.yaml?ref=main" --header "Accept: application/vnd.github.raw+json" > .pre-commit-config.yaml
                \tuv run pre-commit install
            """)
        )
        assert main() == 1
        rewritten = makefile.read_text()
        dev_body = rewritten.split("dev:")[1]
        assert "\tuv run pre-commit clean\n\tuv run pre-commit install" in dev_body

    def test_dev_with_precommit_clean_passes(self, pdk_root: Path) -> None:
        """dev target already running `pre-commit clean` before install passes without rewrite."""
        makefile = pdk_root / "Makefile"
        content = textwrap.dedent("""\
            install:
            \tuv sync

            test:
            \tuv run pytest

            dev: install
            \tgh api "repos/doplaydo/pdk-ci-workflow/contents/templates/.pre-commit-config.yaml?ref=main" --header "Accept: application/vnd.github.raw+json" > .pre-commit-config.yaml
            \tuv run pre-commit clean
            \tuv run pre-commit install
        """)
        makefile.write_text(content)
        assert main() == 0
        assert makefile.read_text() == content

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
                \tuv run pre-commit clean
                \tuv run pre-commit install
            """)
        )
        assert main() == 0
    
    def test_dev_curl_public_missing_precommit_clean_autofix_and_fails(
        self, pdk_root: Path
    ) -> None:
        """Public curl + pre-commit install but no clean → auto-fix inserts clean, exits 1."""
        makefile = pdk_root / "Makefile"
        makefile.write_text(
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
        assert main() == 1
        rewritten = makefile.read_text()
        dev_body = rewritten.split("dev:")[1]
        assert "\tuv run pre-commit clean\n\tuv run pre-commit install" in dev_body
