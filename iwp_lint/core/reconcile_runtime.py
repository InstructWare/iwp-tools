from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import LintConfig
from ..vcs.snapshot_store import SnapshotStore
from .link_normalizer import normalize_links
from .node_catalog import (
    build_code_sidecar_context,
    compile_node_context,
    verify_code_sidecar_freshness_context,
)
from .reconcile import (
    as_int,
    as_list,
    assemble_reconcile_payload,
    build_reconcile_diagnostics_bundle,
    build_reconcile_guidance,
    sanitize_reconcile_payload,
)
from .session_service import SessionService


@dataclass(frozen=True)
class ReconcileRequest:
    config: LintConfig
    session_id: str | None
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
    debug_raw: bool
    auto_start_session: bool
    max_diagnostics: int | None
    min_severity: str
    quiet_warnings: bool
    suggest_fixes: bool
    warning_top_n: int | None
    auto_build_sidecar: bool


def resolve_session_id(
    *,
    config: LintConfig,
    session_id: str | None,
    action: str,
    auto_start_session: bool = False,
    auto_start_origin: str | None = None,
) -> tuple[str, bool]:
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip(), False
    current = _session_current(config)
    if bool(current.get("has_open_session", False)):
        session = current.get("session")
        if isinstance(session, dict):
            resolved = session.get("session_id")
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip(), False
    if auto_start_session and action in {"diff", "reconcile"}:
        origin = auto_start_origin or f"iwp session {action} auto-start"
        started = _session_start(config, metadata={"origin": origin})
        resolved = str(started.get("session_id", "")).strip() if isinstance(started, dict) else ""
        if resolved:
            return resolved, True
    command_lines = "\n".join(f"- {item}" for item in _session_next_step_commands())
    raise RuntimeError(
        f"--session-id is required for session {action} when no open session exists\n"
        "next steps:\n"
        f"{command_lines}"
    )


def run_session_reconcile(request: ReconcileRequest) -> tuple[int, dict[str, object]]:
    resolved_session_id, auto_started = resolve_session_id(
        config=request.config,
        session_id=request.session_id,
        action="reconcile",
        auto_start_session=request.auto_start_session,
        auto_start_origin="iwp session reconcile auto-start",
    )
    diff_payload = _run_session_diff_for_reconcile(request, resolved_session_id)
    normalize_payload: dict[str, object] | None = None
    if request.normalize_links:
        normalize_payload = normalize_links(config=request.config, write=True)
        diff_payload = _run_session_diff_for_reconcile(request, resolved_session_id)
    gate_payload = _session_gate(request.config, session_id=resolved_session_id)
    sidecar_freshness = verify_code_sidecar_freshness_context(config=request.config)
    sidecar_fresh = bool(sidecar_freshness.get("fresh", False))
    sidecar_auto_recovered = False
    sidecar_refresh_payload: dict[str, object] | None = None
    if request.auto_build_sidecar and not sidecar_fresh:
        baseline = _baseline_status(config=request.config)
        baseline_id_raw = baseline.get("baseline_snapshot_id")
        baseline_id = baseline_id_raw if isinstance(baseline_id_raw, int) else None
        compile_result = compile_node_context(request.config)
        sidecar_build_result = build_code_sidecar_context(
            config=request.config,
            compiled_from_baseline_id=baseline_id,
        )
        sidecar_freshness = verify_code_sidecar_freshness_context(config=request.config)
        sidecar_fresh = bool(sidecar_freshness.get("fresh", False))
        sidecar_auto_recovered = sidecar_fresh
        sidecar_refresh_payload = {
            "triggered": True,
            "recovered": sidecar_auto_recovered,
            "compile": compile_result,
            "code_sidecar": sidecar_build_result,
        }
        if sidecar_auto_recovered:
            gate_payload = _session_gate(request.config, session_id=resolved_session_id)
    elif request.auto_build_sidecar:
        sidecar_refresh_payload = {"triggered": False, "recovered": False}
    diagnostics_bundle = build_reconcile_diagnostics_bundle(
        config=request.config,
        gate_payload=gate_payload if isinstance(gate_payload, dict) else {},
        max_diagnostics=request.max_diagnostics,
        min_severity=request.min_severity,
        quiet_warnings=request.quiet_warnings,
        warning_top_n=request.warning_top_n,
    )
    guidance = build_reconcile_guidance(
        config=request.config,
        diff_payload=diff_payload if isinstance(diff_payload, dict) else {},
        gate_payload=gate_payload if isinstance(gate_payload, dict) else {},
        diagnostics_filtered=as_list(diagnostics_bundle.get("diagnostics_filtered")),
        max_items=as_int(diagnostics_bundle.get("max_items"), default=20),
        max_hint_items=as_int(diagnostics_bundle.get("max_hint_items"), default=20),
        sidecar_fresh=sidecar_fresh,
    )
    payload = assemble_reconcile_payload(
        request=request,
        resolved_session_id=resolved_session_id,
        diff_payload=diff_payload if isinstance(diff_payload, dict) else {},
        gate_payload=gate_payload if isinstance(gate_payload, dict) else {},
        diagnostics_bundle=diagnostics_bundle,
        guidance=guidance,
        sidecar_freshness=sidecar_freshness if isinstance(sidecar_freshness, dict) else {},
        sidecar_fresh=sidecar_fresh,
        normalize_payload=normalize_payload,
        sidecar_auto_recovered=sidecar_auto_recovered,
        sidecar_refresh_payload=sidecar_refresh_payload,
    )
    payload = sanitize_reconcile_payload(payload)
    if request.debug_raw:
        payload["raw"] = {
            "intent_diff": diff_payload if isinstance(diff_payload, dict) else {},
            "gate_result": gate_payload if isinstance(gate_payload, dict) else {},
            "lint_report": (
                gate_payload.get("lint_report", {}) if isinstance(gate_payload, dict) else {}
            ),
            "lint_report_filtered": diagnostics_bundle.get("diagnostics_filtered", []),
        }
    if auto_started:
        payload["auto_started_session"] = {"session_id": resolved_session_id, "action": "reconcile"}
    return (0 if bool(payload.get("can_commit", False)) else 1, payload)


