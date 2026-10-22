# scripts/

CLI utilities used by reusable workflows at runtime.

| Script | Used by | Description |
|--------|---------|-------------|
| `check_wheel_contents.py` | `test_code.yml`, `release.yml` via `actions/check_wheel_contents` | Inspects every built wheel without extracting or importing it. Fails on reserved reference directories or `.git` metadata anywhere in archive paths, empty builds, unreadable wheels, and Hatch `force-include`, `sources`, `shared-data`, `shared-scripts`, or `extra-metadata` mappings whose source or destination includes those directories. Required runtime assets belong in the PDK package's ordinary runtime directories. Uses Python 3.12 standard-library modules in CI. |
| `build_cell.py` | `sample-project-test.yml` (DRC steps) | Builds a named PDK cell and writes it to `build/gds/<cell_name>.gds`. Accepts `all_cells` to pack every default-instantiable PDK cell into one GDS, or a specific cell name with rglob fallback for cells not auto-registered in `pdk.cells`. |
| `gds_xor_report.py` | `gds-xor.yml` | Pairs up the top cells of two revisions of a GDS file and XORs them layer by layer with KLayout. Writes `xor/<slug>.xor.gds` (XOR shapes kept on their original layer/datatype, beside copies of both inputs), a `report.pdf` with a summary table plus one page per differing layer, `png/` with the same per-layer figures as standalone images, `comment.md` (the summary plus one collapsed image section per layer, each image left as an `IMAGE:<path>` placeholder for the caller to upload and rewrite), and `summary.md` / `summary.json` with the added, removed, and total difference area in um^2. A missing file on either side counts as an empty layout, so added and deleted GDS files are reported too. Needs only `klayout` and `matplotlib`, so the workflow runs it in a throwaway `uv --no-project` env rather than the PDK's. |
| `sample_project_ci.py` | `sample-projects.yml`, `sample-project-test.yml` | Discovers and packages committed projects, resolves PDK distributions/variants, edits extracted pyprojects, runs notebooks collecting failures, parses skipped tests, and renders checks/exclusions summaries. Uses `tomlkit` for edits. |
| `check_model_jittability.py` | `model_regression.yml` | Activates the configured PDK and JITs every registered SAX model with all numeric default parameters. Models that fail with concrete defaults are reported as skipped; trace-only failures identify the affected model and isolate the numeric parameters that trigger them. PDKs can configure `skip_models` and per-model `static_parameters` under `[tool.gdsfactoryplus.pdk.model_jittability]`. |
| `check_model_metadata.py` | `model_regression.yml` | Validates SAX/Circulax factory bindings and detects assignments available only through `PDK.models` or legacy schematic info. Runs configured PDK variants in isolated processes; `model_metadata_cli.py` handles reports and annotations. |

## Sample-project helper interfaces

`sample_project_ci.py` is run with
`uv run --no-project --with tomlkit python <script> <command>`:

| Command | Contract |
|---|---|
| `discover` | From repo root, prints JSON `{slug, project_dir}` entries for `*--sample-projects/*/pyproject.toml`, excluding `.venv` |
| `package <project_dir> --out <dir> --source-repo <owner/repo> --source-sha <sha>` | Reads root/sample pyprojects from HEAD, writes `sample-project.zip` via `git archive --format=zip HEAD:<project_dir>` and `meta.json`; committed files at archive root, nothing stripped |
| `edit-pyproject <path> [--editable-source DIST=PATH] [--gfp-version V]` | Replaces the distribution's uv source with an editable path and/or adds a gdsfactoryplus constraint; only edits the extracted test copy |
| `run-notebooks <project_root> [--out <report.json>]` | Attempts every selected notebook, collects failures, returns nonzero if any failed; default report is `$RUNNER_TEMP/notebooks.json`, falling back to system temp, never the project directory |
| `summary --checks '<JSON object>' [--notebooks <report.json>] [--exclusions <list.json> ...]` | Renders a checks/results/notes table and all exclusions; notebook reports and exclusion string lists have distinct formats |

