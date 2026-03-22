from __future__ import annotations

from typing import Any

from ..output import (
    print_repair_plan_hint,
    safe_int,
    safe_len,
    write_json,
)

try:
    from iwp_lint.api import (
        baseline_status,
        build_code_sidecar,
        compile_context,
        normalize_annotations,
        snapshot_action,
    )
    from iwp_lint.core.engine import print_console_report, run_diff, run_full
except ImportError:
    from ...iwp_lint.api import (
        baseline_status,
        build_code_sidecar,
        compile_context,
        normalize_annotations,
        snapshot_action,
    )
    from ...iwp_lint.core.engine import print_console_report, run_diff, run_full


def run_build(
    config: Any,
    mode: str,
    json_path: str | None,
    normalize_links: bool = False,
    emit_code_sidecar: bool = True,
) -> int:
    baseline_before = baseline_status(config)
    print(
        "[iwp-build] baseline "
        f"exists={baseline_before.get('baseline_exists')} "
        f"id={baseline_before.get('baseline_snapshot_id')}"
    )
    normalize_result: dict[str, object] | None = None
    if normalize_links:
        normalize_result = normalize_annotations(config=config, write=True)
        print(
            "[iwp-build] normalize-links "
            f"changed={normalize_result.get('changed_count', 0)} "
            f"removed_stale={normalize_result.get('removed_stale_links', 0)} "
            f"removed_dup={normalize_result.get('removed_duplicate_links', 0)}"
        )

    compile_result = compile_context(config)
    sidecar_result: dict[str, object] | None = None
    sidecar_enabled = bool(getattr(config, "code_sidecar", None)) and bool(
        getattr(getattr(config, "code_sidecar", None), "enabled", True)
    )
    if emit_code_sidecar and sidecar_enabled:
        baseline_id_raw = baseline_before.get("baseline_snapshot_id")
        baseline_id = baseline_id_raw if isinstance(baseline_id_raw, int) else None
        sidecar_result = build_code_sidecar(
            config,
            compiled_from_baseline_id=baseline_id,
        )
        print(
            "[iwp-build] code-sidecar "
            f"files={sidecar_result.get('files_written', 0)} "
            f"resolved={sidecar_result.get('resolved_links', 0)} "
            f"unresolved={sidecar_result.get('unresolved_links', 0)}"
        )
    intent = {
        "changed_files": [],
        "changed_md_files": [],
        "changed_code_files": [],
        "changed_count": 0,
        "impacted_nodes": [],
    }
    build_mode = mode
    if mode in {"auto", "diff"}:
        try:
            diff_payload = snapshot_action(config=config, action="diff")
            if isinstance(diff_payload, dict):
                intent = diff_payload
        except RuntimeError as exc:
            if "baseline not found" not in str(exc).lower():
                raise
        try:
            gap_report = run_diff(config, None, None)
            build_mode = "diff"
        except RuntimeError as exc:
            if "baseline not found" not in str(exc).lower():
                raise
            gap_report = run_full(config)
            build_mode = "bootstrap_full"
    else:
        gap_report = run_full(config)
        build_mode = "full"

    gap_error_count = int(gap_report["summary"]["error_count"])
    baseline_bootstrapped = False
    summary = {
        "build_mode": build_mode,
        "baseline_bootstrapped": baseline_bootstrapped,
        "compiled_count": int(compile_result.get("compiled_count", 0)),
        "removed_count": int(compile_result.get("removed_count", 0)),
        "changed_count": int(intent.get("changed_count", 0)),
        "changed_md_count": safe_len(intent.get("changed_md_files")),
        "changed_code_count": safe_len(intent.get("changed_code_files")),
        "impacted_nodes_count": safe_len(intent.get("impacted_nodes")),
        "gap_error_count": gap_error_count,
        "gap_warning_count": int(gap_report["summary"]["warning_count"]),
        "normalize_changed_count": safe_int((normalize_result or {}).get("changed_count", 0)),
        "code_sidecar_files": safe_int((sidecar_result or {}).get("files_written", 0)),
        "code_sidecar_unresolved": safe_int((sidecar_result or {}).get("unresolved_links", 0)),
    }
    print(
        "[iwp-build] build "
        f"mode={summary['build_mode']} "
        f"compiled={summary['compiled_count']} removed={summary['removed_count']} "
        f"changed={summary['changed_count']} impacted_nodes={summary['impacted_nodes_count']} "
        f"gap_errors={summary['gap_error_count']}"
    )
    print_console_report(gap_report)
    full_payload = {
        "summary": summary,
        "normalize": normalize_result,
        "compile": compile_result,
        "code_sidecar": sidecar_result,
        "intent_diff": intent,
        "gap_report": gap_report,
    }
    if gap_error_count > 0:
        full_payload["gate_status"] = "FAIL"
        full_payload["blocked_by"] = ["lint"]
        print("[iwp-build] build failed; keep previous baseline unchanged")
        print_repair_plan_hint(gap_report)
        print(
            "[iwp-build] next "
            "fix diagnostics above, then rerun: "
            "uv run iwp-build build --config .iwp-lint.yaml --mode diff"
        )
        print("[iwp-build] baseline remains " f"id={baseline_before.get('baseline_snapshot_id')}")
        _write_build_outputs(
            json_path=json_path,
            full_payload=full_payload,
            summary=summary,
        )
        return 1
    full_payload["gate_status"] = "SKIPPED"
    full_payload["blocked_by"] = []
    full_payload["checkpoint"] = {
        "updated": False,
        "writer": "session_commit",
        "reason": "build_is_readonly",
    }
    full_payload["no_checkpoint"] = True
    _write_build_outputs(
        json_path=json_path,
        full_payload=full_payload,
        summary=summary,
    )
    print(
        "[iwp-build] build completed without baseline checkpoint; "
        "run `iwp-build session commit` to advance baseline"
    )
    return 0


def _write_build_outputs(
    *,
    json_path: str | None,
    full_payload: dict[str, object],
    summary: dict[str, object],
) -> None:
    written_json_path = write_json(json_path, full_payload)
    if written_json_path is not None:
        print(
            "[iwp-build] build json "
            f"path={written_json_path} "
            f"changed_md={summary['changed_md_count']} "
            f"impacted_nodes={summary['impacted_nodes_count']} "
            f"gap_errors={summary['gap_error_count']}"
        )
