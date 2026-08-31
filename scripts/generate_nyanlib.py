#!/usr/bin/env python3
"""Generate models.nyanlib and SVG symbols for a GDSFactory PDK.

Spawns ``gfp serve``, waits for the cold-start nyanlib cycle to finish,
optionally filters the indexed factories down to those owned by this PDK
(``--filter-to-pdk``), triggers on-demand SVG symbol generation for those in
batches, waits for the resulting nyanlib cycle to complete, then exits.

Output (relative to project root)::

    build/models.nyanlib
    build/symbols/*.svg

Communicates with the server via Content-Length framed JSON-RPC 2.0 over
stdin/stdout — the same protocol the VS Code extension and integration
tests use.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import tomllib


class StdioRpc:
    """Content-Length framed JSON-RPC 2.0 client over stdin/stdout."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._lock = threading.Lock()
        self._id = 0

    def call(
        self, method: str, params: dict | None = None, timeout: float = 30
    ) -> dict:
        with self._lock:
            self._id += 1
            req_id = self._id
            msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                msg["params"] = params
            body = json.dumps(msg).encode()
            frame = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            self.proc.stdin.write(frame)
            self.proc.stdin.flush()
            return self._read_response(req_id, timeout)

    def _read_response(self, req_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            content_length = self._read_header(deadline)
            if content_length is None:
                raise TimeoutError(
                    f"Timed out reading response for request {req_id}"
                )
            raw = self._read_exact(content_length, deadline)
            if raw is None:
                raise TimeoutError(
                    f"Timed out reading body for request {req_id}"
                )
            resp = json.loads(raw)
            if resp.get("id") == req_id:
                return resp
        raise TimeoutError(
            f"Timed out waiting for response to request {req_id}"
        )

    def _read_header(self, deadline: float) -> int | None:
        content_length = None
        while True:
            if time.monotonic() >= deadline:
                return None
            line = self.proc.stdout.readline()
            if not line:
                return None
            text = line.decode().strip()
            if text == "":
                break
            if text.startswith("Content-Length:"):
                content_length = int(text.split(":", 1)[1].strip())
        return content_length

    def _read_exact(self, n: int, deadline: float) -> bytes | None:
        buf = b""
        while len(buf) < n:
            if time.monotonic() >= deadline:
                return None
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


_FATAL_WORKER_ERROR = "Failed to initialize Python"


def _check_fatal_worker_error(log_path: Path) -> str | None:
    """Return the first line reporting a worker Python-init failure, if any.

    A worker's embedded interpreter failing to initialize is systemic, not a
    one-off — every worker hits it identically and retries forever. Detect it
    from the server log so the job fails in seconds instead of waiting out
    the full timeout.
    """
    if not log_path.is_file():
        return None
    with log_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if _FATAL_WORKER_ERROR in line:
                return line.strip()
    return None


def wait_ready(
    rpc: StdioRpc,
    proc: subprocess.Popen,
    log_path: Path,
    timeout: float = 600,
) -> None:
    """Poll getInfo until indexing and nyanlib_generating are both false."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = rpc.call("getInfo", {}, timeout=30)
            result = resp.get("result", {})
            indexing = result.get("indexing", True)
            generating = result.get("nyanlib_generating", True)
            error = result.get("index_error")
            factories = result.get("factory_count", 0)
            print(
                f"  indexing={indexing} generating={generating}"
                f" factories={factories}",
                flush=True,
            )
            if error:
                print(f"  !! index_error: {error}", file=sys.stderr, flush=True)
                sys.exit(1)
            if not indexing and not generating:
                return
        except (TimeoutError, OSError, json.JSONDecodeError) as exc:
            print(f"  poll: {exc}", flush=True)
        fatal_line = _check_fatal_worker_error(log_path)
        if fatal_line:
            print(
                f"  !! fatal worker error: {fatal_line}\n"
                "Every worker fails identically on interpreter init — this "
                "will not resolve by waiting (see build/server.log).",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        if proc.poll() is not None:
            print(
                f"gfp serve exited with code {proc.returncode}",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        time.sleep(2)
    print(
        f"Timed out after {timeout}s waiting for server readiness",
        file=sys.stderr,
        flush=True,
    )
    sys.exit(1)


def _pdk_module_name(project_root: Path) -> str:
    """Return the PDK's module name from pyproject.toml's [tool.gdsfactoryplus]."""
    pyproject_path = project_root / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        cfg = tomllib.load(f)
    gfp_cfg = cfg.get("tool", {}).get("gdsfactoryplus", {})
    name = gfp_cfg.get("pdk", {}).get("name") or gfp_cfg.get("name")
    if not name:
        sys.exit(
            f"No tool.gdsfactoryplus.pdk.name or tool.gdsfactoryplus.name found "
            f"in {pyproject_path}"
        )
    return name


def _owns(fqn: str, pdk_name: str) -> bool:
    """Return True if `fqn` belongs to the PDK named `pdk_name`."""
    return fqn == pdk_name or fqn.startswith(pdk_name + ".")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gfp-bin", required=True, help="Path to the gfp binary")
    parser.add_argument("--project-root", default=".", help="PDK project root")
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help=(
            "Timeout in seconds, reused across phases: server startup, each "
            "resolveFactories batch call, and the Phase 4 drain wait"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Factories per resolveFactories call",
    )
    parser.add_argument(
        "--filter-to-pdk",
        action="store_true",
        help=(
            "Resolve only factories owned by this PDK (per "
            "[tool.gdsfactoryplus(.pdk)].name in pyproject.toml), skipping "
            "built-in gdsfactory cells and sample-project cells. Off by "
            "default: resolves every indexed factory."
        ),
    )
    args = parser.parse_args()

    gfp_bin = Path(args.gfp_bin).resolve()
    project_root = Path(args.project_root).resolve()

    if not gfp_bin.is_file():
        print(f"gfp binary not found: {gfp_bin}", file=sys.stderr)
        sys.exit(1)

    build_dir = project_root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    log_path = build_dir / "server.log"

    print(f"Starting gfp serve (project={project_root})", flush=True)
    log_file = open(log_path, "w")  # noqa: SIM115
    proc = subprocess.Popen(
        [str(gfp_bin), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log_file,
        cwd=str(project_root),
        env={**os.environ, "RUST_LOG": "gfp=info"},
    )

    rpc = StdioRpc(proc)

    try:
        # Phase 1 — wait for cold-start indexing + skeleton nyanlib
        print("Phase 1: Waiting for indexing + cold-start nyanlib…", flush=True)
        wait_ready(rpc, proc, log_path, timeout=args.timeout)
        print("Phase 1 complete.", flush=True)

        # Phase 2 — discover all factories
        print("Phase 2: Listing factories…", flush=True)
        resp = rpc.call("listFactories", {}, timeout=30)
        factories = resp.get("result", {}).get("factories", [])
        fqns = [f["qualified_name"] for f in factories if f.get("is_factory")]

        if not fqns:
            print("No factories indexed — skipping SVG generation.", flush=True)
        else:
            if args.filter_to_pdk:
                pdk_name = _pdk_module_name(project_root)
                owned = [fqn for fqn in fqns if _owns(fqn, pdk_name)]
                print(
                    f"  {len(fqns)} factories found, {len(owned)} owned by "
                    f"'{pdk_name}' — resolving those",
                    flush=True,
                )
                if not owned:
                    print(
                        f"No factories owned by '{pdk_name}' out of "
                        f"{len(fqns)} indexed — check "
                        "[tool.gdsfactoryplus(.pdk)].name in pyproject.toml",
                        file=sys.stderr,
                        flush=True,
                    )
                    sys.exit(1)
            else:
                owned = fqns
                pdk_name = None
                print(f"  {len(fqns)} factories found — resolving all", flush=True)

            # Phase 3 — resolve the PDK's own factories with SVG symbols
            print(
                f"Phase 3: Resolving {len(owned)} factories (renderSymbols)…",
                flush=True,
            )
            batch_size = args.batch_size
            n_batches = (len(owned) + batch_size - 1) // batch_size
            total_resolved = 0
            for i in range(1, n_batches + 1):
                chunk = owned[(i - 1) * batch_size : i * batch_size]
                try:
                    resp = rpc.call(
                        "resolveFactories",
                        {"fqns": chunk, "renderSymbols": True},
                        timeout=args.timeout,
                    )
                except TimeoutError:
                    print(
                        f"  batch {i}/{n_batches} timed out",
                        file=sys.stderr,
                        flush=True,
                    )
                    raise
                resp_error = resp.get("error")
                if resp_error:
                    print(
                        f"  batch {i}/{n_batches} returned an error: {resp_error}",
                        file=sys.stderr,
                        flush=True,
                    )
                    raise RuntimeError(
                        f"resolveFactories batch {i}/{n_batches} failed: {resp_error}"
                    )
                resolved = resp.get("result", {}).get("resolved", [])
                total_resolved += len(resolved)
                print(
                    f"  batch {i}/{n_batches}: {len(resolved)}/{len(chunk)} resolved",
                    flush=True,
                )
            print(f"  {total_resolved}/{len(owned)} resolved", flush=True)
            if total_resolved == 0:
                owned_desc = f"owned by '{pdk_name}'" if pdk_name else "requested"
                print(
                    f"No factories resolved out of {len(owned)} {owned_desc} "
                    "— refusing to proceed with an incomplete nyanlib cache",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)

            # Phase 4 — wait for the SVG nyanlib cycle to drain
            print("Phase 4: Waiting for SVG nyanlib cycle…", flush=True)
            wait_ready(rpc, proc, log_path, timeout=args.timeout)
            print("Phase 4 complete.", flush=True)

        # Report outputs
        nyanlib_path = project_root / "build" / "models.nyanlib"
        symbols_dir = project_root / "build" / "symbols"
        if nyanlib_path.is_file():
            size = nyanlib_path.stat().st_size
            print(f"Output: {nyanlib_path} ({size} bytes)")
        else:
            print("WARNING: build/models.nyanlib not found", file=sys.stderr)
            sys.exit(1)
        svg_count = len(list(symbols_dir.glob("*.svg"))) if symbols_dir.is_dir() else 0
        print(f"Output: {svg_count} SVG symbols in build/symbols/")

    finally:
        if proc.stdin:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log_file.close()


if __name__ == "__main__":
    main()
