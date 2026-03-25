# iwp-build

`iwp-build` is the orchestrator layer for IWP workflows.

It provides read-only build diagnostics and reuses `iwp_lint` as the quality/diff engine.
For day-to-day agent and CI workflows, use `iwp-build` as the primary CLI entrypoint.

Protocol alignment note:

- Core intent authoring is page-first (`pages/**/*.md`) with selective `@iwp` annotations.
- Some payload fields retain legacy internal names for backward compatibility; treat them as diagnostic metadata.

## Development Setup

```bash
uv sync --group dev
uv run iwp-build --help
```

## Responsibilities

- orchestrate workflow entrypoints (`build`, `verify`, `watch`)
- provide read-only build output for intent diff and implementation gap diagnostics
- call `iwp_lint` library API directly (no subprocess dependency)

Non-goals:

- does not re-implement lint/schema/coverage logic
- does not replace agent runtime or code generation engine

## Quick Commands (Dual View)

### Human View (short 7)

```bash
uv run iwp-build build --config .iwp-lint.yaml
uv run iwp-build session start --config .iwp-lint.yaml --json out/session-start.json
uv run iwp-build session diff --config .iwp-lint.yaml
uv run iwp-build session reconcile --config .iwp-lint.yaml
uv run iwp-build session commit --config .iwp-lint.yaml --message "feat: add beta node flow" --evidence-json out/session-evidence.json --json out/session-commit.json
uv run iwp-build history list --config .iwp-lint.yaml --json out/history-list.json
uv run iwp-build verify --config .iwp-lint.yaml --with-tests
```

### Agent View (preset-first)

```bash
uv run iwp-build session start --config .iwp-lint.yaml --preset agent-default --json out/session-start.json
uv run iwp-build session diff --config .iwp-lint.yaml --preset agent-default
uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default
uv run iwp-build session commit --config .iwp-lint.yaml --preset ci-strict --message "agent: reconcile and commit"
uv run iwp-build session normalize-links --config .iwp-lint.yaml
uv run iwp-build history restore --config .iwp-lint.yaml --to 42 --dry-run
```

Notes:

- Presets are read from `.iwp-lint.yaml > execution_presets`.
- Merge priority is: explicit CLI args > preset args > command defaults.
- `session diff` / `session reconcile` can auto-start sessions via:
  - `--auto-start-session`, or
  - config `session.auto_start_on_missing: true`.

## Advanced Commands (Troubleshooting)

```bash
uv run iwp-build build --config .iwp-lint.yaml --normalize-links
uv run iwp-build build --config .iwp-lint.yaml --mode diff --json out/iwp-build.json
uv run iwp-build build --config .iwp-lint.yaml --no-code-sidecar
uv run iwp-build verify --config .iwp-lint.yaml --min-severity error
uv run iwp-build verify --config .iwp-lint.yaml --quiet-warnings
uv run iwp-build watch --config .iwp-lint.yaml --verify
uv run iwp-build session current --config .iwp-lint.yaml --json out/session-current.json
uv run iwp-build session diff --config .iwp-lint.yaml --format both --json out/session-diff.json
uv run iwp-build session diff --config .iwp-lint.yaml --include-baseline-gaps --focus-path pages/home.md --max-gap-items 20 --json out/session-diff.gaps.json
uv run iwp-build session diff --config .iwp-lint.yaml --code-diff-level hunk --code-diff-context-lines 3 --code-diff-max-chars 12000 --json out/session-diff-hunk.json
uv run iwp-build session reconcile --config .iwp-lint.yaml --format both --debug-raw --json out/session-reconcile.debug.json
uv run iwp-build session reconcile --config .iwp-lint.yaml --max-diagnostics 20 --suggest-fixes --json out/session-reconcile.fixes.json
uv run iwp-build session reconcile --config .iwp-lint.yaml --auto-build-sidecar
uv run iwp-build session start --config .iwp-lint.yaml --if-missing
uv run iwp-build session normalize-links --config .iwp-lint.yaml --json out/session-normalize-links.json
uv run iwp-build history list --config .iwp-lint.yaml --limit 50 --json out/history.list.json
uv run iwp-build history restore --config .iwp-lint.yaml --to 42 --dry-run --json out/history.restore.preview.json
uv run iwp-build history restore --config .iwp-lint.yaml --to 42 --force --json out/history.restore.apply.json
uv run iwp-build history prune --config .iwp-lint.yaml --max-snapshots 200 --max-days 30 --max-bytes 2147483648 --json out/history.prune.json
```

