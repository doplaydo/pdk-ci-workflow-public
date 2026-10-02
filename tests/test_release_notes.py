"""Exercise the release workflow's changelog extraction and Markdown output."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from hooks._utils import apply_sync_markers


@pytest.fixture(params=["private", "public"])
def extract_notes(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str | None], str]:
    """Run the actual publishing step without invoking GitHub or a release."""
    workflow_path = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    )
    workflow = yaml.safe_load(
        apply_sync_markers(workflow_path.read_text(), keep=request.param)
    )
    step = next(
        step
        for step in workflow["jobs"]["github-release"]["steps"]
        if step.get("id") == "changelog"
    )
    script = step["run"].split("python3 <<'PYEOF'\n", 1)[1].rsplit("PYEOF", 1)[0]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_VERSION", "0.1.0")

    def extract(changelog: str | None) -> str:
        if changelog is not None:
            (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
        exec(compile(script, str(workflow_path), "exec"), {})
        return (tmp_path / "release_notes.md").read_text(encoding="utf-8")

    return extract


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "add type tags to all @cell components (#74)",
            "add type tags to all `@cell` components (#74)",
        ),
        ("Support @cell().", "Support `@cell`()."),
        ("Support @vcell and @gf.cell.", "Support `@vcell` and `@gf.cell`."),
        (
            "Support @gdsfactory.cell and @gf.vcell",
            "Support `@gdsfactory.cell` and `@gf.vcell`",
        ),
        ("Keep `@cell` and `@gf.cell()`", "Keep `@cell` and `@gf.cell()`"),
        ("Keep `example: @cell`", "Keep `example: @cell`"),
        ("Keep `` `@cell` and @gf.cell ``", "Keep `` `@cell` and @gf.cell ``"),
        (
            "Thanks @joamatab and @cell-owner; notify user@cell.com",
            "Thanks @joamatab and @cell-owner; notify user@cell.com",
        ),
        (
            "Keep @cells, @cell_owner, https://example.com/@cell and \\@cell",
            "Keep @cells, @cell_owner, https://example.com/@cell and \\@cell",
        ),
    ],
)
def test_decorators_do_not_become_contributor_mentions(
    extract_notes: Callable[[str | None], str], description: str, expected: str
) -> None:
    changelog = f"# [0.1.0](release-url) - 2026-09-24\n\n## New\n- {description}\n"
    assert extract_notes(changelog) == f"## New\n- {expected}"


@pytest.mark.parametrize(
    ("opening", "closing"), [("```", "```"), ("~~~~", "~~~~"), ("```", "````")]
)
def test_fenced_code_is_preserved(
    extract_notes: Callable[[str | None], str], opening: str, closing: str
) -> None:
    body = (
        f"{opening}python\n@cell\ndef example():\n    pass\n{closing}\n\nUse @gf.cell"
    )
    changelog = f"# [0.1.0](release-url)\n\n{body}\n"
    assert extract_notes(changelog) == body.replace("Use @gf.cell", "Use `@gf.cell`")


def test_unmatched_backtick_cannot_hide_later_decorators(
    extract_notes: Callable[[str | None], str],
) -> None:
    body = "- Fix `bar (#1)\n- Add @cell support (#2)\n- Use `baz` (#3)"
    changelog = f"# [0.1.0](release-url)\n\n{body}\n"
    assert extract_notes(changelog) == body.replace("@cell", "`@cell`")


@pytest.mark.parametrize("changelog", [None, "", "# Changelog\n", "# [0.0.9]\n@cell"])
def test_missing_or_stale_changelog_does_not_publish_notes(
    extract_notes: Callable[[str | None], str], changelog: str | None
) -> None:
    assert extract_notes(changelog) == ""


def test_only_current_release_is_included(
    extract_notes: Callable[[str | None], str],
) -> None:
    changelog = (
        "# [0.1.0](current)\n\n- Update @cell\n\n# [0.0.9](previous)\n- Old change\n"
    )
    assert extract_notes(changelog) == "- Update `@cell`"
