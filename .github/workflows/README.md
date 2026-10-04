# Reusable Workflows

Reusable workflows are complete, self-contained workflow definitions triggered via `workflow_call`. When a PDK repo calls a reusable workflow, it delegates the entire job — the workflow controls the runner, permissions, steps, and secret handling. The calling repo just says "run this job for me."

PDK repos create thin wrapper workflows that call these and forward secrets explicitly. See `templates/.github/workflows/` for ready-to-copy wrappers.

The docs workflow accepts an optional `runner` input, defaulting to `ubuntu-latest`. Its wrapper reads the `DOCS_RUNNER` repository variable; set it to an available runner label (for example, `ubuntu-8core`) to select a larger docs build machine.


## Workflows

| Workflow | Jobs | Secrets Used | Description |
|----------|------|-------------|-------------|
| `test_code.yml` | pre-commit, test_code, test_gfp | `GFP_API_KEY` | Pre-commit (fetches canonical config), pytest, GFP validation |
| `test-sample-projects.yml` | discover, test, notebooks | `GFP_API_KEY` | Auto-discovers `*--sample-projects/` dirs; runs unit tests and notebook execution per project |
| `pages.yml` | build-docs | `GFP_API_KEY`, `SIMCLOUD_APIKEY` | Sphinx docs build and Pages artifact upload. The caller's wrapper adds the `deploy-docs` job |
| `claude-pr-review.yml` | review | `ANTHROPIC_API_KEY` | AI code review via Claude Sonnet 4. Auto-runs on PR open/reopen; re-runs only when a human posts `/claude-api review` on the PR |
| `drc.yml` | drc | `GFP_API_KEY` | Design Rule Check with badge generation |
| `gds-xor.yml` | gds-xor | `GITHUB_TOKEN` | Per-layer XOR of every `*.gds` a PR changes: uploads the XOR GDS plus a PDF report with one page per differing layer, and posts a sticky comment with the difference area in um^2. Inline images need an attachment upload, which is attributed to a user account, so with only `GITHUB_TOKEN` the comment links the PDF instead |
| `issue.yml` | add-label | `GITHUB_TOKEN` | Auto-labels issues with the `pdk` tag plus a per-repository `pdk:<repo-name>` tag |
| `test_coverage.yml` | coverage | `GFP_API_KEY` | Pytest with line coverage reporting |
| `model_coverage.yml` | model-coverage | `GFP_API_KEY` | PDK model-to-cell coverage check |
| `model_regression.yml` | model-regression | `GFP_API_KEY` | Model-specific regression tests |
| `update_badges.yml` | badges | `GFP_API_KEY`, `GITHUB_TOKEN` | Generate coverage, model, issue, and PR badges |
| `generate_nyanlib.yml` | discover, generate | `GFP_API_KEY`, `GFP_ECR_IMAGE`, `SHARED_SERVICES_AWS_OIDC_ROLE_ARN`, `GITHUB_TOKEN` | Installs sample projects from configured package indexes while ignoring local source overrides for the root PDK package, pulls `gfp-server` from ECR via OIDC and runs `gfp serve` inside it, producing `build/models.nyanlib` + SVG symbols; opens an update PR only on a manual `workflow_dispatch` from `main` |
| `sample-project-upload.yml` | discover, test, nyanlib, upload | `GFP_API_KEY`, `GFP_ECR_IMAGE`, `SHARED_SERVICES_AWS_OIDC_ROLE_ARN` | Per `*--sample-projects/` dir: snapshots the committed project, tests it against the published PDK (`gfp test`, notebooks, DRC smoke test on `sample_drc_errors`), generates nyanlib via `generate_nyanlib.yml` (no update PR), then zips each passing project with its `models.nyanlib` + symbols (or, if its nyanlib generation failed, without fresh ones and with a warning) and uploads it to the GDSFactory+ portal, always overwriting. Version: the `release_version` input, else the project's exact `==` PDK pin, else the installed version; it must match the PDK version actually installed. Refuses projects whose portal key collides with another sample project's. `soft_fail` keeps jobs green while still reporting `::error::` |
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
