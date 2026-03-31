from __future__ import annotations

from ..output import safe_len
from .guidance import build_blocking_reason_details


def assemble_reconcile_payload(
    *,
    request: object,
    artifacts: dict[str, object],
    diagnostics_bundle: dict[str, object],
    guidance: dict[str, object],
) -> dict[str, object]:
    workflow_mode = _resolve_workflow_mode(request)
    mode_warnings = _build_mode_warnings(request)
    gate_payload = _as_dict(artifacts.get("gate_payload"))
    diff_payload = _as_dict(artifacts.get("diff_payload"))
    sidecar_freshness = _as_dict(artifacts.get("sidecar_freshness"))
    gate_status = str(gate_payload.get("gate_status", "FAIL"))
    compiled_payload = _as_dict(gate_payload.get("compiled"))
    compiled_ok = bool(
        gate_payload.get(
            "compiled_ok",
            compiled_payload.get("ok", False),
        )
    )
    compiled_checked_at = gate_payload.get("compiled_checked_at")
    sidecar_fresh = bool(artifacts.get("sidecar_fresh", False))
    can_commit = gate_status != "FAIL" and sidecar_fresh
    sidecar_stale_reasons = sidecar_freshness.get("stale_reasons", [])
    warning_items = diagnostics_bundle.get("warning_items", [])
    top_warnings = diagnostics_bundle.get("top_warnings", [])
    blocking_reasons = guidance.get("blocking_reasons", [])
    return {
        "meta": {
            "protocol_block": "IWP_RECONCILE_V1",
            "mode": "decision",
            "schema_version": "iwp.session.reconcile.v1",
            "workflow_mode": workflow_mode,
        },
        "session_id": artifacts.get("resolved_session_id"),
        "status": "pass" if can_commit else "blocked",
        "can_commit": can_commit,
        "compiled_ok": compiled_ok,
        "compiled_checked_at": compiled_checked_at,
        "sidecar_fresh": sidecar_fresh,
        "compiled_at": sidecar_freshness.get("compiled_at"),
        "compiled_from_baseline_id": sidecar_freshness.get("compiled_from_baseline_id"),
        "sidecar_stale_reasons": sidecar_stale_reasons,
        "blocking_reason_details": build_blocking_reason_details(
            blocking_reasons=blocking_reasons,
            sidecar_stale_reasons=sidecar_stale_reasons,
        ),
        "warning_count": len(warning_items) if isinstance(warning_items, list) else 0,
        "top_warnings": top_warnings if isinstance(top_warnings, list) else [],
        "summary": {
            "changed_md_count": safe_len(diff_payload.get("changed_md_files")),
            "changed_code_count": safe_len(diff_payload.get("changed_code_files")),
            "impacted_nodes_count": safe_len(diff_payload.get("impacted_nodes")),
            "gate_status": gate_status,
            "compiled_ok": compiled_ok,
            "compiled_checked_at": compiled_checked_at,
            "blocked_by": blocking_reasons if isinstance(blocking_reasons, list) else [],
            "warning_count": len(warning_items) if isinstance(warning_items, list) else 0,
            "sidecar_fresh": sidecar_fresh,
        },
        "intent_diff": compact_intent_diff(diff_payload),
        "filters_applied": diff_payload.get("filters_applied", {}),
        "diagnostics_top": diagnostics_bundle.get("diagnostics_top", []),
        "blocking_reasons": blocking_reasons if isinstance(blocking_reasons, list) else [],
        "next_actions": guidance.get("next_actions", []),
        "next_command_examples": guidance.get("next_command_examples", [])
        if bool(getattr(request, "suggest_fixes", False))
        else [],
        "recommended_next_command": guidance.get("recommended_next_command"),
        "recommended_next_chain": guidance.get("recommended_next_chain", []),
        "hints": guidance.get("hints", []),
        "code_path_hints": guidance.get("code_path_hints", []),
        "blocking_pairs_topn": guidance.get("blocking_pairs_topn", []),
        "suggested_code_paths": guidance.get("suggested_code_paths", [])
        if bool(getattr(request, "suggest_fixes", False))
        else guidance.get("code_path_hints", []),
        "diff_excerpt": guidance.get("diff_excerpt", []),
        "normalize": artifacts.get("normalize_payload"),
        "auto_recovered": bool(artifacts.get("sidecar_auto_recovered", False)),
        "sidecar_refresh": artifacts.get("sidecar_refresh_payload"),
        "mode_warnings": mode_warnings,
    }


def compact_intent_diff(payload: dict[str, object]) -> dict[str, object]:
    changed_files = payload.get("changed_files")
    changed_md_files = payload.get("changed_md_files")
    changed_code_files = payload.get("changed_code_files")
    impacted_nodes = payload.get("impacted_nodes")
    return {
        "session_id": payload.get("session_id"),
        "baseline_id_before": payload.get("baseline_id_before"),
        "baseline_id_after": payload.get("baseline_id_after"),
        "changed_files": changed_files if isinstance(changed_files, list) else [],
        "changed_md_files": changed_md_files if isinstance(changed_md_files, list) else [],
        "changed_code_files": changed_code_files if isinstance(changed_code_files, list) else [],
        "changed_count": safe_len(changed_files),
        "impacted_nodes_count": safe_len(impacted_nodes),
        "link_targets_suggested": payload.get("link_targets_suggested", []),
        "filters_applied": payload.get("filters_applied", {}),
        "code_diff_level": payload.get("code_diff_level"),
        "session_status": payload.get("session_status"),
    }


def sanitize_reconcile_payload(payload: dict[str, object]) -> dict[str, object]:
    sanitized = dict(payload)
    if bool(sanitized.get("can_commit", False)):
        sanitized["blocking_reasons"] = []
        sanitized["blocking_pairs_topn"] = []
        sanitized["next_actions"] = []
        sanitized["next_command_examples"] = []
        sanitized["recommended_next_command"] = None
        sanitized["recommended_next_chain"] = []
    return sanitized


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _resolve_workflow_mode(request: object) -> str:
    config = getattr(request, "config", None)
    workflow = getattr(config, "workflow", None)
    mode = str(getattr(workflow, "mode", "aligned")).strip().lower()
    if mode in {"fast", "aligned"}:
        return mode
    return "aligned"


def _build_mode_warnings(request: object) -> list[str]:
    workflow_mode = _resolve_workflow_mode(request)
    if workflow_mode != "fast":
        return []
    config = getattr(request, "config", None)
    authoring = getattr(config, "authoring", None)
    node_generation_mode = (
        str(getattr(authoring, "node_generation_mode", "structural")).strip().lower()
    )
    if node_generation_mode == "structural":
        return [
            "workflow.mode=fast with node_generation_mode=structural may generate high trace pressure; prefer checkpoint loop before aligned gate."
        ]
    return []
