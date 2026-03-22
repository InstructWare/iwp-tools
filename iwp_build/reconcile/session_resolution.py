from __future__ import annotations

from typing import Any

try:
    from iwp_lint.api import (
        baseline_status,
        build_code_sidecar,
        compile_context,
        normalize_annotations,
        session_current,
        session_diff,
        session_gate,
        session_start,
        verify_code_sidecar_freshness,
    )
except ImportError:
    from ...iwp_lint.api import (
        baseline_status,
        build_code_sidecar,
        compile_context,
        normalize_annotations,
        session_current,
        session_diff,
        session_gate,
        session_start,
        verify_code_sidecar_freshness,
    )


def collect_reconcile_artifacts(request: Any) -> dict[str, object]:
    resolved_session_id = resolve_reconcile_session_id(request)
    diff_payload = run_session_diff_for_reconcile(request, resolved_session_id)
    normalize_payload: dict[str, object] | None = None
    if bool(getattr(request, "normalize_links", False)):
        normalize_payload = normalize_annotations(config=request.config, write=True)
        diff_payload = run_session_diff_for_reconcile(request, resolved_session_id)
    gate_payload = session_gate(config=request.config, session_id=resolved_session_id)
    sidecar_freshness = verify_code_sidecar_freshness(config=request.config)
    sidecar_fresh = bool(sidecar_freshness.get("fresh", False))
    sidecar_auto_recovered = False
    sidecar_refresh_payload: dict[str, object] | None = None
    if bool(getattr(request, "auto_build_sidecar", False)) and not sidecar_fresh:
        baseline = baseline_status(config=request.config)
        baseline_id_raw = baseline.get("baseline_snapshot_id")
        baseline_id = baseline_id_raw if isinstance(baseline_id_raw, int) else None
        compile_result = compile_context(request.config)
        sidecar_build_result = build_code_sidecar(
            config=request.config,
            compiled_from_baseline_id=baseline_id,
        )
        sidecar_freshness = verify_code_sidecar_freshness(config=request.config)
        sidecar_fresh = bool(sidecar_freshness.get("fresh", False))
        sidecar_auto_recovered = sidecar_fresh
        sidecar_refresh_payload = {
            "triggered": True,
            "recovered": sidecar_auto_recovered,
            "compile": compile_result,
            "code_sidecar": sidecar_build_result,
        }
        if sidecar_auto_recovered:
            gate_payload = session_gate(config=request.config, session_id=resolved_session_id)
    elif bool(getattr(request, "auto_build_sidecar", False)):
        sidecar_refresh_payload = {"triggered": False, "recovered": False}
    return {
        "resolved_session_id": resolved_session_id,
        "diff_payload": diff_payload,
        "normalize_payload": normalize_payload,
        "gate_payload": gate_payload,
        "sidecar_freshness": sidecar_freshness,
        "sidecar_fresh": sidecar_fresh,
        "sidecar_auto_recovered": sidecar_auto_recovered,
        "sidecar_refresh_payload": sidecar_refresh_payload,
    }


def resolve_reconcile_session_id(request: Any) -> str:
    session_id = getattr(request, "session_id", None)
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    current = session_current(config=request.config)
    if bool(current.get("has_open_session", False)):
        session_info = current.get("session", {})
        if not isinstance(session_info, dict):
            raise RuntimeError("current session payload is invalid")
        resolved = str(session_info.get("session_id", "")).strip()
        if resolved:
            return resolved
    if bool(getattr(request, "auto_start_session", False)):
        started = session_start(
            config=request.config,
            metadata={"origin": "iwp-build session reconcile auto-start"},
        )
        started_id = str(started.get("session_id", "")).strip() if isinstance(started, dict) else ""
        if not started_id:
            raise RuntimeError("failed to auto-start session for reconcile")
        print(f"[iwp-build] auto-started session id={started_id} for action=reconcile")
        return started_id
    commands = _session_next_step_commands()
    command_lines = "\n".join(f"- {item}" for item in commands)
    raise RuntimeError(
        "no open session\n"
        "next steps:\n"
        f"{command_lines}"
    )


def run_session_diff_for_reconcile(request: Any, resolved_session_id: str) -> dict[str, object]:
    return session_diff(
        config=request.config,
        session_id=resolved_session_id,
        code_diff_level=request.code_diff_level,
        code_diff_context_lines=request.code_diff_context_lines,
        code_diff_max_chars=request.code_diff_max_chars,
        node_severity=request.node_severity,
        node_file_types=request.node_file_types,
        node_anchor_levels=request.node_anchor_levels,
        node_kind_prefixes=request.node_kind_prefixes,
        critical_only=request.critical_only,
        markdown_excerpt_max_chars=request.markdown_excerpt_max_chars,
    )


def _session_next_step_commands() -> list[str]:
    return [
        "iwp-build session start --config <cfg>",
        "iwp-build session current --config <cfg>",
        "iwp-build session diff --config <cfg> --preset agent-default",
        "iwp-build session reconcile --config <cfg> --preset agent-default",
    ]