Notebook reports contain `run`, `excluded`, `failed` lists and a
`skip_execution` map of relative paths to tagged-cell counts. Name patterns
`hfss_|hfss-|matlab_|matlab-|_meep|meep_` are excluded and reported; selected
notebook failures do not stop later notebooks. Summaries include full trimmed
`SKIPPED` lines from `gfp test` and missing DRC fixtures as exclusions.
The workflow runs DRC smoke steps in both modes, but its collector never
requires them. Non-success DRC outcomes emit project/step warnings and are
rendered by `summary` with `(not blocking)`, for example `failed (not blocking)`.
They never fail the job or prevent passed-test evidence and upload when all
non-DRC checks pass.
Metadata includes `project_dir`, `slug`, `pdk_distribution`, `pdk_import_name`,
`tree_sha`, `source_repo`, and `source_sha`.

Public workflows use the shared CI helper to test and publish artifacts only.
A separate private central service uploads eligible passed released artifacts
as unchanged ZIP bytes without executing public source code under its admin
key. The portal strips nothing, including committed `build/`; that service's
optional downstream mirror alone omits top-level `build/`.

## Model metadata validation

Run the checker in the PDK's own installed environment, from its repository:

```bash
uv run --no-sync /path/to/pdk-ci-workflow/scripts/check_model_metadata.py --json /tmp/model-metadata-report.json
```

The default strict check requires SAX/Circulax `metadata.models` for registered
cells that already have a same-name `PDK.models` entry or a SAX/Circulax model
in legacy schematic `info.models`. Generic registry keys
without a corresponding cell are not treated as cell assignments. Cells with
no implemented model do not need invented bindings. Providers must load through
normal PDK initialization; the checker does not import a schematic module on
their behalf or resolve a colliding global short-name factory.

Every declared binding is imported, checked against the built default cell's
terminals and settings, and evaluated (SAX) or constructed (Circulax). Model-only
metadata providers may omit symbol ports; the actual cell interface is used
in that case. Explicit symbol declarations are checked against layout ports.
Errors remain failures rather than silently falling back to `PDK.models`. A Circulax
`port_map` is checked against the model interface; this does not establish that
every consumer applies it. Runtime simulation and physical parity remain
separate checks.

By default, the checker reads the configured PDK name and options from
`pyproject.toml`, deduplicates them, and uses a fresh interpreter for each
variant. One import failure does not prevent checking the remaining variants.
Use `--pdk` to select one import path, `--bindings-only` to
diagnose existing bindings without requiring migration, or repeated
`--expect-cell` arguments to guard discovery of metadata-only assignments.
Exit codes are 0 for a clean check, 1 for findings, and 2 for setup failure.
Reports distinguish `passed`, `failed`, `not-applicable`, and `setup-failed`.
`not-applicable` means no SAX/Circulax metadata binding was validated. In the
default strict mode, legacy assignments produce findings; with `--bindings-only`,
their migration is skipped and their counts remain in the report. SPICE and
other-language bindings are counted separately without
requiring fake SAX/Circulax models. This check cannot discover arbitrary custom
dispatch hidden outside the PDK's factory
metadata, legacy schematic info, and model registry. It checks default cell
settings, not the full parameter domain or foundry-model accuracy.

The shared workflow defaults to a nonblocking audit: warnings, per-PDK step
summaries, and a `model-metadata-report` JSON artifact. Once its PDKs have
migrated, repositories using the canonical caller template can set the GitHub
repository variable `ENFORCE_MODEL_METADATA` to `true`. The template forwards
that setting, so it survives `check-template-drift` syncing the workflow.
Custom callers can enable enforcement directly:

```yaml
with:
  enforce-model-metadata: true
```

`enforce-model-metadata` defaults to `false`. The checker still returns a
nonzero exit code for findings or setup failures; the composite action ignores
that exit code unless enforcement is enabled. (Step-level `continue-on-error`
expressions are not honoured for composite actions, so the action decides.) Existing `PDK.models`
entries can remain as compatibility fallbacks, but do not satisfy the
metadata requirement on their own.

## Model jittability settings

Integer defaults are traced because many physical parameters use integer-valued defaults. Mark integer shape controls and other structural values as static; skip only models that cannot be exercised with defaults:

```toml
[tool.gdsfactoryplus.pdk.model_jittability]
skip_models = ["model_requiring_fixture"]

[tool.gdsfactoryplus.pdk.model_jittability.static_parameters]
"*" = ["nmodes"]
multimode_model = ["npoints"]
```
