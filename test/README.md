# tools/test

Shared end-to-end fixtures for `iwp-build` and `iwp-lint` tests.

Each scenario is self-contained and includes:

- `InstructWare.iw/`: markdown intent docs
- `_ir/`: implementation stubs where `@iwp.link` comments are written by tests
- `.iwp-lint.yaml`: scenario-local config
- `schema.file`: points to shared test schema under `test/schema/`
- `expected/`: optional notes (assertions are encoded in test code)

Shared schema profiles:

- `test/schema/test-schema.min.json`: default minimal schema for most e2e scenarios
- `test/schema/test-schema.i18n.min.json`: i18n-focused minimal schema

Schema execution policy:

- all e2e scenarios run both schema profiles by default via `subTest`:
  - `minimal` (scenario shared test schema)
  - `official` (`schema/iwp-schema.v1.json`)

Scenario names map to workflow events and edge cases:

- `feature_add_node`
- `feature_delete_node`
- `feature_modify_node`
- `code_only_change`
- `bootstrap_first_build`
- `bootstrap_no_baseline_no_links`
- `compiled_stale_or_missing`
- `i18n_zh_en`

Test entry points:

```bash
uv run python -m unittest iwp_build.tests.test_e2e_suite
uv run python -m unittest iwp_lint.tests.test_e2e_suite
```
