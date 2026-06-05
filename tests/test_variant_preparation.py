"""Tests for the PDK variant detection logic from test-sample-projects.yml.

The inline Python script in the *Prepare PDK variant* step determines whether
a sample-project depends on a variant of the root PDK (e.g.
``my-pdk-photonic``) and, if so, which variant name to use when calling
``make prepare-<variant>``.

We extract that logic into a pure function here and exercise the various
dependency formats it must handle.
"""

from __future__ import annotations

import re
from pathlib import Path


def detect_variant(root_name: str, dependencies: list[str]) -> str | None:
    """Return the variant suffix, or ``None`` if the project uses the root PDK.

    This mirrors the inline Python script embedded in
    ``.github/workflows/test-sample-projects.yml``:

    .. code-block:: python

        for d in deps:
            dep_name = re.split(r"[\\[~><=!;@\\s]", d)[0]
            if dep_name != root_name and dep_name.startswith(root_name + "-"):
                variant = dep_name[len(root_name) + 1:]
                ...
                break
    """
    for d in dependencies:
        dep_name = re.split(r"[\[~><=!;@\s]", d)[0]
        if dep_name != root_name and dep_name.startswith(root_name + "-"):
            return dep_name[len(root_name) + 1 :]
    return None


class TestDetectVariant:
    """Unit tests for variant detection from dependency lists."""

    def test_simple_root_dependency_no_variant(self) -> None:
        """Depending on the root PDK itself yields no variant."""
        assert detect_variant("my-pdk", ["my-pdk"]) is None

    def test_variant_dependency(self) -> None:
        """A dependency named ``root-name-<variant>`` yields the variant."""
        assert detect_variant("my-pdk", ["my-pdk-photonic"]) == "photonic"

    def test_variant_with_extras(self) -> None:
        """Extras brackets are stripped before comparison."""
        assert detect_variant("my-pdk", ["my-pdk-photonic[sim]"]) == "photonic"

    def test_variant_with_version_constraint(self) -> None:
        """Version constraints are stripped before comparison."""
        assert detect_variant("my-pdk", ["my-pdk-photonic>=1.0"]) == "photonic"

    def test_variant_with_tilde_constraint(self) -> None:
        assert detect_variant("my-pdk", ["my-pdk-photonic~=2.0"]) == "photonic"

    def test_variant_with_not_equal(self) -> None:
        assert detect_variant("my-pdk", ["my-pdk-photonic!=0.1"]) == "photonic"

    def test_variant_with_semicolon_marker(self) -> None:
        """PEP 508 environment markers use ``;``."""
        result = detect_variant(
            "my-pdk", ['my-pdk-photonic; python_version>="3.9"']
        )
        assert result == "photonic"

    def test_variant_with_at_url(self) -> None:
        """Direct URL references use ``@``."""
        result = detect_variant(
            "my-pdk", ["my-pdk-photonic@ https://example.com/archive.tar.gz"]
        )
        assert result == "photonic"

    def test_multiple_deps_only_variant_matters(self) -> None:
        """When there are multiple deps, only the variant one is returned."""
        deps = ["gdsfactory>=7.0", "numpy", "my-pdk-silicon", "requests"]
        assert detect_variant("my-pdk", deps) == "silicon"

    def test_root_name_itself_is_skipped(self) -> None:
        """The root name matching exactly is not a variant."""
        deps = ["my-pdk", "my-pdk-photonic"]
        assert detect_variant("my-pdk", deps) == "photonic"

    def test_no_matching_deps(self) -> None:
        """Dependencies unrelated to the root yield no variant."""
        deps = ["numpy", "scipy", "gdsfactory"]
        assert detect_variant("my-pdk", deps) is None

    def test_empty_dependencies(self) -> None:
        assert detect_variant("my-pdk", []) is None

    def test_first_variant_wins(self) -> None:
        """The script breaks after the first variant match."""
        deps = ["my-pdk-photonic", "my-pdk-silicon"]
        assert detect_variant("my-pdk", deps) == "photonic"

    def test_compound_variant_name(self) -> None:
        """A multi-segment variant like ``high-speed`` is returned intact."""
        assert (
            detect_variant("my-pdk", ["my-pdk-high-speed"]) == "high-speed"
        )

    def test_variant_with_whitespace_constraint(self) -> None:
        """Whitespace between name and constraint is handled."""
        assert detect_variant("my-pdk", ["my-pdk-photonic >=1.0"]) == "photonic"


class TestDetectVariantWithFiles:
    """Integration-level tests that read from actual pyproject.toml files."""

    def test_variant_from_toml_files(self, tmp_path: Path) -> None:
        """Round-trip: write pyproject.toml files, read them, detect variant."""
        import tomllib

        # Root pyproject.toml
        root_toml = tmp_path / "root" / "pyproject.toml"
        root_toml.parent.mkdir()
        root_toml.write_text('[project]\nname = "my-pdk"\n')

        # Sample project pyproject.toml
        sample_toml = tmp_path / "sample" / "pyproject.toml"
        sample_toml.parent.mkdir()
        sample_toml.write_text(
            '[project]\nname = "sample"\n'
            'dependencies = ["my-pdk-photonic>=1.0", "numpy"]\n'
        )

        with open(root_toml, "rb") as f:
            root_name = tomllib.load(f)["project"]["name"]
        with open(sample_toml, "rb") as f:
            deps = tomllib.load(f)["project"]["dependencies"]

        assert detect_variant(root_name, deps) == "photonic"

    def test_no_variant_from_toml_files(self, tmp_path: Path) -> None:
        """Sample project depending on root PDK directly has no variant."""
        import tomllib

        root_toml = tmp_path / "root" / "pyproject.toml"
        root_toml.parent.mkdir()
        root_toml.write_text('[project]\nname = "my-pdk"\n')

        sample_toml = tmp_path / "sample" / "pyproject.toml"
        sample_toml.parent.mkdir()
        sample_toml.write_text(
            '[project]\nname = "sample"\n'
            'dependencies = ["my-pdk", "numpy"]\n'
        )

        with open(root_toml, "rb") as f:
            root_name = tomllib.load(f)["project"]["name"]
        with open(sample_toml, "rb") as f:
            deps = tomllib.load(f)["project"]["dependencies"]

        assert detect_variant(root_name, deps) is None
