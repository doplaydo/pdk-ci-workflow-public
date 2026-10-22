# Reusable Workflows

Reusable workflows are complete, self-contained workflow definitions triggered via `workflow_call`. When a PDK repo calls a reusable workflow, it delegates the entire job — the workflow controls the runner, permissions, steps, and secret handling. The calling repo just says "run this job for me."

PDK repos create thin wrapper workflows that call these and forward secrets explicitly. See `templates/.github/workflows/` for ready-to-copy wrappers.

The docs workflow accepts an optional `runner` input, defaulting to `ubuntu-latest`. Its wrapper reads the `DOCS_RUNNER` repository variable; set it to an available runner label (for example, `ubuntu-8core`) to select a larger docs build machine.


## Workflows

| Workflow | Jobs | Secrets Used | Description |
|----------|------|-------------|-------------|
| `test_code.yml` | wheel-contents, pre-commit, test_code, test_gfp, test_gfp_no_dev | `GFP_API_KEY` | Builds and checks wheel reference-file exclusion, pre-commit (fetches canonical config), pytest, GFP validation |
| `sample-projects.yml` | discover, package, test | `GFP_API_KEY` (optional) | PDK entry point: committed ZIP packages → shared suite; never uploads |
| `sample-project-test.yml` | test | `GFP_API_KEY` (optional) | Shared per-project suite with editable or released PDK; publishes passed-test artifacts only |
| `pages.yml` | build-docs | `GFP_API_KEY`, `SIMCLOUD_APIKEY` | Sphinx docs build and Pages artifact upload. The caller's wrapper adds the `deploy-docs` job |
| `claude-pr-review.yml` | review | `ANTHROPIC_API_KEY` | AI code review via Claude Sonnet 4. Auto-runs on PR open/reopen; re-runs only when a human posts `/claude-api review` on the PR |
| `drc.yml` | drc | `GFP_API_KEY` | Design Rule Check with badge generation |
| `gds-xor.yml` | gds-xor | `GITHUB_TOKEN` | Per-layer XOR of every `*.gds` a PR changes: uploads the XOR GDS plus a PDF report with one page per differing layer, and posts a sticky comment with the difference area in um^2. Inline images need an attachment upload, which is attributed to a user account, so with only `GITHUB_TOKEN` the comment links the PDF instead |
| `code-security.yml` | semgrep, trivy | — | Semgrep (`p/ci`, `p/secrets`) and Trivy (vuln, misconfig, secret; HIGH/CRITICAL with a fix available) filesystem scans with public tooling and read-only access. Findings fail the job and are published to the job summary and as artifacts. To accept a finding, use repo-local suppression: `.trivyignore` (CVE IDs), `.semgrepignore` (paths), or inline `# nosemgrep: <rule-id>` |
| `issue.yml` | add-label | `GITHUB_TOKEN` | Auto-labels issues with the `pdk` tag plus a per-repository `pdk:<repo-name>` tag |
| `test_coverage.yml` | coverage | `GFP_API_KEY` | Pytest with line coverage reporting |
| `model_coverage.yml` | model-coverage | `GFP_API_KEY` | PDK model-to-cell coverage check |
| `model_regression.yml` | model-regression | `GFP_API_KEY` | Model regression tests plus JAX and metadata audits; strict metadata enforcement is opt-in via `enforce-model-metadata` |
| `update_badges.yml` | badges | `GFP_API_KEY`, `GITHUB_TOKEN` | Generate coverage, model, issue, and PR badges |
| `build-pdf.yml` | build-pdf | `GFP_API_KEY`, `SIMCLOUD_APIKEY` | Build PDF docs on demand; uploads artifact and optionally attaches to a release |

## Sample-project contract

`sample-projects.yml` discovers `*--sample-projects/*/pyproject.toml`, packages
committed content, then calls `sample-project-test.yml`. Both accept
`pdk_source` (`editable` or `released`), `gfp_version` (empty = latest), and
`script_ref` (default `main`, selects runtime helper downloads). Direct suite
callers supply `projects`, a JSON list of `{slug, project_dir}` objects, and
publish the package artifacts themselves. `slug` replaces `/` with `-` and
strips leading/trailing `-`.

