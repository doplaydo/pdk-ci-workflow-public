# templates/

Copy these files into your PDK repo as-is. No modification needed.

## Workflows

Minimal wrappers — each calls the upstream reusable workflow and forwards secrets explicitly.



| File | Purpose |
|------|---------|
| `.github/workflows/test_code.yml` | Pre-commit, pytest, GFP validation |
| `.github/workflows/sample-projects-editable.yml` | Shared sample suite against the editable PDK on PRs, pushes to `main`, and dispatch; conditionally deployed when `*--sample-projects/` exists |
| `.github/workflows/pages.yml` | Sphinx docs build + GitHub Pages |
| `.github/workflows/claude-pr-review.yml` | AI code review via Claude — runs once on PR open/reopen; re-run on demand with `/claude-api review` comment |
| `.github/workflows/drc.yml` | Design Rule Check via GFP |
| `.github/workflows/gds-xor.yml` | Per-layer XOR report for GDS files changed by a PR |
| `.github/workflows/issue.yml` | Auto-label PDK issues |
| `.github/workflows/test_coverage.yml` | Pytest with line coverage reporting |
| `.github/workflows/model_coverage.yml` | PDK model-to-cell coverage check |
| `.github/workflows/model_regression.yml` | Model-specific regression tests |
| `.github/workflows/sample-projects-released.yml` | Shared sample suite against the released PDK on sample changes on `main` or dispatch; no portal upload in this repo; conditionally deployed |
| `.github/workflows/update_badges.yml` | Generate coverage, model, issue, and PR badges |
| `.github/workflows/code-security.yml` | SAST (Semgrep) and SCA (Trivy) security scans |







## Sample-project callers and migration

Both sample templates are thin callers of `sample-projects.yml`, deployed by
`check-template-drift` only when a root `*--sample-projects/` directory exists.
The hook removes the deprecated `sample-projects.yml` and
`sample-project-upload.yml` callers. A maintainer must re-sync every PDK repo
immediately after merge; until then, old callers fail with “workflow not found”.

| Caller | Workflow name | Triggers | Job id / mode |
|---|---|---|---|
| `sample-projects-editable.yml` | Sample projects · editable PDK | PR, push to `main`, dispatch | `test-sample-projects` / `editable` |
| `sample-projects-released.yml` | Sample projects · released PDK | Push to `main` touching `**/*--sample-projects/**`, dispatch | `released` / `released` |

Do not add a `name:` to the editable job: its check names must remain
`test-sample-projects / test / Test <dir>` for the release health gate. Released
checks are `released / test / Test <dir>` and do not gate merging. Editable
mode replaces the PDK source with the checkout; released mode does not mask
committed path sources. Both use the same per-project suite and report every
exclusion. Package ZIPs contain committed content, with no `build/` stripping.

Both callers forward only `GFP_API_KEY` (optional in the shared suite).
The public released caller has no upload settings or mirror token. Uploads
are handled by a private central service after passed released tests, subject
to its reviewed repo/portal-PDK allowlist; no admin key belongs in a public
repo. That service uploads the exact tested ZIP; only its optional downstream
mirror omits top-level `build/`.

## Pre-commit Config

`.pre-commit-config.yaml` is the **canonical config** for all PDK repos.

- **Do not commit this file** — add it to `.gitignore`
- **CI fetches it automatically** via the `test_code.yml` workflow
- **Locally**, `make dev` downloads it:

```makefile
dev: install
	curl -sf https://raw.githubusercontent.com/doplaydo/pdk-ci-workflow-public/main/templates/.pre-commit-config.yaml -o .pre-commit-config.yaml
	uv run pre-commit clean
	uv run pre-commit install
```

- **If `git commit` fails with `` `<hook-id>` is not present in repository https://github.com/doplaydo/pdk-ci-workflow-public ``:** your local pre-commit cache predates a hook that was added upstream. Run `pre-commit clean && make dev` — do **not** run `pre-commit autoupdate`, it does not work for the mutable `rev: main` pin and will not fix this.

## Other Config

| File | Purpose |
|------|---------|
| `.github/dependabot.yml` | Monthly pip + github-actions updates |
