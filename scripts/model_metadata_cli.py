"""Reporting and isolated variant execution for the model metadata checker."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any


def configured_pdks(pyproject_path: Path) -> list[str]:
    with pyproject_path.open("rb") as project_file:
        configuration = tomllib.load(project_file)
    pdk = configuration.get("tool", {}).get("gdsfactoryplus", {}).get("pdk", {})
    name = pdk.get("name")
    options = pdk.get("options", [])
    if not isinstance(name, str) or not name:
        raise ValueError("No [tool.gdsfactoryplus.pdk] name found in pyproject.toml")
    if not isinstance(options, list) or not all(
        isinstance(option, str) and option for option in options
    ):
        raise ValueError("PDK options must be a list of non-empty import paths")
    return list(dict.fromkeys([name, *options]))


def annotation(detail: str) -> None:
    level = (
        "error"
        if os.environ.get("PDK_MODEL_METADATA_ENFORCE", "false").lower() == "true"
        else "warning"
    )
    escaped = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title=PDK model metadata::{escaped}")


def write_report(report: dict[str, Any], output_path: Path | None) -> None:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + "\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a") as summary:
            summary.write(
                f"## Model metadata: {report['pdk']}\n\nStatus: **{report['status']}**\n\n"
            )
            if report["status"] == "setup-failed":
                summary.write(report["detail"].replace("\n", " ") + "\n\n")
            else:
                summary.write(
                    f"Metadata cells: {report['metadata_modeled_cells']}; "
                    f"valid bindings: {report['passed_bindings']}; "
                    f"registry-modeled cells: {report['registry_modeled_cells']}; "
                    f"legacy schematic model cells: {report['legacy_schematic_modeled_cells']}.\n\n"
                )
                for issue in report["issues"]:
                    detail = issue["detail"].replace("\n", " ")
                    summary.write(f"- `{issue['cell']}` ({issue['code']}): {detail}\n")
                if report["status"] == "not-applicable":
                    summary.write(
                        "No SAX/Circulax metadata bindings were validated. Legacy assignments, if any, are counted above.\n"
                    )
                if report["other_language_cells"]:
                    summary.write(
                        f"\n{report['other_language_cells']} cells have bindings for other languages outside this check.\n"
                    )


def run_one(name: str, arguments: Any, audit_pdk: Callable[..., Any]) -> int:
    try:
        module = importlib.import_module(name)
        result = audit_pdk(
            module.PDK,
            name,
            require_metadata=not arguments.bindings_only,
            expected_cells=tuple(arguments.expect_cell),
        )
    except Exception as error:  # noqa: BLE001 - distinguish import failures from findings
        detail = f"Model metadata setup failed: {type(error).__name__}: {error}"
        print(detail)
        annotation(f"{name}: {detail}")
        write_report(
            {"pdk": name, "status": "setup-failed", "detail": detail}, arguments.json
        )
        return 2
    report = asdict(result)
    report["status"] = (
        "failed"
        if not result.passed
        else "passed"
        if result.passed_bindings
        else "not-applicable"
    )
    for issue in result.issues:
        detail = f"{name}.{issue.cell} [{issue.code}]: {issue.detail}"
        print(f"FAIL {detail}")
        annotation(detail)
    print(
        f"{name}: {result.metadata_modeled_cells} cells with model metadata, {result.registry_modeled_cells} registry-modeled cells, {result.legacy_schematic_modeled_cells} legacy schematic model cells, {result.passed_bindings} valid bindings, {len(result.issues)} issues"
    )
    if report["status"] == "not-applicable":
        print(
            "No SAX/Circulax metadata bindings were validated. Legacy assignments, if any, are counted above."
        )
    if result.other_language_cells:
        print(
            f"{result.other_language_cells} cells have other-language bindings outside this check."
        )
    write_report(report, arguments.json)
    return 0 if result.passed else 1


def run_variants(names: list[str], arguments: Any, script_path: Path) -> int:
    reports = []
    exit_codes = []
    with tempfile.TemporaryDirectory(
        prefix="pdk-model-metadata-"
    ) as temporary_directory:
        for index, name in enumerate(names):
            report_path = Path(temporary_directory) / f"{index}.json"
            command = [
                sys.executable,
                str(script_path),
                "--pdk",
                name,
                "--json",
                str(report_path),
            ]
            if arguments.bindings_only:
                command.append("--bindings-only")
            for cell in arguments.expect_cell:
                command.extend(["--expect-cell", cell])
            # Each variant gets a clean kfactory registry and its own active PDK.
            result = subprocess.run(command, check=False)
            exit_codes.append(
                result.returncode if result.returncode in (0, 1, 2) else 2
            )
            if report_path.exists():
                reports.append(json.loads(report_path.read_text()))
            else:
                detail = (
                    f"PDK process ended without a report (exit {result.returncode})"
                )
                annotation(f"{name}: {detail}")
                reports.append(
                    {"pdk": name, "status": "setup-failed", "detail": detail}
                )
                exit_codes.append(2)
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps({"pdks": reports}, indent=2) + "\n")
    return max(exit_codes, default=2)


def main(
    argv: list[str] | None, audit_pdk: Callable[..., Any], script_path: Path
) -> int:
    parser = argparse.ArgumentParser(
        description="Check SAX/Circulax metadata and require migration of existing model assignments."
    )
    parser.add_argument(
        "--pdk", help="Select one import path; defaults to all configured PDK options"
    )
    parser.add_argument(
        "--bindings-only",
        action="store_true",
        help="Validate metadata without requiring legacy assignments to migrate",
    )
    parser.add_argument(
        "--expect-cell",
        action="append",
        default=[],
        help="Require a metadata-only cell assignment; repeat as needed",
    )
    parser.add_argument("--json", type=Path, help="Write a JSON report to this path")
    arguments = parser.parse_args(argv)
    if arguments.pdk:
        return run_one(arguments.pdk, arguments, audit_pdk)
    try:
        names = configured_pdks(Path("pyproject.toml"))
    except Exception as error:  # noqa: BLE001 - report configuration failure
        detail = f"Model metadata setup failed: {type(error).__name__}: {error}"
        print(detail)
        annotation(detail)
        write_report(
            {"pdk": "configuration", "status": "setup-failed", "detail": detail},
            arguments.json,
        )
        return 2
    return run_variants(names, arguments, script_path)
