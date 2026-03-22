from __future__ import annotations

from collections.abc import Mapping

from .utils import safe_int


def render_iwp_diff_text(
    payload: Mapping[str, object],
    *,
    max_lines: int = 200,
) -> str:
    lines: list[str] = ["<<<IWP_DIFF_V1>>>"]
    meta = payload.get("meta")
    if isinstance(meta, Mapping):
        lines.append(f"mode:{quoted(string_or_empty(meta.get('mode', '')))}")
    session_id = payload.get("session_id")
    lines.append(f"session_id:{quoted(session_id)}")
    lines.append(f"status:{quoted(payload.get('session_status', 'unknown'))}")
    filters = payload.get("filters_applied", {})
    if isinstance(filters, Mapping):
        lines.append("filters_applied:")
        lines.append(f"- node_severity={quoted(string_or_empty(filters.get('node_severity', '')))}")
        lines.append(f"- critical_only={str(bool(filters.get('critical_only', False))).lower()}")
    code_details = payload.get("changed_code_details", [])
    if isinstance(code_details, list) and code_details:
        lines.append("changed_code_summary:")
        for item in code_details[:20]:
            if not isinstance(item, Mapping):
                continue
            file_path = string_or_empty(item.get("file_path", ""))
            ranges = format_line_ranges(item.get("changed_line_ranges"))
            lines.append(f"- file={quoted(file_path)} lines={quoted(ranges)}")
    blocks = payload.get("markdown_change_blocks", [])
    line_count = 0
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            lines.append(f"file:{quoted(block.get('file', ''))}")
            ops = block.get("ops", [])
            if not isinstance(ops, list):
                continue
            for op in ops:
                if not isinstance(op, Mapping):
                    continue
                op_kind = str(op.get("op", "+"))
                line_no = safe_int(op.get("line", 0))
                node_id = str(op.get("node_id", "n/a"))
                if op_kind == "~":
                    old_text = quoted(op.get("old_text", ""))
                    new_text = quoted(op.get("new_text", ""))
                    lines.append(f"~[{line_no}]:{{{node_id}}} {old_text} => {new_text}")
                else:
                    text = quoted(op.get("text", ""))
                    lines.append(f"{op_kind}[{line_no}]:{{{node_id}}} {text}")
                line_count += 1
                if line_count >= max(0, max_lines):
                    lines.append("...truncated:true")
                    lines.append("<<<END_IWP_DIFF_V1>>>")
                    return "\n".join(lines)
    link_targets = payload.get("link_targets_suggested", [])
    if isinstance(link_targets, list) and link_targets:
        lines.append("link_targets:")
        for target in link_targets[:20]:
            lines.append(f"- {quoted(target)}")
    lines.append("<<<END_IWP_DIFF_V1>>>")
    return "\n".join(lines)


