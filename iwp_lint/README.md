# iwp-lint

`iwp-lint` is the quality engine for IWP projects.

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
@iwp.link views/pages/home.md::n.abc123def4567890
```

## CLI Commands

### Core lint/schema

```bash
uv run iwp-lint full --config .iwp-lint.yaml --json out/iwp-report.json
uv run iwp-lint diff --config .iwp-lint.yaml --json out/iwp-diff-report.json
uv run iwp-lint schema --config .iwp-lint.yaml --mode strict --json out/iwp-schema-report.json
```

### Node catalog

```bash
uv run iwp-lint nodes build --config .iwp-lint.yaml --json out/iwp-node-catalog.json
uv run iwp-lint nodes query --config .iwp-lint.yaml --source views/pages/home.md --text "Read Manifesto" --limit 5
uv run iwp-lint nodes query --config .iwp-lint.yaml --source views/pages/home.md --line 42
uv run iwp-lint nodes query --config .iwp-lint.yaml --source views/pages/home.md --text "Read Manifesto" --top1-only --format link
uv run iwp-lint nodes export --config .iwp-lint.yaml --source views/pages/home.md --json out/nodes-home.json
uv run iwp-lint nodes export --config .iwp-lint.yaml --source views/pages/docs/index.md --source views/pages/docs/manifesto.md --json out/nodes-docs.json
uv run iwp-lint nodes compile --config .iwp-lint.yaml
uv run iwp-lint nodes verify-compiled --config .iwp-lint.yaml
```

Backward-compatible module entry still works:

```bash
python -m iwp_lint full --config .iwp-lint.yaml
```

## Library API (Stable Entry)

`iwp_lint` can be used as a Python library by build orchestrators:

```python
from iwp_lint.config import load_config
from iwp_lint.api import compile_context, run_quality_gate, verify_compiled

config = load_config(".iwp-lint.yaml")
compile_context(config)
verify_compiled(config)
gate = run_quality_gate(config)
```

Primary API module:

- `iwp_lint/api.py`

## Diff Source

`diff` compares current workspace against the latest filesystem snapshot baseline.
Baseline checkpoints are managed by `iwp-build build`.

Legacy note:

- `iwp-lint snapshot *` and `iwp-lint tasks *` are no longer exposed as CLI commands.
- Snapshot internals remain available through library API for orchestrators.

## Node Registry and Catalog

Node ID stability:

- Auto-maintained registry: `.iwp/node_registry.v1.json`
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
node_catalog_file: .iwp/node_catalog.v1.json
cache:
  node_index_db_file: .iwp/cache/node_index.v1.sqlite
compiled:
  dir: .iwp/compiled
```

`nodes compile` JSON output includes:

- `compiled_json_files`
- `compiled_md_files`

`.iwc.json` includes:

- document metadata (`artifact=iwc`, `version=2`, `source_path`, `source_hash`, `generated_at`, `schema_version`)
- dictionary pools (`dict.kinds`, `dict.titles`, `dict.sections`, `dict.file_types`)
- node tuples in fixed order (`node_id`, `anchor_text`, `kind_idx`, `title_idx`, `section_idx`, `file_type_idx`, `is_critical`, `source_line_start`, `source_line_end`, `block_text`)
- `block_text` is required in v2 to keep agent context grounded in original markdown snippets

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
- compatibility wrapper: `iwp_lint/tests/test_e2e_flow.py`

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

