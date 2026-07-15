# templates/

Copy these files into your PDK repo as-is. No modification needed.

## Workflows

Minimal wrappers — each calls the upstream reusable workflow and forwards secrets explicitly.


| File | Purpose |
|------|---------|
| `.github/workflows/test_code.yml` | Pre-commit, pytest, GFP validation |
| `.github/workflows/sample-projects.yml` | Unit tests, notebooks, and DRC for `*--sample-projects/` directories — deployed automatically by `check-template-drift` |
| `.github/workflows/pages.yml` | Sphinx docs build + GitHub Pages |
| `.github/workflows/claude-pr-review.yml` | AI code review via Claude — runs once on PR open/reopen; re-run on demand with `/claude-api review` comment |
| `.github/workflows/drc.yml` | Design Rule Check via GFP |
| `.github/workflows/issue.yml` | Auto-label PDK issues |
| `.github/workflows/test_coverage.yml` | Pytest with line coverage reporting |
| `.github/workflows/model_coverage.yml` | PDK model-to-cell coverage check |
| `.github/workflows/model_regression.yml` | Model-specific regression tests |
| `.github/workflows/update_badges.yml` | Generate coverage, model, issue, and PR badges |
| `.github/workflows/code-security.yml` | SAST (Semgrep) and SCA (Trivy) security scans |

## Release Workflow

`release.yml` wraps two upstream workflows, chained in one `workflow_dispatch` run: `cut-release.yml` creates the bumped `release/X.Y.Z` branch and opens the PR, then `release.yml` immediately tags, builds, and publishes. To cut a release:

1. **Recommended:** use the "Run workflow" button on your repo's Actions tab for `Release` and enter the version (`MAJOR.MINOR.PATCH`, optionally `-prerelease` and/or `+localsuffix`, e.g. `1.2.3`, `1.2.3-rc1`, `1.2.3+gfp0` — it must also match your `[tool.tbump.version]` regex). This creates `release/X.Y.Z` off `main` **with the version-bump commit already on it** (your `bump_command`) and opens the release PR. Dispatching a version whose branch or tag already exists is a safe no-op. Equivalently, from the CLI: `gh workflow run release.yml -f version=X.Y.Z` (run from inside the repo directory, or add `--repo owner/repo` from elsewhere).
2. **Or by hand:** branch `release/X.Y.Z` off `main` and open a PR. If you didn't commit the version bump yourself, the pipeline pushes one for you on its first trigger and waits for CI to re-run.
3. The pipeline tags `vX.Y.Z` at the exact commit it just created, builds wheels, optionally builds PDF docs, publishes to your configured target, and publishes the GitHub Release — all automatically, before "Test code" has necessarily finished on the PR. If `main`'s last CI run wasn't green when you dispatched this, the pipeline refuses to proceed (see below) — it does not wait for the release branch's own PR checks.
4. Review the bump commit, the published package, and the GitHub Release. If everything looks right, merge the PR.

**The PR must be merged via "Create a merge commit"** — not squash or rebase. Tagging happens on the release branch before merge; a squash-merge would give the merged commit on `main` a new SHA, leaving the tag pointing at a commit that's no longer reachable from `main`'s history. This is not enforced by branch protection — it's a manual requirement when merging release PRs specifically.

### Configuring your release

Add a `[tool.pdk-ci-workflow.release]` section to your `pyproject.toml`. All fields are optional (defaults shown):

```toml
[tool.pdk-ci-workflow.release]
target = "private"                # "private" | "pypi" | "none"
build_pdf = false
pdf_filename_prefix = "docs"
pdf_apt_packages = ["texlive-latex-extra", "texlive-fonts-extra", "texlive-xetex", "latexmk", "xindy"]
upload_uv_lock = false
upload_requirements_txt = false
```


`target` options:
- `pypi` — standard `twine upload` to public PyPI
- `none` — skip publishing (tag + build + GitHub Release only)

`pdf_apt_packages` overrides the apt packages installed before `pdf_build_command` runs — the default assumes a LaTeX-based `make docs-pdf`; override it (e.g. `["libpango1.0-dev", "libcairo2-dev", "libgdk-pixbuf2.0-dev"]`) if your repo's PDF pipeline uses WeasyPrint/cairo instead.

`bump_command` (default `tbump {version} --non-interactive --no-tag --no-push`) must create the bump commit locally **without tagging or pushing** — the workflows handle both. `pdf_build_command` (default `make docs-pdf`) must leave `docs.pdf` at the repo root; the workflow renames it to `<pdf_filename_prefix>-<version>.pdf`.


See `pdk-ci-workflow-public`'s own `.github/workflows/release.yml` for the full list of advanced overrides (`bump_command`, `pre_build_command`, `pypi_build_command`, etc.) — defaults work for the standard `make rm-samples` / `make build` / `tbump` project layout.

### If a release partially fails

The version bump lands when the branch is cut, and the git tag is pushed *before* any publish step runs. If a publish job (wheel build, registry upload, PyPI upload) fails after the tag was already pushed, re-running "Test code" or re-pushing to the release branch will **not** retry the publish — the workflow sees the tag already exists and treats it as an already-completed release (idempotent no-op, by design, so accidental double-tagging can't happen).

To recover: bump to the next patch version instead (e.g. `release/1.2.3` failed publish → branch `release/1.2.4`) rather than trying to re-trigger `1.2.3`. Do not delete and recreate the `v1.2.3` tag unless you're certain nothing downstream (mirrors, dependents pinning that tag) has already picked it up.

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
