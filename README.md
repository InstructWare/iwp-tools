# iwp-tools

`iwp-tools` is the standalone toolkit repository for InstructWare protocol workflows.

It provides two CLI commands:

- `iwp-lint`: schema/link/coverage quality checks
- `iwp-build`: incremental build orchestration on top of `iwp-lint`

Quick command map:

- `iwp-lint check` == `iwp-lint full`
- `iwp-build build --mode diff` for implementation checkpoint
- `iwp-build verify` for compiled + lint gate validation

If validation fails, prefer this quick recovery path:

```bash
uv run iwp-lint links normalize --config .iwp-lint.yaml --write
uv run iwp-build build --config .iwp-lint.yaml --mode diff
uv run iwp-build verify --config .iwp-lint.yaml
```

## Install

Use whichever global tool manager your environment already standardizes on.

### Option A: pipx (isolated global CLI)

```bash
pipx install instructware-tools
iwp-lint --help
iwp-build --help
```

### Option B: uv tool (isolated global CLI)

```bash
uv tool install instructware-tools
iwp-lint --help
iwp-build --help
```

### Option C: uvx (one-off execution, no persistent install)

```bash
uvx instructware-tools iwp-lint --help
uvx instructware-tools iwp-build --help
```

## Local development

Run all quality commands from the `tools` directory.

```bash
uv sync --group dev
make quality
make typecheck
uv run python -m unittest iwp_lint.tests.test_regression
uv run python -m unittest iwp_build.tests.test_e2e_suite
uv run python -m unittest iwp_lint.tests.test_e2e_suite
```

If your current directory is repository root, use:

```bash
cd tools
uv sync --group dev --frozen
make quality
make typecheck
```

## Build releases

```bash
uv build
uv run pyinstaller --onefile --name iwp-lint iwp_lint/__main__.py
uv run pyinstaller --onefile --name iwp-build iwp_build/__main__.py
```

## License

This repository is licensed under MIT. See [`LICENSE`](./LICENSE).
