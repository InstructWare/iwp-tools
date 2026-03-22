from __future__ import annotations

import re

from ..output import collect_remediation_hints


def build_structured_hints(
    *,
    diagnostics: list[object],
    max_items: int,
) -> list[dict[str, object]]:
    hints = collect_remediation_hints(diagnostics)
    results: list[dict[str, object]] = []
    for hint in hints[: max(0, max_items)]:
        command = hint.replace("run: ", "") if hint.startswith("run: ") else ""
        results.append(
            {
                "kind": "remediation",
                "message": hint,
                "command": command,
            }
        )
    return results


def build_blocking_pairs_topn(*, diagnostics: list[object], max_items: int) -> list[str]:
    pairs: list[str] = []
    seen: set[str] = set()
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", ""))
        if code not in {"IWP107", "IWP108"}:
            continue
        source_path = str(item.get("file_path", "")).strip()
        message = str(item.get("message", ""))
        matched = re.search(r"(n\\.[a-zA-Z0-9]+)", message)
        node_id = matched.group(1) if matched is not None else ""
        if not source_path or not node_id:
            continue
        pair = f"{source_path}::{node_id}"
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
        if len(pairs) >= max(1, max_items):
            break
    return pairs


def build_diff_excerpt(diff_payload: dict[str, object], *, max_items: int) -> list[str]:
    text = str(diff_payload.get("markdown_change_text", "")).strip()
    if not text:
        return []
    lines = text.splitlines()
    return lines[: max(0, max_items)]
