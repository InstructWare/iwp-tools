# iwp-tools

`iwp-tools` is the standalone toolkit repository for InstructWare protocol workflows.

It provides two CLI commands:

- `iwp-lint`: schema/link/coverage quality checks
- `iwp-build`: incremental build orchestration on top of `iwp-lint`

## Install

```bash
pipx install instructware-tools
iwp-lint --help
iwp-build --help
```

## Local development

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pyright iwp_lint iwp_build test
uv run python -m unittest iwp_lint.tests.test_regression
uv run python -m unittest iwp_build.tests.test_e2e_suite
uv run python -m unittest iwp_lint.tests.test_e2e_suite
```

## Build releases

```bash
uv build
uv run pyinstaller --onefile --name iwp-lint iwp_lint/__main__.py
uv run pyinstaller --onefile --name iwp-build iwp_build/__main__.py
```

## License

This repository is licensed under MIT. See [`LICENSE`](./LICENSE).
