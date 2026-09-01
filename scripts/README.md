# scripts/

CLI utilities used by reusable workflows at runtime.

| Script | Used by | Description |
|--------|---------|-------------|
| `build_cell.py` | `test-sample-projects.yml` (DRC job) | Builds a named PDK cell and writes it to `build/gds/<cell_name>.gds`. Accepts `all_cells` to pack every default-instantiable PDK cell into one GDS, or a specific cell name with rglob fallback for cells not auto-registered in `pdk.cells`. |
| `generate_nyanlib.py` | `generate_nyanlib.yml` | Content-Length framed JSON-RPC 2.0 client that drives `gfp serve`: waits for cold-start indexing, lists factories, filters them down to the ones owned by this PDK if requested, resolves those in batches via `resolveFactories(renderSymbols=True)`, then waits for the resulting nyanlib cycle to drain. Produces `build/models.nyanlib` + `build/symbols/*.svg`; exits 0 cleanly when the PDK has zero factories, exits non-zero if any batch errors or zero factories resolve. |
| `gds_xor_report.py` | `gds-xor.yml` | Pairs up the top cells of two revisions of a GDS file and XORs them layer by layer with KLayout. Writes `xor/<slug>.xor.gds` (XOR shapes kept on their original layer/datatype, beside copies of both inputs), a `report.pdf` with a summary table plus one page per differing layer, `png/` with the same per-layer figures as standalone images, `comment.md` (the summary plus one collapsed image section per layer, each image left as an `IMAGE:<path>` placeholder for the caller to upload and rewrite), and `summary.md` / `summary.json` with the added, removed, and total difference area in um^2. A missing file on either side counts as an empty layout, so added and deleted GDS files are reported too. Needs only `klayout` and `matplotlib`, so the workflow runs it in a throwaway `uv --no-project` env rather than the PDK's. |
| `check_model_jittability.py` | `model_regression.yml` | Activates the configured PDK and JITs every registered SAX model with all numeric default parameters. Models that fail with concrete defaults are reported as skipped; trace-only failures identify the affected model and isolate the numeric parameters that trigger them. PDKs can configure `skip_models` and per-model `static_parameters` under `[tool.gdsfactoryplus.pdk.model_jittability]`. |

## Model jittability settings

Integer defaults are traced because many physical parameters use integer-valued defaults. Mark integer shape controls and other structural values as static; skip only models that cannot be exercised with defaults:

```toml
[tool.gdsfactoryplus.pdk.model_jittability]
skip_models = ["model_requiring_fixture"]

[tool.gdsfactoryplus.pdk.model_jittability.static_parameters]
"*" = ["nmodes"]
multimode_model = ["npoints"]
```