def _run_session_diff_for_reconcile(
    request: ReconcileRequest,
    resolved_session_id: str,
) -> dict[str, object]:
    return _session_diff(
        request.config,
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


def _baseline_status(*, config: LintConfig) -> dict[str, Any]:
    db_path = (config.project_root / config.snapshot_db_file).resolve()
    store = SnapshotStore(db_path)
    latest = store.latest_snapshot_info()
    if latest is None:
        return {
            "snapshot_db_path": db_path.as_posix(),
            "baseline_exists": False,
            "baseline_snapshot_id": None,
            "baseline_created_at": None,
        }
    return {
        "snapshot_db_path": db_path.as_posix(),
        "baseline_exists": True,
        "baseline_snapshot_id": latest["snapshot_id"],
        "baseline_created_at": latest["created_at"],
    }


def _session_start(
    config: LintConfig,
    *,
    metadata: dict[str, object] | None = None,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.start(metadata=metadata)


def _session_current(config: LintConfig) -> dict[str, Any]:
    service = SessionService(config)
    return service.current()


def _session_diff(
    config: LintConfig,
    *,
    session_id: str,
    code_diff_level: str | None = None,
    code_diff_context_lines: int | None = None,
    code_diff_max_chars: int | None = None,
    node_severity: str | None = None,
    node_file_types: list[str] | None = None,
    node_anchor_levels: list[str] | None = None,
    node_kind_prefixes: list[str] | None = None,
    critical_only: bool = False,
    markdown_excerpt_max_chars: int | None = None,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.diff(
        session_id=session_id,
        code_diff_level=code_diff_level,
        code_diff_context_lines=code_diff_context_lines,
        code_diff_max_chars=code_diff_max_chars,
        node_severity=node_severity,
        node_file_types=node_file_types,
        node_anchor_levels=node_anchor_levels,
        node_kind_prefixes=node_kind_prefixes,
        critical_only=critical_only,
        markdown_excerpt_max_chars=markdown_excerpt_max_chars,
    )


def _session_gate(
    config: LintConfig,
    *,
    session_id: str,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.gate(session_id=session_id)
