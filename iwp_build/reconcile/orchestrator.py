from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..output import render_iwp_reconcile_text, safe_len, write_json
from .diagnostics import build_reconcile_diagnostics_bundle
from .guidance import build_reconcile_guidance
from .payload import assemble_reconcile_payload, sanitize_reconcile_payload
from .session_resolution import collect_reconcile_artifacts


@dataclass(frozen=True)
class ReconcileRequest:
    config: Any
    session_id: str | None
    json_path: str | None
    normalize_links: bool
    code_diff_level: str | None
    code_diff_context_lines: int | None
    code_diff_max_chars: int | None
    node_severity: str | None
    node_file_types: list[str] | None
    node_anchor_levels: list[str] | None
    node_kind_prefixes: list[str] | None
    critical_only: bool
    markdown_excerpt_max_chars: int | None
    output_format: str
    debug_raw: bool
    auto_start_session: bool
    max_diagnostics: int | None
    min_severity: str
    quiet_warnings: bool
    suggest_fixes: bool
    warning_top_n: int | None
    auto_build_sidecar: bool


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
    request = ReconcileRequest(
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
    artifacts = collect_reconcile_artifacts(request)
    gate_payload = _as_dict(artifacts.get("gate_payload"))
    diagnostics_bundle = build_reconcile_diagnostics_bundle(
        config=request.config,
        gate_payload=gate_payload,
        max_diagnostics=request.max_diagnostics,
        min_severity=request.min_severity,
        quiet_warnings=request.quiet_warnings,
        warning_top_n=request.warning_top_n,
    )
    diff_payload = _as_dict(artifacts.get("diff_payload"))
    diagnostics_filtered = _as_list(diagnostics_bundle.get("diagnostics_filtered"))
    guidance = build_reconcile_guidance(
        config=request.config,
        diff_payload=diff_payload,
        gate_payload=gate_payload,
        diagnostics_filtered=diagnostics_filtered,
        max_items=_as_int(diagnostics_bundle.get("max_items"), default=20),
        max_hint_items=_as_int(diagnostics_bundle.get("max_hint_items"), default=20),
        sidecar_fresh=bool(artifacts.get("sidecar_fresh", False)),
    )
    payload = assemble_reconcile_payload(
        request=request,
        artifacts=artifacts,
        diagnostics_bundle=diagnostics_bundle,
        guidance=guidance,
    )
    payload = sanitize_reconcile_payload(payload)
    if request.debug_raw:
        payload["raw"] = {
            "intent_diff": diff_payload,
            "gate_result": gate_payload,
            "lint_report": gate_payload.get("lint_report", {}),
            "lint_report_filtered": diagnostics_filtered,
        }
    written = write_json(request.json_path, payload)
    print(
        "[iwp-build] session reconcile "
        f"id={artifacts.get('resolved_session_id')} can_commit={payload['can_commit']} "
        f"changed={safe_len(diff_payload.get('changed_files') if isinstance(diff_payload, dict) else [])} "
        f"impacted_nodes={safe_len(diff_payload.get('impacted_nodes') if isinstance(diff_payload, dict) else [])}"
    )
    if written is not None:
        print(f"[iwp-build] session reconcile json path={written}")
    if request.output_format in {"text", "both"}:
        text_output = render_iwp_reconcile_text(
            payload,
            max_hint_items=_as_int(diagnostics_bundle.get("max_hint_items"), default=20),
        )
        print(text_output)
    return (0 if payload["can_commit"] else 1, payload)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
