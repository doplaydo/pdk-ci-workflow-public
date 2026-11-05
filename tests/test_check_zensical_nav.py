"""Tests for check_zensical_nav hook."""

from __future__ import annotations

import textwrap
from pathlib import Path

from hooks.check_zensical_nav import main


def _write_config(root: Path, nav: str, *, at: str = "docs/zensical.toml") -> None:
    path = root / at
    path.parent.mkdir(parents=True, exist_ok=True)
    docs_dir = 'docs_dir = "."\n' if at.startswith("docs/") else ""
    path.write_text(f"[project]\n{docs_dir}nav = {nav}\n")


class TestCheckZensicalNav:
    def test_no_config_passes(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert main() == 0

    def test_existing_pages_pass(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(
            tmp_path, '[{ "Home" = "index.md" }, { "A" = [{ "B" = "sub/b.md" }] }]'
        )
        (tmp_path / "docs" / "sub").mkdir(parents=True)
        (tmp_path / "docs" / "index.md").write_text("# Home\n")
        (tmp_path / "docs" / "sub" / "b.md").write_text("# B\n")
        assert main() == 0

    def test_dead_entry_fails(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "Gone" = "gone_page.md" }]')
        assert main() == 1
        assert "gone_page.md" in capsys.readouterr().out

    def test_root_config_defaults_docs_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '["index.md"]', at="zensical.toml")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "index.md").write_text("# Home\n")
        assert main() == 0

    def test_urls_and_anchors(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(
            tmp_path,
            '[{ "Repo" = "https://github.com/x/y" }, { "Sec" = "index.md#part" }]',
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / "index.md").write_text("# Home\n")
        assert main() == 0

    def test_notebook_source_counts_as_generated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "Layout" = "10_layout.md" }]')
        (tmp_path / "notebooks").mkdir()
        (tmp_path / "notebooks" / "10_layout.ipynb").write_text("{}")
        assert main() == 0

    def test_stem_in_makefile_counts_as_generated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "DRC" = "drc_summary.md" }]')
        (tmp_path / "Makefile").write_text(
            "docs:\n\tcp references/drc.md docs/drc_summary.md\n"
        )
        assert main() == 0

    def test_fstring_prefix_counts_as_generated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "Models" = "models_cband.md" }]')
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "write_models.py").write_text(
            textwrap.dedent("""\
                page = PATH.repo / "docs" / f"models_{flavour}.md"
            """)
        )
        assert main() == 0

    def test_shell_prefix_counts_as_generated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "Layout" = "oband_1_layout.md" }]')
        (tmp_path / "Makefile").write_text(
            'nbdocs:\n\tnbconvert --output oband_"$$(basename $$nb .ipynb)".md\n'
        )
        assert main() == 0

    def test_mention_in_other_doc_config_does_not_count(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path, '[{ "Gone" = "gone_page.md" }]')
        (tmp_path / "mkdocs-pdf.yml").write_text("nav:\n  - gone_page.md\n")
        assert main() == 1

    def test_no_nav_passes(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zensical.toml").write_text('[project]\nsite_name = "x"\n')
        assert main() == 0

    def test_invalid_toml_fails(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zensical.toml").write_text("[project\n")
        assert main() == 1
