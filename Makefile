.PHONY: quality quality-fix

quality:
	uv run ruff check iwp_lint iwp_build test
	uv run ruff format --check iwp_lint iwp_build test

quality-fix:
	uv run ruff format iwp_lint iwp_build test