| Artifact (7-day retention) | Contents |
|---|---|
| `sample-project-<slug>` | `sample-project.zip` and `meta.json` |
| `sample-project-tested-<pdk_source>-<slug>` | `meta.json`, `installed-version.txt`, `summary.json`; only when every required check passed |

The ZIP is `git archive --format=zip HEAD:<project_dir>`, committed files at
archive root, with nothing stripped. `meta.json` records `project_dir`, `slug`,
`pdk_distribution`, `pdk_import_name`, `tree_sha`, `source_repo`, and `source_sha`.
Packaging reads both root and sample pyprojects from HEAD. The portal ZIP is
never regenerated from the test working directory.

The suite extracts into `$RUNNER_TEMP/project` outside the checkout:

- **Editable:** checks out the caller PDK, runs `make prepare-<variant>` when
  applicable and available, and replaces the PDK distribution's uv source with
  that editable checkout, even if an entry already exists.
- **Released:** installs from the package index with no source override added
  or ignored. A committed path source is not hidden.
- Both optionally constrain gdsfactoryplus, configure its index with retries,
  install system dependencies, run `uv sync --no-dev` and `gfp test -v`, then
  `uv sync --all-extras --all-groups`, record the installed PDK version, and
  run `gfp test -v` again.
- All selected notebooks are attempted, collecting failures before failing.
  Paths matching `hfss_|hfss-|matlab_|matlab-|_meep|meep_` are excluded and
  listed, as are `skip-execution`-tagged cell counts and full `gfp test`
  `SKIPPED` lines. DRC smoke-tests `sample_drc_errors.py` when present; absence
  is listed as an exclusion. DRC expects errors unless `allow-zero-errors` is
  set, retries the service, and redacts the key from report logs. Fixture lookup,
  cell building, DRC, verification, redaction, and report upload run in both
  modes and are always diagnostic, never required. Non-success outcomes emit
  `::warning::` with the project and step and appear as `failed (not blocking)`,
  `skipped (not blocking)`, or the corresponding outcome in the summary. DRC
  never fails either mode's job or withholds passed-test evidence: passing all
  non-DRC checks in released mode is sufficient for upload eligibility.
- Check steps continue to collect results; the final summary table and
  exclusions determine job failure, rather than silently hiding failures.
  Project matrices do not fail fast, and empty project lists skip consumers.

Canonical check names are `test-sample-projects / test / Test <dir>` for the
editable caller and `released / test / Test <dir>` for the released caller.
Only editable checks feed the release gate; released failures are visible red
checks that withhold that project's upload, not a merge gate.

### Public upload boundary

The public entry point has no upload inputs/job and needs no mirror token.
It only tests and publishes artifacts. A private central service polls
allowlisted public repos' passed released-mode artifacts and uploads the exact
ZIP without executing their code under an admin key. Its optional downstream
mirror separately omits top-level `build/`; the portal ZIP strips nothing.

### Caller migration

`check-template-drift` deploys both `sample-projects-editable.yml` and
`sample-projects-released.yml` only when root `*--sample-projects/` exists,
and deletes deprecated `sample-projects.yml` and `sample-project-upload.yml`
callers. The maintainer re-syncs every PDK repo immediately after merge; until
then old callers fail with “workflow not found”. Keep the editable caller's
job id `test-sample-projects` without `name:` so the release gate still matches;
released uses `released`. The gate paginates check runs, fails on missing
editable checks when discoverable sample-project pyprojects exist on main HEAD,
and fails on unfinished matching checks with a retry instruction.

## Example Usage

```yaml
# .github/workflows/test_code.yml (in a PDK repo)
name: Test code
on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    uses: doplaydo/pdk-ci-workflow-public/.github/workflows/test_code.yml@main
    secrets:
      GFP_API_KEY: ${{ secrets.GFP_API_KEY }}
```
