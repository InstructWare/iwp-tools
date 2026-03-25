# iwp-lint

`iwp-lint` is the quality engine for IWP projects.

In normal agent/runtime workflows, prefer `iwp-build` as the user-facing CLI.
Use `iwp-lint` directly for lint-focused diagnostics, normalization, and engine-level troubleshooting.

Protocol alignment note:

- Core authoring is page-first (`pages/**/*.md`) with selective `@iwp` annotation.

## Development Setup

```bash
uv sync --group dev
uv run iwp-lint --help
```

Local quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m unittest iwp_lint.tests.test_regression
```

It provides:

- markdown node parsing and stable node ID assignment
- annotation protocol validation (`@iwp.link`)
- coverage and schema gate checks
- diff analysis based on filesystem snapshot
- node catalog build/query for agent lookup
- snapshot API for incremental workflows (consumed by `iwp-build`)

Architecture and maintenance guide:

- See `iwp_lint/ARCHITECTURE.md`.

## Annotation Protocol

Use one single-line annotation in code comments:

```text
@iwp.link <source_path>::<node_id>
```

Example:

```text
@iwp.link pages/home.md::n.abc1
```

Page text marker (minimal syntax):

```text
- [text] Hero title copy
```

Marker notes:

- only `[text]` is supported
- `interaction` is inferred from `interaction_hooks`
- `structure` is inferred from `layout_tree` / `layout` / `display_rules`

## Quick Commands

### Human View

```bash
uv run iwp-lint full --config .iwp-lint.yaml --json out/iwp-report.json
uv run iwp-lint diff --config .iwp-lint.yaml --json out/iwp-diff-report.json
uv run iwp-lint nodes compile --config .iwp-lint.yaml
uv run iwp-lint nodes verify-compiled --config .iwp-lint.yaml
uv run iwp-lint links normalize --config .iwp-lint.yaml --write
uv run iwp-lint schema --config .iwp-lint.yaml --mode strict --json out/iwp-schema-report.json
```

### Agent View (entrypoint-first)

```bash
# Prefer iwp-build presets for runtime loops:
uv run iwp-build session diff --config .iwp-lint.yaml --preset agent-default
uv run iwp-build session diff --config .iwp-lint.yaml --include-baseline-gaps --focus-path pages/home.md --max-gap-items 20
uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default --max-diagnostics 20 --suggest-fixes
uv run iwp-build session commit --config .iwp-lint.yaml --preset ci-strict
```

Notes:

- `iwp-lint` is the engine/toolbox; `iwp-build` is the default runtime entrypoint for agent workflows.
- In `iwp-build`, merge priority is: explicit CLI args > preset args > command defaults.

## Advanced Commands (Lint Specialist)

### Core lint/schema

```bash
uv run iwp-lint check --config .iwp-lint.yaml --json out/iwp-report.json
uv run iwp-lint full --config .iwp-lint.yaml --json out/iwp-report.json
uv run iwp-lint diff --config .iwp-lint.yaml --json out/iwp-diff-report.json
uv run iwp-lint schema --config .iwp-lint.yaml --mode strict --json out/iwp-schema-report.json
uv run iwp-lint full --config .iwp-lint.yaml --min-severity error
uv run iwp-lint full --config .iwp-lint.yaml --quiet-warnings
```

Notes:

- `check` is an alias of `full`.
- When command spelling is close but invalid, CLI prints a "Did you mean ..." hint.
- `--min-severity error` prints only error diagnostics (summary still includes warning counts).
- `--quiet-warnings` hides warning diagnostics while preserving summary metrics.
- Console diagnostics are tagged with severity: `[E][IWPxxx]` / `[W][IWPxxx]`.

Status line semantics:

- `status=OK`: no errors and no warnings.
- `status=PASS_WITH_WARNINGS`: no errors, at least one warning.
- `status=FAIL`: at least one error.

### Node catalog

```bash
uv run iwp-lint nodes build --config .iwp-lint.yaml --json out/iwp-node-catalog.json
uv run iwp-lint nodes query --config .iwp-lint.yaml --source pages/home.md --text "Read Manifesto" --limit 5
uv run iwp-lint nodes query --config .iwp-lint.yaml --source pages/home.md --line 42
uv run iwp-lint nodes query --config .iwp-lint.yaml --source pages/home.md --text "Read Manifesto" --top1-only --format link
uv run iwp-lint nodes export --config .iwp-lint.yaml --source pages/home.md --json out/nodes-home.json
uv run iwp-lint nodes export --config .iwp-lint.yaml --source pages/docs/index.md --source pages/docs/manifesto.md --json out/nodes-docs.json
uv run iwp-lint nodes compile --config .iwp-lint.yaml
uv run iwp-lint nodes verify-compiled --config .iwp-lint.yaml
uv run iwp-lint links normalize --config .iwp-lint.yaml
uv run iwp-lint links normalize --config .iwp-lint.yaml --write
uv run iwp-lint links sidecar --config .iwp-lint.yaml
```

## Library API (Stable Entry)

`iwp_lint` can be used as a Python library by build orchestrators:

```python
from iwp_lint.config import load_config
from iwp_lint.api import (
    build_code_sidecar,
    compile_context,
    run_gate_suite,
    run_quality_gate,
    session_gate,
    session_commit,
    session_diff,
    session_start,
    verify_compiled,
)