## Workflow

1. `build`: compile `.iwc`, build code sidecar under `.iwp/compiled/code`, compute implementation gap (link/coverage diagnostics), no baseline update
2. agent uses `session diff` / `session reconcile` text protocol output as primary implementation hints and edits code
3. `session commit`: run gate and atomically advance baseline as the regular commit checkpoint writer
4. `history restore`: switch current baseline pointer to a historical checkpoint when rollback/forward-jump is needed
5. `verify`: run compiled checks, full lint gate, and optional regression tests
6. `watch` (optional local loop): incremental `.iwc` compile only; not a workflow checkpoint

## Integration with iwp_lint API

`iwp-build` uses these APIs from `iwp_lint/api.py`:

- `compile_context(...)`
- `build_code_sidecar(...)`
- `verify_compiled(...)`
- `normalize_annotations(...)`
- `snapshot_action(...)`
- `session_start(...)`
- `session_current(...)`
- `session_diff(...)`
- `session_commit(...)`
- `session_audit(...)`
- `history_list(...)`
- `history_restore(...)`
- `history_prune(...)`

This design keeps one source of truth for lint and snapshot semantics.


## Suggested CI Usage

Example:

```bash
uv run iwp-build build --config .iwp-lint.yaml --json out/iwp-build.json
uv run iwp-build session reconcile --config .iwp-lint.yaml
# agent applies changes based on printed IWP_RECONCILE_V1 text block
uv run iwp-build verify --config .iwp-lint.yaml --with-tests
```

Output notes:

- `--json` writes the full build payload (`summary`, `compile`, `intent_diff`, `gap_report`, `checkpoint`)
- build JSON may include legacy-named compatibility fields (`mode_flags.page_only_enabled`, `summary.page_only_enabled`)
- build default includes code sidecar output (`.iwp/compiled/code/_ir/**` by default)
- `--no-code-sidecar` disables sidecar generation for faster local loops
- `build` prints baseline state as diff context (`exists`, `id`) and never advances baseline
- `build` success/failure both keep baseline unchanged; use `session commit` for normal baseline advancement
- `history restore` switches baseline pointer to a historical checkpoint and returns required follow-up actions
- `history restore` default safety blocks dirty workspace; use `--force` to override
- `history restore --dry-run` previews write/delete impact without applying filesystem changes
- `history prune` applies retention policy and keeps protected checkpoints (latest and recent restore safety point)
- `session start` auto-generates a unique `session_id` by default; manual custom id is intentionally disabled in current phase
- only one active session is allowed per workspace (`open|dirty|verified|blocked`)
- `session start --if-missing` is idempotent for agent bootstrap:
  - starts a new session when none exists
  - reuses current active session when present
- `session current` returns the currently open session so agents can discover the active session id without external state
- `session diff` / `session commit` fall back to current open session when `--session-id` is omitted
- `session reconcile` resolves current open session; it can also auto-start when enabled (`--auto-start-session` or `session.auto_start_on_missing`)
- `session reconcile --auto-build-sidecar` runs controlled sidecar refresh (`compile_context + build_code_sidecar`) when sidecar is stale, then continues reconcile decision in one command
- active session statuses are `open|dirty|verified|blocked` (committed means closed)
- when no open session exists (and auto-start is disabled), errors include actionable next-step commands
- `session diff` / `session reconcile` are text-first by default:
  - `--format text` (default): print protocol blocks (`IWP_DIFF_V1`, `IWP_RECONCILE_V1`)
  - `--format json`: write JSON only (`--json` optional; defaults to `out/session-<action>.json`)
  - `--format both`: print text and write JSON
