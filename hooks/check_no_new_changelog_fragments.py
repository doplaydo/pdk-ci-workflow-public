"""Reject newly added changelog fragments without blocking historical files."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from hooks._utils import CheckResult, is_self_repo

TEMPLATE = ".changelog.d/changelog_template.jinja"


def _git(*args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], input=stdin, capture_output=True, check=True, timeout=10
    ).stdout


def _comparison_base() -> str | None:
    """Use the event's old tree in CI, or the local default-branch merge base."""
    event = os.environ.get("GITHUB_EVENT_NAME")
    if os.environ.get("GITHUB_ACTIONS") == "true" and event in {
        "pull_request",
        "push",
    }:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        base = (
            payload["pull_request"]["base"]["sha"]
            if event == "pull_request"
            else payload["before"]
        )
        if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
            raise ValueError("missing or invalid GitHub event base SHA")
        if base == "0" * 40:
            if event != "push":
                raise ValueError("unexpected empty pull-request base SHA")
            default = payload["repository"]["default_branch"]
            if payload["ref"] != f"refs/heads/{default}":
                return (
                    _git("merge-base", f"refs/remotes/origin/{default}", "HEAD")
                    .decode()
                    .strip()
                )
            # First push to the default branch: compare the whole snapshot.
            return (
                _git("hash-object", "-t", "tree", "--stdin", stdin=b"").decode().strip()
            )
        return base

    try:
        return _git("merge-base", "refs/remotes/origin/HEAD", "HEAD").decode().strip()
    except subprocess.CalledProcessError:
        # No remote default branch, no history, or a shallow local clone:
        # without an explicit tree, git compares the index against HEAD.
        return None


def main() -> int:
    """Report paths newly introduced into the root changelog directory."""
    result = CheckResult("check-no-new-changelog-fragments")
    if is_self_repo():
        return result.report()

    try:
        base = _comparison_base()
        args = [
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=A",
        ]
        if base is not None:
            args.append(base)
        paths = _git(*args, "--", ".changelog.d").split(b"\0")
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        event = os.environ.get("GITHUB_EVENT_NAME", "unknown")
        context = (
            f"GitHub {event!r} event"
            if os.environ.get("GITHUB_ACTIONS") == "true"
            else "local Git index"
        )
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail += f"\n{os.fsdecode(exc.stderr).strip()}"
        result.error(
            "Could not compare changelog paths with the Git base. Check the "
            "GitHub event payload and checkout history, or run in a Git checkout. "
            f"Context: {context}. Details: {detail}"
        )
        return result.report()

    for path in paths:
        if path and os.fsdecode(path) != TEMPLATE:
            result.error(
                f"New changelog fragment {os.fsdecode(path)!r}. "
                "Use a release-note-ready PR title and the appropriate labels instead."
            )
    return result.report()


if __name__ == "__main__":
    raise SystemExit(main())