def render_iwp_reconcile_text(
    payload: Mapping[str, object],
    *,
    max_hint_items: int = 20,
) -> str:
    lines: list[str] = ["<<<IWP_RECONCILE_V1>>>"]
    meta = payload.get("meta")
    if isinstance(meta, Mapping):
        lines.append(f"mode:{quoted(string_or_empty(meta.get('mode', '')))}")
    lines.append(f"session_id:{quoted(payload.get('session_id', ''))}")
    lines.append(f"status:{quoted(payload.get('status', 'unknown'))}")
    lines.append(f"can_commit:{str(bool(payload.get('can_commit', False))).lower()}")
    summary = payload.get("summary", {})
    if isinstance(summary, Mapping):
        changed_md = safe_int(summary.get("changed_md_count", 0))
        changed_code = safe_int(summary.get("changed_code_count", 0))
        impacted = safe_int(summary.get("impacted_nodes_count", 0))
        lines.append(
            f'diff_summary:{quoted(f"changed_md={changed_md} changed_code={changed_code} impacted_nodes={impacted}")}'
        )
        warning_count = safe_int(summary.get("warning_count", 0))
        lines.append(f"warning_count:{warning_count}")
    filters = payload.get("filters_applied", {})
    if isinstance(filters, Mapping):
        lines.append("filters_applied:")
        lines.append(f"- node_severity={quoted(string_or_empty(filters.get('node_severity', '')))}")
        lines.append(f"- critical_only={str(bool(filters.get('critical_only', False))).lower()}")
    blocking = payload.get("blocking_reasons", [])
    if isinstance(blocking, list) and blocking:
        lines.append("blocking_reasons:")
        for item in blocking[:max_hint_items]:
            lines.append(f"- {quoted(item)}")
    blocking_details = payload.get("blocking_reason_details", [])
    if isinstance(blocking_details, list) and blocking_details:
        lines.append("blocking_reason_details:")
        for item in blocking_details[:max_hint_items]:
            if not isinstance(item, Mapping):
                continue
            reason = string_or_empty(item.get("reason", ""))
            message = string_or_empty(item.get("message", ""))
            lines.append(f"- reason={quoted(reason)} message={quoted(message)}")
            stale_reasons = item.get("stale_reasons", [])
            if isinstance(stale_reasons, list) and stale_reasons:
                lines.append(f"  stale_reasons={quoted(','.join(str(x) for x in stale_reasons))}")
            next_steps = item.get("next_steps", [])
            if isinstance(next_steps, list) and next_steps:
                for step in next_steps[:max_hint_items]:
                    lines.append(f"  next_step={quoted(step)}")
    diagnostics_top = payload.get("diagnostics_top", [])
    if isinstance(diagnostics_top, list) and diagnostics_top:
        lines.append("diagnostics_top:")
        for item in diagnostics_top[:max_hint_items]:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- code={quoted(string_or_empty(item.get('code', '')))} severity={quoted(string_or_empty(item.get('severity', '')))} file={quoted(string_or_empty(item.get('file_path', '')))} line={safe_int(item.get('line', 0))} message={quoted(string_or_empty(item.get('message', '')))}"
            )
    top_warnings = payload.get("top_warnings", [])
    if isinstance(top_warnings, list) and top_warnings:
        lines.append("top_warnings:")
        for item in top_warnings[:max_hint_items]:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- code={quoted(string_or_empty(item.get('code', '')))} file={quoted(string_or_empty(item.get('file_path', '')))} line={safe_int(item.get('line', 0))} message={quoted(string_or_empty(item.get('message', '')))}"
            )
    next_actions = payload.get("next_actions", [])
    if isinstance(next_actions, list) and next_actions:
        lines.append("next_actions:")
        for action in next_actions[:max_hint_items]:
            if not isinstance(action, Mapping):
                continue
            command = string_or_empty(action.get("command", ""))
            lines.append(
                f"- kind={quoted(action.get('kind', ''))} command={quoted(command)} reason={quoted(action.get('reason', ''))}"
            )
    blocking_pairs = payload.get("blocking_pairs_topn", [])
    if isinstance(blocking_pairs, list) and blocking_pairs:
        lines.append("blocking_pairs_topn:")
        for pair in blocking_pairs[:max_hint_items]:
            lines.append(f"- {quoted(pair)}")
    suggested_code_paths = payload.get("suggested_code_paths", [])
    if isinstance(suggested_code_paths, list) and suggested_code_paths:
        lines.append("suggested_code_paths:")
        for path in suggested_code_paths[:max_hint_items]:
            lines.append(f"- {quoted(path)}")
    next_command_examples = payload.get("next_command_examples", [])
    if isinstance(next_command_examples, list) and next_command_examples:
        lines.append("next_command_examples:")
        for command in next_command_examples[:max_hint_items]:
            lines.append(f"- {quoted(command)}")
    recommended_next_command = payload.get("recommended_next_command")
    if isinstance(recommended_next_command, str) and recommended_next_command.strip():
        lines.append(f"recommended_next_command:{quoted(recommended_next_command)}")
    recommended_next_chain = payload.get("recommended_next_chain", [])
    if isinstance(recommended_next_chain, list) and recommended_next_chain:
        lines.append("recommended_next_chain:")
        for command in recommended_next_chain[:max_hint_items]:
            lines.append(f"- {quoted(command)}")
    if "auto_recovered" in payload:
        lines.append(f"auto_recovered:{str(bool(payload.get('auto_recovered', False))).lower()}")
    hints = payload.get("hints", [])
    if isinstance(hints, list) and hints:
        lines.append("hints:")
        for hint in hints[:max_hint_items]:
            if isinstance(hint, Mapping):
                command = string_or_empty(hint.get("command", ""))
                lines.append(
                    f"- kind={quoted(hint.get('kind', 'hint'))} message={quoted(hint.get('message', ''))} command={quoted(command)}"
                )
            else:
                lines.append(f"- {quoted(hint)}")
    code_path_hints = payload.get("code_path_hints", [])
    if isinstance(code_path_hints, list) and code_path_hints:
        lines.append("code_path_hints:")
        for path in code_path_hints[:max_hint_items]:
            lines.append(f"- {quoted(path)}")
    diff_excerpt = payload.get("diff_excerpt", [])
    if isinstance(diff_excerpt, list) and diff_excerpt:
        lines.append("diff_excerpt:")
        for item in diff_excerpt[:max_hint_items]:
            lines.append(f"- {quoted(item)}")
    lines.append("<<<END_IWP_RECONCILE_V1>>>")
    return "\n".join(lines)


def escape_text(value: object) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def quoted(value: object) -> str:
    return f"\"{escape_text(value)}\""


def string_or_empty(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() == "none" else text


def format_line_ranges(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            continue
        start = safe_int(item[0], default=0)
        end = safe_int(item[1], default=0)
        if start <= 0 or end <= 0:
            continue
        parts.append(f"{start}" if start == end else f"{start}-{end}")
    return ",".join(parts)
