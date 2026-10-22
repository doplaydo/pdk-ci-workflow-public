"""Run the actual release health gate offline with paginated check-run fixtures."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EDITABLE_CHECK = "test-sample-projects / test / Test examplepdk--sample-projects/demo"
EXACT_CHECKS = [
    "test / pre-commit",
    "test / test_code",
    "test / test_gfp",
    "test / test_gfp_no_dev",
]


def check_run(
    name: str, status: str = "completed", conclusion: str | None = "success"
) -> dict[str, str | None]:
    return {"name": name, "status": status, "conclusion": conclusion}


@pytest.fixture
def run_gate(
    tmp_path: Path,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/cut-release.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["cut-release"]["steps"]
        if step.get("id") == "main_health"
    )
    script = step["run"].replace("${{ github.repository }}", "acme/examplepdk")
    # Emulate gh's output shape for both the old and paginated invocation.
    # No real GitHub API call is made, even during the pre-change red run.
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" > "$GH_ARGS"\n'
        'if [[ " $* " == *" --slurp "* ]]; then\n'
        '  cat "$GH_PAGES"\n'
        "else\n"
        '  jq ".[0]" "$GH_PAGES"\n'
        "fi\n"
    )
    gh.chmod(0o755)

    def run(
        samples: bool,
        checks: list[dict[str, str | None]],
        *,
        second_page: bool = False,
        missing_exact: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if samples:
            sample = tmp_path / "examplepdk--sample-projects/demo/pyproject.toml"
            sample.parent.mkdir(parents=True, exist_ok=True)
            sample.write_text('[project]\nname = "demo"\n')
        exact = [check_run(name) for name in EXACT_CHECKS]
        if missing_exact:
            exact.pop()
        pages = [{"check_runs": exact + ([] if second_page else checks)}]
        if second_page:
            pages.append({"check_runs": checks})
        pages_path = tmp_path / "pages.json"
        pages_path.write_text(json.dumps(pages))
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            cwd=tmp_path,
            check=False,
            env={
                **os.environ,
                "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                "REPO": "acme/examplepdk",
                "GH_PAGES": str(pages_path),
                "GH_ARGS": str(tmp_path / "gh-args"),
            },
            text=True,
            capture_output=True,
            timeout=10,
        )

    return run


def test_gate_fetches_all_pages(run_gate: Callable, tmp_path: Path) -> None:
    result = run_gate(True, [check_run(EDITABLE_CHECK)], second_page=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "gh-args").read_text().splitlines() == [
        "api",
        "--paginate",
        "--slurp",
        "repos/acme/examplepdk/commits/main/check-runs?per_page=100",
    ]
    assert EDITABLE_CHECK in result.stdout


@pytest.mark.parametrize("checks", [[], [check_run("released / test / Test demo")]])
def test_samples_require_editable_check(run_gate: Callable, checks: list) -> None:
    result = run_gate(True, checks)
    assert result.returncode == 1
    assert "::error::no test-sample-projects check on main HEAD" in result.stdout


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting"])
@pytest.mark.parametrize("samples", [False, True])
def test_incomplete_matching_check_blocks_release(
    run_gate: Callable, status: str, samples: bool
) -> None:
    result = run_gate(samples, [check_run(EDITABLE_CHECK, status, None)])
    assert result.returncode == 1
    assert (
        f"::error::{EDITABLE_CHECK} still running - retry once it finishes"
        in result.stdout
    )


@pytest.mark.parametrize("second_page", [False, True])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_failed_matching_check_blocks_release(
    run_gate: Callable, second_page: bool, conclusion: str
) -> None:
    result = run_gate(
        True,
        [check_run(EDITABLE_CHECK, conclusion=conclusion)],
        second_page=second_page,
    )
    assert result.returncode == 1
    assert "required pattern-matched check(s) not passing" in result.stdout


@pytest.mark.parametrize("conclusion", ["success", "skipped"])
def test_completed_passing_or_skipped_check_is_allowed(
    run_gate: Callable, conclusion: str
) -> None:
    result = run_gate(True, [check_run(EDITABLE_CHECK, conclusion=conclusion)])
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_samples_does_not_require_editable_check(run_gate: Callable) -> None:
    result = run_gate(False, [])
    assert result.returncode == 0, result.stdout + result.stderr


def test_released_and_unrelated_checks_are_not_release_gates(
    run_gate: Callable,
) -> None:
    result = run_gate(
        True,
        [
            check_run(EDITABLE_CHECK),
            check_run("released / test / Test demo", conclusion="failure"),
            check_run("test / drc", "in_progress", None),
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_checks_remain_required(run_gate: Callable) -> None:
    result = run_gate(False, [], missing_exact=True)
    assert result.returncode == 1
    assert (
        "::error::Required check not passing: 'test / test_gfp_no_dev'" in result.stdout
    )


@pytest.mark.parametrize(
    "path", [".github/workflows/release.yml", "templates/.github/workflows/release.yml"]
)
def test_release_has_no_sample_upload_caller_or_sync_secret(path: str) -> None:
    text = (REPO_ROOT / path).read_text()
    assert "upload-sample-projects" not in text
    assert "sample-project-upload.yml" not in text
    assert "GSP_SYNC_TOKEN" not in text