config = load_config(".iwp-lint.yaml")
compile_context(config)
verify_compiled(config)
gate = run_quality_gate(config)
suite = run_gate_suite(config)
session = session_start(config)
intent = session_diff(config, session_id=session["session_id"])
gate = session_gate(config, session_id=session["session_id"])
session_commit(config, session_id=session["session_id"])
build_code_sidecar(config)
```

Primary API module:

- `iwp_lint/api.py`

## Diff Source

`diff` compares current workspace against the latest filesystem snapshot baseline.
Baseline checkpoints are managed by `iwp-build build`.

Snapshot internals are exposed through library API for orchestrators.

Session workflow (non-git, baseline-aware):

- `session_start` records baseline checkpoint for a task session
  - exactly one active session is allowed per workspace (`open|dirty|verified|blocked`)
  - starting a new session while one is active returns an error
- `session_diff` compares current workspace vs session baseline and returns:
  - changed markdown/code files
  - structured code line-level summary via `changed_code_details`
  - impacted markdown nodes
  - compact markdown change protocol source via `markdown_change_blocks` and `markdown_change_text`
  - optional impacted-node filters (`node_severity`, node type filters, critical-only)
  - markdown node excerpt (`block_text_excerpt`) with configurable truncation
  - optional baseline gap summary (`baseline_gap_summary`) with focused uncovered pairs
  - suggested trace targets (`<source_path>::<node_id>`)
  - density warnings for suspiciously high link concentration
- `session_commit` runs gate suite and atomically advances baseline only on pass

## Node Registry and Catalog

Node ID stability:

- Auto-maintained registry: `.iwp/node_registry.v1.json`
- Canonical ID format is short hash prefix (`n.<hex_prefix>`) generated per source markdown file
- Default minimum prefix length is 4; when collisions occur in the same source file, prefix length expands automatically
- Semantic signature matching reduces ID churn on list reorder or minor text edits

Node catalog for agent lookup:

- Export JSON: `.iwp/node_catalog.v1.json`
- Machine index: `.iwp/cache/node_index.v1.sqlite`
- Query path is index-first with JSON fallback
- Agent context sidecars:
  - canonical machine payload: `.iwp/compiled/json/**/*.iwc.json`
  - agent-friendly markdown view: `.iwp/compiled/md/**/*.iwc.md`

Config override:

```yaml
schema:
  file: builtin:iwp-schema.v1
```

Or use an explicit filesystem path:

```yaml
schema:
  file: schema/iwp-schema.v1.json

node_registry_file: .iwp/node_registry.v1.json
node_id_min_length: 4
node_catalog_file: .iwp/node_catalog.v1.json
code_exclude_globs:
  - "**/node_modules/**"
  - "**/dist/**"
  - "**/__pycache__/**"
  - "**/.pytest_cache/**"
cache:
  node_index_db_file: .iwp/cache/node_index.v1.sqlite
compiled:
  dir: .iwp/compiled
code_sidecar:
  enabled: true
  dir: .iwp/compiled/code
  replace_pure_link_line: true
  max_diagnostics: 20
  include_node_anchor_text: true
  include_node_block_text: true
session:
  auto_start_on_missing: false
  link_density_threshold: 0.25
  code_diff_level: summary
  code_diff_context_lines: 3
  code_diff_max_chars: 12000
  diff_node_severity: all
  markdown_excerpt_max_chars: 240
  max_text_lines: 200
  max_hint_items: 20
  max_diagnostics_items: 20

execution_presets:
  agent-default:
    session_diff:
      node_severity: error
      format: text
    session_reconcile:
      format: text
  ci-strict:
    verify:
      run_tests: true
      min_severity: error
