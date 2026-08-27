# Reusable Workflows

Reusable workflows are complete, self-contained workflow definitions triggered via `workflow_call`. When a PDK repo calls a reusable workflow, it delegates the entire job — the workflow controls the runner, permissions, steps, and secret handling. The calling repo just says "run this job for me."

PDK repos create thin wrapper workflows that call these and forward secrets explicitly. See `templates/.github/workflows/` for ready-to-copy wrappers.


## Workflows

| Workflow | Jobs | Secrets Used | Description |
|----------|------|-------------|-------------|
| `test_code.yml` | pre-commit, test_code, test_gfp | `GFP_API_KEY` | Pre-commit (fetches canonical config), pytest, GFP validation |
| `test-sample-projects.yml` | discover, test, notebooks, drc | `GFP_API_KEY` | Auto-discovers `*--sample-projects/` dirs; runs unit tests, notebook execution, and DRC per project |
| `pages.yml` | build-docs | `GFP_API_KEY`, `SIMCLOUD_APIKEY` | Sphinx docs build and Pages artifact upload. The caller's wrapper adds the `deploy-docs` job |
| `claude-pr-review.yml` | review | `ANTHROPIC_API_KEY` | AI code review via Claude Sonnet 4. Auto-runs on PR open/reopen; re-runs only when a human posts `/claude-api review` on the PR |
| `drc.yml` | drc | `GFP_API_KEY` | Design Rule Check with badge generation |
| `gds-xor.yml` | gds-xor | `GITHUB_TOKEN` | Per-layer XOR of every `*.gds` a PR changes: uploads the XOR GDS plus a PDF report with one page per differing layer, and posts a sticky comment with the difference area in um^2 |
| `issue.yml` | add-label | `GITHUB_TOKEN` | Auto-labels issues with the `pdk` tag plus a per-repository `pdk:<repo-name>` tag |
| `test_coverage.yml` | coverage | `GFP_API_KEY` | Pytest with line coverage reporting |
| `model_coverage.yml` | model-coverage | `GFP_API_KEY` | PDK model-to-cell coverage check |
| `model_regression.yml` | model-regression | `GFP_API_KEY` | Model-specific regression tests |
| `update_badges.yml` | badges | `GFP_API_KEY`, `GITHUB_TOKEN` | Generate coverage, model, issue, and PR badges |
| `generate_nyanlib.yml` | generate | `GFP_API_KEY`, `GITHUB_TOKEN` | Runs `gfp serve` inside the `gfp-server` container against the PDK, resolves the factories owned by that PDK, and produces `build/models.nyanlib` + SVG symbols; opens an update PR only on a manual `workflow_dispatch` from `main` |
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
