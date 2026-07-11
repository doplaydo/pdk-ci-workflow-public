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
| `.github/workflows/release-drafter.yml` | Semantic versioning + Claude-curated release notes |
| `.github/workflows/drc.yml` | Design Rule Check via GFP |
| `.github/workflows/issue.yml` | Auto-label PDK issues |
| `.github/workflows/test_coverage.yml` | Pytest with line coverage reporting |
| `.github/workflows/model_coverage.yml` | PDK model-to-cell coverage check |
| `.github/workflows/model_regression.yml` | Model-specific regression tests |
| `.github/workflows/update_badges.yml` | Generate coverage, model, issue, and PR badges |
| `.github/workflows/code-security.yml` | SAST (Semgrep) and SCA (Trivy) security scans |
| `.github/workflows/release.yml` | Release pipeline wrapper: manual dispatch cuts a bumped `release/X.Y.Z` branch + PR; green CI on that branch auto-triggers tag, build, publish, and GitHub Release |

## Release Workflow

`release.yml` wraps two upstream workflows: `cut-release.yml` (manual dispatch) and `release.yml` (auto-fires via `workflow_run` when "Test code" succeeds on a `release/X.Y.Z` branch). To cut a release:

1. **Recommended:** use the "Run workflow" button on your repo's Actions tab for `Release` and enter the version (`MAJOR.MINOR.PATCH`, optionally `-prerelease` and/or `+localsuffix`, e.g. `1.2.3`, `1.2.3-rc1`, `1.2.3+gfp0` — it must also match your `[tool.tbump.version]` regex). This creates `release/X.Y.Z` off `main` **with the version-bump commit already on it** (your `bump_command`, including any `[tool.tbump.before_commit]` hooks such as towncrier) and opens the release PR. Dispatching a version whose branch or tag already exists is a safe no-op.
2. **Or by hand:** branch `release/X.Y.Z` off `main` and open a PR. If you didn't commit the version bump yourself, the pipeline pushes one for you on its first trigger and waits for CI to re-run.
3. Once "Test code" goes green on the PR, the pipeline tags `vX.Y.Z` at the exact CI-tested commit, builds wheels, optionally builds PDF docs, publishes to your configured target, and publishes the GitHub Release — all automatically, before you merge.
4. Review the bump commit, the published package, and the GitHub Release. If everything looks right, merge the PR.

**The PR must be merged via "Create a merge commit"** — not squash or rebase. Tagging happens on the release branch before merge; a squash-merge would give the merged commit on `main` a new SHA, leaving the tag pointing at a commit that's no longer reachable from `main`'s history. This is not enforced by branch protection — it's a manual requirement when merging release PRs specifically.

### Configuring your release

Add a `[tool.pdk-ci-workflow.release]` section to your `pyproject.toml`. All fields are optional (defaults shown):

```toml
[tool.pdk-ci-workflow.release]
target = "private"                # "private" | "pypi" | "pypi-trusted" | "none"
build_pdf = false
pdf_filename_prefix = "docs"
pdf_apt_packages = ["texlive-latex-extra", "texlive-fonts-extra", "texlive-xetex", "latexmk", "xindy"]
upload_uv_lock = false
upload_requirements_txt = false
ligentec_release_style = false
```

`target` options:
- `private` — uploads wheels to the CodeArtifact-backed `prod.gdsfactory.com` endpoint (still using `DEVPI_*`-named secrets — the backend is CodeArtifact, but the twine-compatible endpoint is unchanged). The destination org comes from a repository Actions **variable** `RELEASE_PRIVATE_ORG_ID` (Settings → Secrets and variables → Actions → Variables) set to the customer's registry org UUID. A variable — not `pyproject.toml` — because org UUIDs are customer identifiers that must never ship inside the distributed wheel/sdist.
- `pypi` — standard `twine upload` to public PyPI
- `pypi-trusted` — OIDC trusted publishing to PyPI (no stored PyPI credentials)
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
| `.github/release-drafter.yml` | Release note categories + auto-labeler |
