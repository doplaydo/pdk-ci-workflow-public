# scripts/

CLI utilities used by reusable workflows at runtime.

| Script | Used by | Description |
|--------|---------|-------------|
| `build_cell.py` | `test-sample-projects.yml` (DRC job) | Builds a named PDK cell and writes it to `build/gds/<cell_name>.gds`. Accepts `all_cells` to pack every default-instantiable PDK cell into one GDS, or a specific cell name with rglob fallback for cells not auto-registered in `pdk.cells`. |
