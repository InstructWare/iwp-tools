from __future__ import annotations

from typing import Any

from ..output import build_diagnostics_top, safe_int


def build_reconcile_diagnostics_bundle(
    *,
    config: Any,
    gate_payload: dict[str, object],
    max_diagnostics: int | None,
    min_severity: str,
    quiet_warnings: bool,
    warning_top_n: int | None,
) -> dict[str, object]:
    lint_report = gate_payload.get("lint_report", {})
    if not isinstance(lint_report, dict):
        lint_report = {}
    diagnostics = lint_report.get("diagnostics", [])
    all_diagnostics = diagnostics if isinstance(diagnostics, list) else []
    diagnostics_filtered = filter_reconcile_diagnostics(
        all_diagnostics,
        min_severity=min_severity,
        quiet_warnings=quiet_warnings,
    )
    max_items = (
        max(1, int(max_diagnostics))
        if max_diagnostics is not None
        else int(getattr(config.session, "max_diagnostics_items", 20))
    )
    max_hint_items = int(getattr(config.session, "max_hint_items", 20))
    diagnostics_top = build_diagnostics_top(
        diagnostics_filtered,
        max_items=max_items,
    )
    resolved_warning_top_n = (
        max(1, int(warning_top_n))
        if warning_top_n is not None
        else max(1, int(getattr(config.session, "warning_summary_top_n", 2)))
    )
    warning_items = [
        item
        for item in all_diagnostics
        if isinstance(item, dict)
        and str(item.get("severity", "error")).strip().lower() != "error"
    ]
    top_warnings = [
        {
            "code": str(item.get("code", "")),
            "file_path": str(item.get("file_path", "")),
            "line": safe_int(item.get("line", 0)),
            "message": str(item.get("message", "")),
        }
        for item in warning_items[:resolved_warning_top_n]
    ]
    return {
        "all_diagnostics": all_diagnostics,
        "diagnostics_filtered": diagnostics_filtered,
        "diagnostics_top": diagnostics_top,
        "warning_items": warning_items,
        "top_warnings": top_warnings,
        "max_items": max_items,
        "max_hint_items": max_hint_items,
    }


def filter_reconcile_diagnostics(
    diagnostics: list[object],
    *,
    min_severity: str,
    quiet_warnings: bool,
) -> list[object]:
    resolved_min = str(min_severity).strip().lower()
    if resolved_min not in {"warning", "error"}:
        resolved_min = "warning"
    threshold = 1 if resolved_min == "error" else 0
    filtered: list[object] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "error")).strip().lower()
        rank = 1 if severity == "error" else 0
        if quiet_warnings and severity != "error":
            continue
        if rank < threshold:
            continue
        filtered.append(item)
    return filtered
