from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..core.models import MarkdownNode
from ..schema.schema_loader import load_schema_profile
from ..schema.schema_semantics import match_file_type, resolve_section_keys
from ..schema.schema_validator import list_markdown_rel_paths
from .node_registry import NodeRegistry, build_signature

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
NODE_OVERRIDE_RE = re.compile(r"<!--\s*@iwp\.node\s+([a-z0-9][a-z0-9._-]{2,127})\s*-->")
DEFAULT_NODE_REGISTRY_FILE = ".iwp/node_registry.v1.json"


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
    exclude_markdown_globs: list[str] | None = None,
    node_registry_file: str = DEFAULT_NODE_REGISTRY_FILE,
) -> list[MarkdownNode]:
    profile = load_schema_profile(schema_path)
    registry = NodeRegistry((iwp_root.parent / node_registry_file).resolve())
    nodes: list[MarkdownNode] = []
    for rel in list_markdown_rel_paths(iwp_root, exclude_markdown_globs):
        path = iwp_root / rel
        file_type = match_file_type(rel, profile.file_type_schemas)
        file_type_id = file_type.id if file_type else "unknown"
        allowed_section_keys = {item.key for item in file_type.sections} if file_type else set()
        nodes.extend(
            _parse_one_file(
                path,
                iwp_root,
                critical_patterns,
                section_i18n=profile.section_i18n,
                file_type_id=file_type_id,
                allowed_section_keys=allowed_section_keys,
                kind_rule_format=profile.kind_rule_format,
                registry=registry,
            )
        )
    nodes = _with_line_end_ranges(nodes, iwp_root)
    registry.flush()
    return nodes


def _parse_one_file(
    path: Path,
    iwp_root: Path,
    critical_patterns: list[str],
    section_i18n: dict[str, dict[str, list[str]]],
    file_type_id: str,
    allowed_section_keys: set[str],
    kind_rule_format: str,
    registry: NodeRegistry,
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
                    is_critical=is_critical,
                )
            )
            continue

        list_match = LIST_ITEM_RE.match(line)
        if list_match and heading_stack:
            item_text = list_match.group(1).strip()
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
            is_critical = _is_critical(item_text, critical_patterns) or _is_critical(
                heading_stack[-1][1], critical_patterns
            )
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
