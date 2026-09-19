"""Execute the reusable workflow's report and badge steps without remote DRC."""

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from hooks._utils import apply_sync_markers

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/drc.yml"


def step_script(name: str, audience: str) -> str:
    data = yaml.safe_load(apply_sync_markers(WORKFLOW.read_text(), keep=audience))
    return next(s["run"] for s in data["jobs"]["drc"]["steps"] if s.get("name") == name)


@pytest.mark.parametrize("audience", ["private", "public"])
@pytest.mark.parametrize(
    "report,value",
    [
        (None, "unavailable"),
        ("", "unavailable"),
        ("<broken>", "unavailable"),
        ("<other><items/></other>", "unavailable"),
        ("<report-database/>", "unavailable"),
        ("<report-database><items/></report-database>", "0"),
        (
            '<report-database><items><item/><item id="2"/></items></report-database>',
            "2",
        ),
        (
            "<report-database><items>"
            + "<item/>" * 12000
            + "</items></report-database>",
            "12000",
        ),
    ],
    ids=[
        "missing",
        "empty",
        "malformed",
        "wrong-root",
        "no-items",
        "clean",
        "compact",
        "large-count",
    ],
)
def test_badge_content(tmp_path, audience, report, value):
    report_path = tmp_path / "build/lyrdb/all_cells.lyrdb"
    report_path.parent.mkdir(parents=True)
    if report is not None:
        report_path.write_text(report)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step_script("Create DRC badge", audience)],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if value == "unavailable" else 0)
    svg = ET.parse(tmp_path / "badges/drc.svg").getroot()
    assert svg.attrib["aria-label"] == f"DRC errors: {value}"
    assert f"**{value}**" in summary.read_text()
    assert int(svg.attrib["width"]) >= 80 + len(value) * 7 + 12
    expected_color = (
        "#9f9f9f" if value == "unavailable" else "#4c1" if value == "0" else "#e05d44"
    )
    assert svg.find("{http://www.w3.org/2000/svg}path").attrib["fill"] == expected_color


@pytest.mark.parametrize(
    "exit_code,has_report,expected",
    [(0, True, 0), (1, True, 1), (7, False, 1), (0, False, 1)],
)
def test_drc_logs_and_failure_status(tmp_path, exit_code, has_report, expected):
    if has_report:
        report = tmp_path / "build/lyrdb/all_cells.lyrdb"
        report.parent.mkdir(parents=True)
        report.write_text("<report-database><items/></report-database>")
    # A shell function replaces only the remote command; run the real workflow shell.
    fake_uv = f'uv() {{ echo "remote diagnostic" >&2; return {exit_code}; }}\n'
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", fake_uv + step_script("Run DRC", "private")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected
    assert "remote diagnostic" in (tmp_path / "build/drc.log").read_text()
    if not has_report:
        assert "DRC produced no report" in result.stdout
    if exit_code:
        assert f"DRC command exited with code {exit_code}" in result.stdout


def test_drc_log_redacts_api_key(tmp_path):
    log = tmp_path / "build/drc.log"
    log.parent.mkdir()
    api_key = "secret/with&characters"
    log.write_text(f"remote diagnostic: {api_key}")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step_script("Redact DRC log", "private")],
        cwd=tmp_path,
        env={**os.environ, "GFP_API_KEY": api_key},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert log.read_text() == "remote diagnostic: ***"
