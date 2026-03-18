# iwp-build

`iwp-build` is the orchestrator layer for IWP workflows.

It provides the manual build checkpoint and reuses `iwp_lint` as the quality/diff engine.

## Development Setup

```bash
uv sync --group dev
uv run iwp-build --help
```

## Responsibilities

- orchestrate workflow entrypoints (`build`, `verify`, `watch`)
- provide a manual build checkpoint for intent diff and implementation gap output
- call `iwp_lint` library API directly (no subprocess dependency)

Non-goals:

- does not re-implement lint/schema/coverage logic
- does not replace agent runtime or code generation engine

## Commands

```bash
uv run iwp-build build --config .iwp-lint.yaml
uv run iwp-build build --config .iwp-lint.yaml --mode diff --json out/iwp-build.json --diff-json out/iwp-diff.json
uv run iwp-build verify --config .iwp-lint.yaml --run-tests
uv run iwp-build watch --config .iwp-lint.yaml --verify
```

Backward-compatible module entry still works:

```bash
python -m iwp_build build --config .iwp-lint.yaml
```

## Workflow

1. `build`: compile `.iwc`, compute intent diff (markdown delta), then compute implementation gap (link/coverage diagnostics)
2. agent uses the compact diff output as implementation hints and edits code
3. `verify`: run compiled checks, full lint gate, and optional regression tests
4. `watch` (optional local loop): incremental `.iwc` compile only; not a workflow checkpoint

## Integration with iwp_lint API

`iwp-build` uses these APIs from `iwp_lint/api.py`:

- `snapshot_action(...)`
- `run_quality_gate(...)`
- `compile_context(...)`
- `verify_compiled(...)`

This design keeps one source of truth for lint and snapshot semantics.

## Watch Mode (Hot Compile for `.iwc`)

`iwp-build watch` is a local developer loop (optional):

- starts with one full `.iwc` compile
- polls markdown and control files
- batches changes with debounce
- recompiles only impacted markdown sources
- targets `.iwc v2` dual artifacts (`.iwp/compiled/json/**` + `.iwp/compiled/md/**`)
- can optionally verify artifacts and run tests after each cycle
- does not generate workflow tasks or baseline checkpoints

Example:

```bash
uv run iwp-build watch --config .iwp-lint.yaml --debounce-ms 600 --verify
```

## Suggested CI Usage

Example:

```bash
uv run iwp-build build --config .iwp-lint.yaml --json out/iwp-build.json --diff-json out/iwp-diff.json
# agent applies changes based on out/iwp-diff.json
uv run iwp-build verify --config .iwp-lint.yaml --run-tests
```

Output notes:

- `--json` writes the full checkpoint payload (`summary`, `compile`, `intent_diff`, `gap_report`)
- `--diff-json` writes a compact payload for agent implementation loops (`summary`, `intent_diff`, `gap_report.diagnostics`, `gap_report.nodes`)

## E2E Scenarios

Build e2e tests are fixture-driven and map to agent flow checkpoints:

- shared fixtures: `test/<scenario>/`
- e2e suite entrypoint: `iwp_build/tests/test_e2e_suite.py`
- compatibility wrapper: `iwp_build/tests/test_e2e_flow.py`

Covered flows:

- feature add node: build fails before link patch, then passes after `@iwp.link` update
- feature delete node: stale link fails verify, cleanup + rebuild restores green state
- feature modify node: impacted nodes detected in diff, link update required
- bootstrap without baseline and without links: first build fails, patch links, second build initializes baseline
- bootstrap first build: `--mode auto` enters `bootstrap_full` and initializes baseline

Schema profile matrix:

- every build e2e scenario runs both:
  - `minimal` profile (shared test schema under `test/schema/`)
  - `official` profile (`schema/iwp-schema.v1.json`)
- tests rewrite fixture markdown as needed per profile to keep business intent assertions stable.

Run only build e2e:

```bash
uv run python -m unittest iwp_build.tests.test_e2e_suite
```

## Migration from Legacy Commands

The old task-oriented commands were removed to keep the tool surface minimal.

- `iwp-build snapshot init|update|diff` -> `iwp-build build`
- `iwp-build plan` -> `iwp-build build`
- `iwp-build apply` -> removed (no task status workflow)
- `iwp-build verify --id <task_id>` -> `iwp-build verify`

Design change:

- `build` is now the manual checkpoint for intent diff + implementation gap.
- `watch` remains optional acceleration and does not define workflow checkpoints.
