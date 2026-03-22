from __future__ import annotations

from typing import Any

from .orchestrator import run_session_reconcile as _run_session_reconcile


def run_session_reconcile(
    *,
    config: Any,
    session_id: str | None,
    json_path: str | None,
    normalize_links: bool = False,
    code_diff_level: str | None = None,
    code_diff_context_lines: int | None = None,
    code_diff_max_chars: int | None = None,
    node_severity: str | None = None,
    node_file_types: list[str] | None = None,
    node_anchor_levels: list[str] | None = None,
    node_kind_prefixes: list[str] | None = None,
    critical_only: bool = False,
    markdown_excerpt_max_chars: int | None = None,
    output_format: str = "text",
    debug_raw: bool = False,
    auto_start_session: bool = False,
    max_diagnostics: int | None = None,
    min_severity: str = "warning",
    quiet_warnings: bool = False,
    suggest_fixes: bool = False,
    warning_top_n: int | None = None,
    auto_build_sidecar: bool = False,
) -> tuple[int, dict[str, object]]:
    return _run_session_reconcile(
        config=config,
        session_id=session_id,
        json_path=json_path,
        normalize_links=normalize_links,
        code_diff_level=code_diff_level,
        code_diff_context_lines=code_diff_context_lines,
        code_diff_max_chars=code_diff_max_chars,
        node_severity=node_severity,
        node_file_types=node_file_types,
        node_anchor_levels=node_anchor_levels,
        node_kind_prefixes=node_kind_prefixes,
        critical_only=critical_only,
        markdown_excerpt_max_chars=markdown_excerpt_max_chars,
        output_format=output_format,
        debug_raw=debug_raw,
        auto_start_session=auto_start_session,
        max_diagnostics=max_diagnostics,
        min_severity=min_severity,
        quiet_warnings=quiet_warnings,
        suggest_fixes=suggest_fixes,
        warning_top_n=warning_top_n,
        auto_build_sidecar=auto_build_sidecar,
    )


__all__ = ["run_session_reconcile"]
