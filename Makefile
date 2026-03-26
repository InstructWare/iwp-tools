.PHONY: quality quality-fix typecheck

UV_RUN = env -u VIRTUAL_ENV uv run

quality:
	$(UV_RUN) ruff check iwp_lint iwp_build test
	$(UV_RUN) ruff format --check iwp_lint iwp_build test

quality-fix:
	$(UV_RUN) ruff format iwp_lint iwp_build test

typecheck:
	$(UV_RUN) python -m pyright iwp_lint iwp_build test
