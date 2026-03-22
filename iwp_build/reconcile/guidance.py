from __future__ import annotations

from typing import Any

from ..output import build_next_actions
from .hints import build_blocking_pairs_topn, build_diff_excerpt, build_structured_hints
from .next_actions import (
    build_next_command_examples,
    build_recommended_next_chain,
    build_sidecar_next_actions,
)
from .path_hints import build_code_path_hints, build_suggested_code_paths


def build_reconcile_guidance(
    *,
    config: Any,
    diff_payload: dict[str, object],
    gate_payload: dict[str, object],
    diagnostics_filtered: list[object],
    max_items: int,
    max_hint_items: int,
    sidecar_fresh: bool,
) -> dict[str, object]:
    blocking_reasons = gate_payload.get("blocked_by", [])
    compiled_payload = gate_payload.get("compiled", {})
    if not isinstance(compiled_payload, dict):
        compiled_payload = {}
    next_actions = build_next_actions(
        compiled=compiled_payload,
        diagnostics=diagnostics_filtered,
    )
    hints: list[dict[str, object]] = build_structured_hints(
        diagnostics=diagnostics_filtered,
        max_items=max_hint_items,
    )
    if not sidecar_fresh:
        reasons = list(blocking_reasons) if isinstance(blocking_reasons, list) else []
        if "code_sidecar" not in reasons:
            reasons.append("code_sidecar")
        blocking_reasons = reasons
        next_actions = build_sidecar_next_actions()
        hints = [
            {
                "kind": "remediation",
                "message": "run: uv run iwp-build build --config .iwp-lint.yaml",
                "command": "uv run iwp-build build --config .iwp-lint.yaml",
            },
            {
                "kind": "remediation",
                "message": "run: uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default",
                "command": "uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default",
            },
        ]
    blocking_pairs_topn = build_blocking_pairs_topn(
        diagnostics=diagnostics_filtered,
        max_items=max_items,
    )
    code_path_hints = build_code_path_hints(
        config=config,
        changed_md_files=diff_payload.get("changed_md_files", []),
    )
    suggested_code_paths = build_suggested_code_paths(
        lint_report=gate_payload.get("lint_report", {}),
        code_path_hints=code_path_hints,
        max_items=max_hint_items,
    )
    diff_excerpt = build_diff_excerpt(
        diff_payload,
        max_items=max_hint_items,
    )
    next_command_examples = build_next_command_examples(
        next_actions=next_actions,
        max_items=max_hint_items,
    )
    recommended_next_chain = build_recommended_next_chain(
        next_actions=next_actions,
        max_items=max_hint_items,
    )
    recommended_next_command = recommended_next_chain[0] if recommended_next_chain else None
    return {
        "blocking_reasons": blocking_reasons if isinstance(blocking_reasons, list) else [],
        "next_actions": next_actions,
        "hints": hints,
        "blocking_pairs_topn": blocking_pairs_topn,
        "code_path_hints": code_path_hints,
        "suggested_code_paths": suggested_code_paths,
        "diff_excerpt": diff_excerpt,
        "next_command_examples": next_command_examples,
        "recommended_next_chain": recommended_next_chain,
        "recommended_next_command": recommended_next_command,
    }


def build_blocking_reason_details(
    *,
    blocking_reasons: object,
    sidecar_stale_reasons: object,
) -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    reasons = blocking_reasons if isinstance(blocking_reasons, list) else []
    if "code_sidecar" in reasons:
        stale = sidecar_stale_reasons if isinstance(sidecar_stale_reasons, list) else []
        details.append(
            {
                "reason": "code_sidecar",
                "message": "code sidecar is stale or missing; commit is blocked by default",
                "stale_reasons": stale,
                "next_steps": [
                    "uv run iwp-build build --config .iwp-lint.yaml",
                    "uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default",
                ],
            }
        )
    return details