```

`code_exclude_globs` applies to code discovery paths used by lint/normalize/sidecar/snapshot flows.
Default excludes are:

- `**/node_modules/**`
- `**/dist/**`
- `**/__pycache__/**`
- `**/.pytest_cache/**`

## Code Sidecar (for code review / agent read context)

`links sidecar` mirrors code files under `code_roots` into:

- `.iwp/compiled/code/<code_root_relative_path>`

Then it resolves `@iwp.link <source_path>::<node_id>` and injects IWP context blocks:

```text
<<<IWP_NODE_CONTEXT source=pages/home.md node=n.a327>>>
Read Manifesto
- Read Manifesto
<<<IWP_NODE_CONTEXT_END>>>
```

Rules:

- pure link line is replaced by context block when `replace_pure_link_line=true`
- mixed line keeps original text and inserts context block on following line
- generation is idempotent: output directory is rebuilt each run
- unresolved links are reported as diagnostics summary (`IWP305`) and do not fail command by default

`nodes compile` JSON output includes:

- `compiled_json_files`
- `compiled_md_files`

`.iwc.json` includes:

- document metadata (`artifact=iwc`, `version=1`, `source_path`, `source_hash`, `generated_at`, `schema_version`)
- dictionary pools (`dict.kinds`, `dict.titles`, `dict.sections`, `dict.file_types`)
- node tuples in fixed order (`node_id`, `anchor_text`, `kind_idx`, `title_idx`, `section_idx`, `file_type_idx`, `is_critical`, `source_line_start`, `source_line_end`, `block_text`)
- `block_text` is required to keep agent context grounded in original markdown snippets

`.iwc.md` includes:

- top metadata comments via `@iwp.meta` (`artifact=iwc_md`, `version=1`, `source_path`, `source_hash`, `schema_version`, `generated_at`, `entry_count`)
- per-node inline anchor comments (`<!-- @iwp.node id=<node_id> -->`) with minimal payload (id only)
- original markdown snippets kept in source order, so agents can read intent and trace node ids together

## Snapshot Storage

- Snapshot DB: `.iwp/cache/snapshots.sqlite`

Config example:

```yaml
cache:
  dir: .iwp/cache
  snapshot_db_file: .iwp/cache/snapshots.sqlite
```

## Error Codes

- `IWP101` invalid annotation format
- `IWP103` invalid or missing source_path
- `IWP104` invalid node_id
- `IWP105` source_path and node_id mismatch
- `IWP106` annotation conflict on same code position
- `IWP107` uncovered node
- `IWP108` uncovered critical node
- `IWP109` coverage threshold not met
- `verify-compiled` returns non-zero when artifacts are missing/stale/invalid
- `IWP201` missing required markdown section
- `IWP202` unknown/illegal markdown section or unmatched markdown file type
- `IWP204` invalid markdown structure (e.g. H1 count)

Coverage identity key is `(source_path, node_id)` instead of `node_id` only.

## Tiny-diff coverage guardrail

To reduce noisy failures on tiny diffs, `iwp-lint diff` supports a hybrid rule:

- use mode-specific thresholds (`thresholds_by_mode.diff`)
- when impacted node sample is tiny (`tiny_diff.min_impacted_nodes`), apply absolute tested-node guardrail
- optionally degrade tiny-diff tested diagnostics from error to warning

Config example:

```yaml
thresholds:
  node_linked_min: 60
  critical_linked_min: 80
  node_tested_min: 0

thresholds_by_mode:
  full:
    node_linked_min: 60
    critical_linked_min: 80
    node_tested_min: 0
  diff:
    node_linked_min: 60
    critical_linked_min: 80
    node_tested_min: 60

tiny_diff:
  min_impacted_nodes: 3
  node_tested_min_count: 1
  degrade_to_warning: true
```

## Common remediation hints

`iwp-lint` now prints follow-up hints for common diagnostics:

- `IWP105`: run `uv run iwp-lint links normalize --config .iwp-lint.yaml --write`
- `IWP107`: add/update/remove colocated `@iwp.link` for uncovered node boundary
- `IWP109`: review thresholds and tiny-diff settings in `.iwp-lint.yaml`

## Recommended CI Gate

```yaml
name: iwp-lint
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --group dev
      - run: uv run iwp-lint nodes compile --config .iwp-lint.yaml
      - run: uv run iwp-lint nodes verify-compiled --config .iwp-lint.yaml
      - run: uv run iwp-lint full --config .iwp-lint.yaml --json out/iwp-report.json
```

## E2E Scenarios

Lint e2e tests reuse shared fixtures and validate end-to-end diagnostics:

- shared fixtures: `test/<scenario>/`
- e2e suite entrypoint: `iwp_lint/tests/test_e2e_suite.py`

Covered edges:

- code-only diff does not create markdown source-not-found noise
- `nodes verify-compiled` fails on missing compiled artifacts
- CJK minor text change keeps stable node id in i18n schema flow
- deleted markdown node with stale link emits mismatch diagnostics

Schema profile matrix:

- every lint e2e scenario runs both:
  - `minimal` profile (shared test schema under `test/schema/`)
  - `official` profile (`schema/iwp-schema.v1.json`)
- assertions remain behavior-focused (exit code, diagnostics, diff scope) across both profiles.

Run only lint e2e:

```bash
uv run python -m unittest iwp_lint.tests.test_e2e_suite
```