- `agent-default` preset sets:
  - `session start` to `if_missing=true`
  - `session diff` to `format=both` with stable path `out/session-diff.json` (replaced each run)
  - `session reconcile` to `format=both` with stable path `out/session-reconcile.json` (replaced each run)
  - `session reconcile` diagnostics visibility to `min_severity=error`
  - `session reconcile` warning summary to `warning_top_n=2`
  - `session reconcile` to `auto_build_sidecar=true`
- `--debug-raw` appends raw heavy payload under `raw` for troubleshooting
- `session diff` now includes structured code details:
  - `meta` (`protocol_block`, `mode`, `schema_version`)
  - top-level `filters_applied`
  - text protocol `changed_code_summary` (`file` + changed line ranges)
  - `changed_code_details[*].file_path`
  - `changed_code_details[*].change_kind` (`added|modified|deleted`)
  - `changed_code_details[*].changed_line_count`
  - `changed_code_details[*].changed_line_ranges`
  - optional `changed_code_details[*].hunks` when `--code-diff-level hunk`
- `build` does not create/use session state; session lifecycle is managed only by `session *` commands
- `session diff` supports focused node filtering:
  - `--node-severity all|error|warning`
  - `--node-file-type-id <id>` (repeatable)
  - `--node-anchor-level <level>` (repeatable)
  - `--node-kind-prefix <prefix>` (repeatable)
  - `--critical-only`
  - `--markdown-excerpt-max-chars <N>`
- `session diff` supports optional baseline gap summary:
  - `--include-baseline-gaps`
  - `--focus-path <path>`
  - `--max-gap-items <N>`
  - JSON field: `baseline_gap_summary.total_errors`, `baseline_gap_summary.total_warnings`, `baseline_gap_summary.top_uncovered_pairs`
- `session reconcile` runs `diff -> (optional normalize) -> gate` and returns:
  - `meta` (`protocol_block`, `mode`, `schema_version`)
  - top-level `filters_applied` (same structure as `session diff`)
  - text protocol `diff_summary` (`changed_md=... changed_code=... impacted_nodes=...`)
  - text protocol `diagnostics_top` (code/severity/file/line/message)
  - `sidecar_fresh`
  - `compiled_at`
  - `compiled_from_baseline_id`
  - `warning_count`
  - `top_warnings` (default top 2)
  - `can_commit`
  - `diagnostics_top`
  - `blocking_reasons`
  - `blocking_pairs_topn`
  - `next_actions`
  - `next_command_examples`
  - `recommended_next_command`
  - `recommended_next_chain`
  - `auto_recovered`
  - `hints`
  - `code_path_hints`
  - `suggested_code_paths`
- `session reconcile` additional options:
  - `--max-diagnostics <N>`
  - `--min-severity warning|error`
  - `--warning-top-n <N>`
  - `--quiet-warnings`
  - `--suggest-fixes`
  - `--auto-build-sidecar`
- session flow convenience command:
  - `session normalize-links` (delegates to normalize annotations in write mode)
- `session commit` enforces sidecar freshness by default:
  - stale/missing sidecar blocks commit (`blocked_by` includes `code_sidecar`)
  - exception path: `--allow-stale-sidecar`
- reconcile payload sanitization:
  - when `can_commit=true`, blocking fields are empty arrays (`blocking_reasons`, `blocking_pairs_topn`, `next_actions`, `next_command_examples`)
  - text protocol never emits placeholder entries like `- ""` or string sentinel `"None"`
  - text protocol omits empty list sections entirely to reduce parser noise (`diagnostics_top`, `next_actions`, `blocking_pairs_topn`, `next_command_examples`, `diff_excerpt`, etc.)
- `session diff` emits compact markdown change payload:
  - `markdown_change_blocks`
  - `markdown_change_text` (line-protocol style, LLM friendly)
