from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..core.models import MarkdownNode
from ..schema.schema_loader import load_schema_profile
from ..schema.schema_semantics import match_file_type, resolve_section_keys
from ..schema.schema_validator import list_markdown_rel_paths
from ..versioning import DEFAULT_NODE_REGISTRY_FILE
from .node_registry import NodeRegistry, build_short_node_ids_by_source, build_signature

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
NODE_OVERRIDE_RE = re.compile(r"<!--\s*@iwp\.node\s+([a-z0-9][a-z0-9._-]{2,127})\s*-->")
TEXT_MARKER_RE = re.compile(r"^\[text\]\s*:?\s*(.*)$")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"`+", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "node"


def parse_markdown_nodes(
    iwp_root: Path,
    critical_patterns: list[str],
    schema_path: Path | str,
    critical_granularity: str = "all",
    exclude_markdown_globs: list[str] | None = None,
    node_registry_file: str = DEFAULT_NODE_REGISTRY_FILE,
    node_id_min_length: int = 4,
) -> list[MarkdownNode]:
    profile = load_schema_profile(schema_path)
    registry = NodeRegistry((iwp_root.parent / node_registry_file).resolve())
    nodes: list[MarkdownNode] = []
    for rel in list_markdown_rel_paths(iwp_root, exclude_markdown_globs):
        path = iwp_root / rel
        file_type = match_file_type(rel, profile.file_type_schemas)
        file_type_id = file_type.id if file_type else "unknown"
        allowed_section_keys = {item.key for item in file_type.sections} if file_type else set()
        allow_unknown_sections = file_type.allow_unknown_sections if file_type else False
        nodes.extend(
            _parse_one_file(
                path,
                iwp_root,
                critical_patterns,
                section_i18n=profile.section_i18n,
                file_type_id=file_type_id,
                allowed_section_keys=allowed_section_keys,
                allow_unknown_sections=allow_unknown_sections,
                kind_rule_format=profile.kind_rule_format,
                text_marker_enabled=profile.text_marker_enabled,
                text_marker_token=profile.text_marker_token,
                registry=registry,
                critical_granularity=critical_granularity,
            )
        )
    nodes = _with_line_end_ranges(nodes, iwp_root)
    short_map = _finalize_short_node_ids(nodes, node_id_min_length=node_id_min_length)
    for stable_key, short_id in short_map.items():
        registry.set_canonical_uid(stable_key, short_id)
    registry.flush()
    return nodes


def _parse_one_file(
    path: Path,
    iwp_root: Path,
    critical_patterns: list[str],
    section_i18n: dict[str, dict[str, list[str]]],
    file_type_id: str,
    allowed_section_keys: set[str],
    allow_unknown_sections: bool,
    kind_rule_format: str,
    text_marker_enabled: bool,
    text_marker_token: str,
    registry: NodeRegistry,
    critical_granularity: str,
) -> list[MarkdownNode]:
    rel = path.relative_to(iwp_root).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_stack: list[tuple[int, str]] = []
    local_nodes: list[MarkdownNode] = []
    pending_override: str | None = None
    current_section_key = "document"

    for idx, line in enumerate(lines, start=1):
        override_match = NODE_OVERRIDE_RE.search(line)
        if override_match:
            pending_override = override_match.group(1)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            if level == 2:
                resolved = resolve_section_keys(title, section_i18n)
                if len(resolved) == 1 and (
                    not allowed_section_keys or resolved[0] in allowed_section_keys
                ):
                    current_section_key = resolved[0]
                elif allow_unknown_sections:
                    current_section_key = slugify(title)
                else:
                    current_section_key = "unknown_section"
            elif level < 2:
                current_section_key = "document"
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            title_path = ".".join(slugify(item[1]) for item in heading_stack)
            parent_titles = [item[1] for item in heading_stack[:-1]]
            signature = build_signature(
                source_path=rel,
                file_type_id=file_type_id,
                section_key=current_section_key,
                node_type="heading",
                parent_titles=parent_titles,
                anchor_text=title,
            )
            node_id = registry.assign_uid(signature, pending_override)
            pending_override = None
            is_critical = _is_critical(title, critical_patterns)
            local_nodes.append(
                MarkdownNode(
                    node_id=node_id,
                    source_path=rel,
                    line_start=idx,
                    line_end=idx,
                    title_path=title_path,
                    anchor_text=title,
                    section_key=current_section_key,
                    file_type_id=file_type_id,
                    computed_kind=_compute_kind(
                        kind_rule_format, file_type_id, current_section_key
                    ),
                    anchor_level=_resolve_anchor_level(
                        file_type_id=file_type_id,
                        section_key=current_section_key,
                        is_text=False,
                    ),
                    is_critical=is_critical,
                )
            )
            continue

        list_match = LIST_ITEM_RE.match(line)
        if list_match and heading_stack:
            item_text = list_match.group(1).strip()
            item_text, is_text = _extract_text_marker(
                item_text=item_text,
                text_marker_enabled=text_marker_enabled,
                text_marker_token=text_marker_token,
            )
            title_path = ".".join(slugify(item[1]) for item in heading_stack)
            parent_titles = [item[1] for item in heading_stack]
            signature = build_signature(
                source_path=rel,
                file_type_id=file_type_id,
                section_key=current_section_key,
                node_type="list_item",
                parent_titles=parent_titles,
                anchor_text=item_text,
            )
            node_id = registry.assign_uid(signature, pending_override)
            pending_override = None
            title_is_critical = _is_critical(heading_stack[-1][1], critical_patterns)
            item_is_critical = _is_critical(item_text, critical_patterns)
            if critical_granularity == "title_only":
                is_critical = item_is_critical
            else:
                is_critical = item_is_critical or title_is_critical
            local_nodes.append(
                MarkdownNode(
                    node_id=node_id,
                    source_path=rel,
                    line_start=idx,
                    line_end=idx,
                    title_path=title_path,
                    anchor_text=item_text,
                    section_key=current_section_key,
                    file_type_id=file_type_id,
                    computed_kind=_compute_kind(
                        kind_rule_format, file_type_id, current_section_key
                    ),
                    anchor_level=_resolve_anchor_level(
                        file_type_id=file_type_id,
                        section_key=current_section_key,
                        is_text=is_text,
                    ),
                    is_critical=is_critical,
                )
            )

    return local_nodes


