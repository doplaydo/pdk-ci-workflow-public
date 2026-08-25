# scripts/

CLI utilities used by reusable workflows at runtime.

| Script | Used by | Description |
|--------|---------|-------------|
| `build_cell.py` | `test-sample-projects.yml` (DRC job) | Builds a named PDK cell and writes it to `build/gds/<cell_name>.gds`. Accepts `all_cells` to pack every default-instantiable PDK cell into one GDS, or a specific cell name with rglob fallback for cells not auto-registered in `pdk.cells`. |
| `generate_nyanlib.py` | `generate_nyanlib.yml` | Content-Length framed JSON-RPC 2.0 client that drives `gfp serve`: waits for cold-start indexing, lists factories, triggers `resolveFactories(renderSymbols=True)`, then waits for the resulting nyanlib cycle to drain. Produces `build/models.nyanlib` + `build/symbols/*.svg`; exits 0 cleanly when the PDK has zero factories. |
