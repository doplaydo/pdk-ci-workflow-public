"""Tests for sample-project discovery logic from test-sample-projects.yml.

The workflow discovers sample projects via:

    find . -maxdepth 3 -name 'pyproject.toml' \
        -path '*--sample-projects/*' ! -path '*/.venv/*'

These tests exercise the same ``find`` command against temporary directory
trees to verify discovery semantics.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_discovery(root: Path) -> list[str]:
    """Run the same find command used in the CI workflow and return matches."""
    result = subprocess.run(
        [
            "find", ".", "-maxdepth", "3",
            "-name", "pyproject.toml",
            "-path", "*--sample-projects/*",
            "!", "-path", "*/.venv/*",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    # Mirror the sed post-processing in the workflow:
    #   sed 's|^\./||; s|/pyproject.toml||'
    lines = [
        line.removeprefix("./").removesuffix("/pyproject.toml")
        for line in result.stdout.strip().splitlines()
        if line
    ]
    return sorted(lines)


def _make_sample_project(root: Path, rel_path: str) -> Path:
    """Create a minimal pyproject.toml at *root / rel_path / pyproject.toml*."""
    d = root / rel_path
    d.mkdir(parents=True, exist_ok=True)
    toml = d / "pyproject.toml"
    toml.write_text('[project]\nname = "sample"\n')
    return toml


class TestSampleProjectDiscovery:
    """Verify the ``find`` command used by the discover job."""

    def test_single_sample_project_found(self, tmp_path: Path) -> None:
        """A standard sample-project directory is discovered."""
        _make_sample_project(tmp_path, "my-pdk--sample-projects/basic")
        dirs = _run_discovery(tmp_path)
        assert dirs == ["my-pdk--sample-projects/basic"]

    def test_multiple_sample_projects_found(self, tmp_path: Path) -> None:
        """All sample-project directories under the root are returned."""
        _make_sample_project(tmp_path, "my-pdk--sample-projects/basic")
        _make_sample_project(tmp_path, "my-pdk--sample-projects/advanced")
        dirs = _run_discovery(tmp_path)
        assert dirs == [
            "my-pdk--sample-projects/advanced",
            "my-pdk--sample-projects/basic",
        ]

    def test_venv_directories_excluded(self, tmp_path: Path) -> None:
        """Paths containing ``.venv`` must be ignored."""
        _make_sample_project(
            tmp_path, "my-pdk--sample-projects/.venv/some-package"
        )
        dirs = _run_discovery(tmp_path)
        assert dirs == []

    def test_depth_beyond_maxdepth_not_found(self, tmp_path: Path) -> None:
        """Directories deeper than maxdepth 3 are NOT found.

        maxdepth 3 means at most ``./a/b/pyproject.toml`` (3 components from
        ``.``).  ``./a/b/c/pyproject.toml`` is depth 4 and should be missed.
        """
        _make_sample_project(
            tmp_path, "my-pdk--sample-projects/deep/nested/extra"
        )
        dirs = _run_discovery(tmp_path)
        assert dirs == []

    def test_non_matching_directory_ignored(self, tmp_path: Path) -> None:
        """Directories that don't match ``*--sample-projects/*`` are ignored."""
        _make_sample_project(tmp_path, "my-pdk/some-other-dir")
        _make_sample_project(tmp_path, "sample-projects/basic")
        dirs = _run_discovery(tmp_path)
        assert dirs == []

    def test_empty_repo_returns_nothing(self, tmp_path: Path) -> None:
        """An empty directory tree produces an empty list."""
        dirs = _run_discovery(tmp_path)
        assert dirs == []

    def test_maxdepth_boundary_is_found(self, tmp_path: Path) -> None:
        """A pyproject.toml at exactly maxdepth 3 should be found.

        Depth 3 = ``./parent--sample-projects/child/pyproject.toml``.
        """
        _make_sample_project(tmp_path, "x--sample-projects/y")
        dirs = _run_discovery(tmp_path)
        assert dirs == ["x--sample-projects/y"]

    def test_multiple_pdk_sample_projects(self, tmp_path: Path) -> None:
        """Sample projects from different PDKs are all discovered."""
        _make_sample_project(tmp_path, "pdk-a--sample-projects/basic")
        _make_sample_project(tmp_path, "pdk-b--sample-projects/demo")
        dirs = _run_discovery(tmp_path)
        assert dirs == [
            "pdk-a--sample-projects/basic",
            "pdk-b--sample-projects/demo",
        ]