def _with_line_end_ranges(nodes: list[MarkdownNode], iwp_root: Path) -> list[MarkdownNode]:
    by_file: dict[str, list[MarkdownNode]] = defaultdict(list)
    for node in nodes:
        by_file[node.source_path].append(node)

    out: list[MarkdownNode] = []
    for source_path, file_nodes in by_file.items():
        file_nodes.sort(key=lambda n: n.line_start)
        file_line_count = len((iwp_root / source_path).read_text(encoding="utf-8").splitlines())
        for idx, node in enumerate(file_nodes):
            if idx < len(file_nodes) - 1:
                node.line_end = max(node.line_start, file_nodes[idx + 1].line_start - 1)
            else:
                node.line_end = max(node.line_start, file_line_count)
            out.append(node)
    return out


def _is_critical(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(pattern.lower() in low for pattern in patterns)


def _compute_kind(kind_rule_format: str, file_type_id: str, section_key: str) -> str:
    return kind_rule_format.format(file_type_id=file_type_id, section_key=section_key)


def _extract_text_marker(
    item_text: str, text_marker_enabled: bool, text_marker_token: str
) -> tuple[str, bool]:
    if not text_marker_enabled:
        return item_text, False
    if text_marker_token.strip() != "[text]":
        return item_text, False
    match = TEXT_MARKER_RE.match(item_text)
    if not match:
        return item_text, False
    normalized = match.group(1).strip()
    return (normalized or item_text), True


def _resolve_anchor_level(file_type_id: str, section_key: str, is_text: bool) -> str:
    if is_text:
        return "text"
    if not file_type_id.startswith("views."):
        return "default"
    if section_key == "interaction_hooks":
        return "interaction"
    if section_key in {"layout_tree", "layout", "display_rules"}:
        return "structure"
    return "structure"


def _finalize_short_node_ids(
    nodes: list[MarkdownNode],
    node_id_min_length: int,
) -> dict[str, str]:
    by_source: dict[str, list[MarkdownNode]] = defaultdict(list)
    for node in nodes:
        by_source[node.source_path].append(node)

    stable_keys_by_source: dict[str, list[str]] = {}
    reserved_ids_by_source: dict[str, set[str]] = {}
    for source_path, source_nodes in by_source.items():
        stable_keys: list[str] = []
        reserved_ids: set[str] = set()
        for node in source_nodes:
            if node.node_id.startswith("n."):
                reserved_ids.add(node.node_id)
                continue
            stable_keys.append(node.node_id)
        stable_keys_by_source[source_path] = stable_keys
        reserved_ids_by_source[source_path] = reserved_ids

    short_map = build_short_node_ids_by_source(
        stable_keys_by_source=stable_keys_by_source,
        min_length=node_id_min_length,
        reserved_ids_by_source=reserved_ids_by_source,
    )
    stable_to_short: dict[str, str] = {}
    for node in nodes:
        if node.node_id.startswith("n."):
            continue
        short_id = short_map.get((node.source_path, node.node_id))
        if short_id:
            stable_to_short[node.node_id] = short_id
            node.node_id = short_id
    return stable_to_short
