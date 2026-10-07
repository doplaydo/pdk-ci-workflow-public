# Composite Actions

Composite actions are bundles of steps packaged up with an action.yml file that can live in any directory. They're used within a job, at the step level, and they execute on whatever runner the calling job is already using. The calling repo retains full control over the job definition — the runner, permissions, other steps before and after — and just drops the composite action in as a convenience. This is ideal when you want to share common step sequences (like setting up a toolchain or sending a notification) but leave teams free to structure their own jobs around them.

[`check_model_metadata`](check_model_metadata/action.yml) validates SAX/Circulax
factory model bindings in the caller's installed uv environment. It checks all
configured PDK variants in isolated processes and writes
`build/model-metadata.json`. Its `enforce` input selects error or warning
annotations and whether findings fail the step (audit mode exits zero).
The reusable model regression workflow defaults to a nonblocking audit and
exposes `enforce-model-metadata` for strict enforcement. See the
[checker documentation](../scripts/README.md#model-metadata-validation).
Canonical caller templates forward the `ENFORCE_MODEL_METADATA` repository
variable (`true` enables enforcement).

`check_wheel_contents` validates the wheels in its `wheel-directory` input
(default: `wheelhouse`) and the caller's `pyproject.toml`. Routine Test code CI
and release builds both use it to reject reference fixtures and Git metadata.
Required runtime files must live in ordinary directories under the PDK package.
