# Reusable Workflows

Reusable workflows are complete, self-contained workflow definitions triggered via `workflow_call`. When a PDK repo calls a reusable workflow, it delegates the entire job — the workflow controls the runner, permissions, steps, and secret handling. The calling repo just says "run this job for me."

PDK repos create thin wrapper workflows that call these and forward secrets explicitly. See `templates/.github/workflows/` for ready-to-copy wrappers.

The docs workflow accepts an optional `runner` input, defaulting to `ubuntu-latest`. Its wrapper reads the `DOCS_RUNNER` repository variable; set it to an available runner label (for example, `ubuntu-8core`) to select a larger docs build machine.


## Workflows

| Workflow | Jobs | Secrets Used | Description |
|----------|------|-------------|-------------|
| `test_code.yml` | wheel-contents, pre-commit, test_code, test_gfp, test_gfp_no_dev | `GFP_API_KEY` | Builds and checks wheel reference-file exclusion, pre-commit (fetches canonical config), pytest, GFP validation |
| `test-sample-projects.yml` | discover, test, notebooks | `GFP_API_KEY` | Auto-discovers `*--sample-projects/` dirs; runs unit tests and notebook execution per project |
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
| `sample-project-upload.yml` | discover, test, upload | `GFP_API_KEY`, `GSP_SYNC_TOKEN` (optional) | Per `*--sample-projects/` dir: snapshots the committed project, tests it against the published PDK (`gfp test`, notebooks, DRC smoke test on `sample_drc_errors`), then zips each passing project (minus any committed `build/models.nyanlib` + `build/symbols/`) and uploads it to the GDSFactory+ portal, always overwriting; after a successful upload, mirrors projects mapped in doplaydo/gfp-sample-projects' `.github/sample-projects-sources.yml` there as a `sync/<dir>` PR. Version: the `release_version` input, else the project's exact `==` PDK pin, else the installed version; it must match the PDK version actually installed. Refuses projects whose portal key collides with another sample project's. `soft_fail` keeps jobs green while still reporting `::error::` |
| `build-pdf.yml` | build-pdf | `GFP_API_KEY`, `SIMCLOUD_APIKEY`, `PDK_CI_WORKFLOW_TOKEN` | Build PDF docs on demand; uploads artifact and optionally attaches to a release |

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