- `session commit --evidence-json` freezes pre-commit diff and writes structured evidence:
  - `intent_diff`
  - `link_evidence`
  - `gate_result`
  - `commit_result`
- link density warning threshold is configurable via `.iwp-lint.yaml`:
  - `session.link_density_threshold` (default `0.25`)

Verify diagnostics:

- `verify` prints baseline state used by current check
- compiled verification failure now includes root-cause buckets (`missing`, `stale`, `invalid`) with sample files
- lint verification failure prints top diagnostics (`code`, `file`, `reason`) and remediation hints for common codes (`IWP105`, `IWP107`, `IWP109`)
- verify supports lint output filtering:
  - `--min-severity error` shows only error diagnostics
  - `--quiet-warnings` hides warning diagnostics
- verify gate controls:
  - `--protocol-only` runs compiled + lint gates only
  - `--with-tests` runs tests after protocol gate passes
  - `--run-tests` remains supported as alias for `--with-tests`
- verify prints gate summary:
  - `protocol=PASS|FAIL`
  - `tests=PASS|FAIL|SKIPPED`
  - `overall=PASS|FAIL`
- lint console output includes status and severity tags:
  - status: `OK`, `PASS_WITH_WARNINGS`, `FAIL`
  - diagnostics: `[E][IWPxxx] ...` / `[W][IWPxxx] ...`

Recovery loop (advanced/manual):

```bash
uv run iwp-build build --config .iwp-lint.yaml --mode diff
uv run iwp-lint links normalize --config .iwp-lint.yaml --write
uv run iwp-build build --config .iwp-lint.yaml --mode diff
uv run iwp-build verify --config .iwp-lint.yaml
```

Text protocol examples:

```text
<<<IWP_DIFF_V1>>>
session_id:"s.86a6f9baf697"
status:"dirty"
file:"pages/home.md"
+[50]:{n.578c} "Test"
link_targets:
- "pages/home.md::n.578c"
<<<END_IWP_DIFF_V1>>>
```

```text
<<<IWP_RECONCILE_V1>>>
session_id:"s.86a6f9baf697"
status:"blocked"
can_commit:false
blocking_reasons:
- "lint"
next_actions:
- kind="lint_fix" command="uv run iwp-lint links normalize --config .iwp-lint.yaml --write" reason="run: uv run iwp-lint links normalize --config .iwp-lint.yaml --write"
hints:
- kind="remediation" message="run: uv run iwp-lint links normalize --config .iwp-lint.yaml --write" command="uv run iwp-lint links normalize --config .iwp-lint.yaml --write"
code_path_hints:
- "_ir/src/pages/HomePage.vue"
<<<END_IWP_RECONCILE_V1>>>
```

## E2E Scenarios

Build e2e tests are fixture-driven and map to agent flow stages:

- shared fixtures: `test/<scenario>/`
- e2e suite entrypoint: `iwp_build/tests/test_e2e_suite.py`

Covered flows:

- page-intent add node: build fails before link patch, then passes after `@iwp.link` update
- page-intent delete node: stale link fails verify, cleanup + rebuild restores green state
- page-intent modify node: impacted nodes detected in diff, link update required
- bootstrap without baseline and without links: first build fails, patch links, second build passes without baseline update
- bootstrap first build: `--mode auto` enters `bootstrap_full` in read-only build mode

Schema profile matrix:

- every build e2e scenario runs both:
  - `minimal` profile (shared test schema under `test/schema/`)
  - `official` profile (`schema/iwp-schema.v1.json`)
- tests rewrite fixture markdown as needed per profile to keep business intent assertions stable.

Run only build e2e:

```bash
uv run python -m unittest iwp_build.tests.test_e2e_suite
```

Design change:

- `build` is read-only for intent diff + implementation gap.
- `session commit` is the regular checkpoint writer for new baseline states.
- `session commit --message` records checkpoint message for `history list` display.
- `history restore` is the baseline pointer switch path for rollback/forward-jump to existing checkpoints.
