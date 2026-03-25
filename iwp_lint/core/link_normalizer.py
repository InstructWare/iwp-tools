from __future__ import annotations

import re
from typing import Any

from ..config import LintConfig, resolve_schema_source
from ..parsers.comment_scanner import LINK_RE, discover_code_files
from ..parsers.md_parser import parse_markdown_nodes

LINK_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<prefix>//|#|<!--)\s*@iwp\.link\b(?P<body>.*?)(?P<suffix>\s*-->)?\s*$"
)


def normalize_links(config: LintConfig, write: bool) -> dict[str, Any]:
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        config.iwp_root_path,
        config.critical_node_patterns,
        schema_path,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
        node_id_min_length=config.node_id_min_length,
        page_only_enabled=config.page_only.enabled,
        authoring_tokens_enabled=config.authoring.tokens.enabled,
        node_generation_mode=config.authoring.node_generation_mode,
    )
    node_keys = {(node.source_path, node.node_id) for node in nodes}

    files = discover_code_files(
        config.project_root,
        config.code_roots,
        config.include_ext,
        config.code_exclude_globs,
    )
    changed_files: list[str] = []
    removed_stale = 0
    removed_duplicate = 0
    multi_line_blocks_seen = 0

    for file_path in files:
        rel = file_path.relative_to(config.project_root).as_posix()
        original_lines = file_path.read_text(encoding="utf-8").splitlines()
        rewritten, changed, stale_count, duplicate_count, merged_count = _normalize_file_lines(
            original_lines, node_keys
        )
        removed_stale += stale_count
        removed_duplicate += duplicate_count
        multi_line_blocks_seen += merged_count
        if not changed:
            continue
        changed_files.append(rel)
        if write:
            text = "\n".join(rewritten)
            if original_lines:
                text += "\n"
            file_path.write_text(text, encoding="utf-8")

    return {
        "mode": "write" if write else "check",
        "checked_files": len(files),
        "changed_files": sorted(changed_files),
        "changed_count": len(changed_files),
        "removed_stale_links": removed_stale,
        "removed_duplicate_links": removed_duplicate,
        "multi_line_blocks_seen": multi_line_blocks_seen,
    }


def _normalize_file_lines(
    lines: list[str], node_keys: set[tuple[str, str]]
) -> tuple[list[str], bool, int, int, int]:
    output: list[str] = []
    idx = 0
    changed = False
    stale_count = 0
    duplicate_count = 0
    merged_count = 0

    while idx < len(lines):
        line = lines[idx]
        if not _is_link_only_line(line):
            output.append(line)
            idx += 1
            continue

        block_start = idx
        block_lines: list[str] = []
        while idx < len(lines) and _is_link_only_line(lines[idx]):
            block_lines.append(lines[idx])
            idx += 1
        normalized_lines, block_stale, block_duplicate = _normalize_link_block(
            block_lines, node_keys
        )
        stale_count += block_stale
        duplicate_count += block_duplicate
        if len(block_lines) > 1:
            merged_count += 1
        if normalized_lines != block_lines:
            changed = True
        if not normalized_lines:
            # Entire block removed.
            continue
        output.extend(normalized_lines)
        if idx - block_start > 1 and normalized_lines != block_lines:
            changed = True
    return output, changed, stale_count, duplicate_count, merged_count


def _normalize_link_block(
    block_lines: list[str], node_keys: set[tuple[str, str]]
) -> tuple[list[str], int, int]:
    extracted: list[tuple[str, str, str, str, str]] = []
    stale_count = 0
    duplicate_count = 0
    for line in block_lines:
        match = LINK_LINE_RE.match(line)
        if not match:
            continue
        indent = match.group("indent")
        prefix = match.group("prefix")
        suffix = match.group("suffix") or ""
        found = LINK_RE.findall(line)
        if not found:
            continue
        source_path, node_id = found[0]
        if (source_path, node_id) not in node_keys:
            stale_count += 1
            continue
        extracted.append((source_path, node_id, indent, prefix, suffix))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str, str, str]] = []
    for item in extracted:
        key = (item[0], item[1])
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        unique.append(item)

    unique.sort(key=lambda item: (item[0], item[1]))
    if not unique:
        return [], stale_count, duplicate_count

    indent = unique[0][2]
    prefix = unique[0][3]
    suffix = unique[0][4]
    out_lines = [
        f"{indent}{prefix} @iwp.link {source_path}::{node_id}{suffix}".rstrip()
        for source_path, node_id, _, _, _ in unique
    ]
    return out_lines, stale_count, duplicate_count


def _is_link_only_line(line: str) -> bool:
    match = LINK_LINE_RE.match(line)
    if not match:
        return False
    return bool(LINK_RE.search(line))
