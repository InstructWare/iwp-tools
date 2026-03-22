from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import LintConfig
from ..versioning import IWC_JSON_FORMAT_VERSION, IWC_MD_META_VERSION
from .catalog_types import NodeCatalogEntry


def write_compiled_context(
    *,
    config: LintConfig,
    entries: list[NodeCatalogEntry],
    source_paths: list[str] | None,
    schema_version: str,
) -> dict[str, object]:
    normalized_sources = [item.strip() for item in (source_paths or []) if item and item.strip()]
    if normalized_sources:
        source_set = set(normalized_sources)
        entries = [item for item in entries if item.source_path in source_set]

    by_source: dict[str, list[NodeCatalogEntry]] = {}
    for entry in entries:
        by_source.setdefault(entry.source_path, []).append(entry)
    for source in by_source:
        by_source[source].sort(key=lambda item: (item.line_start, item.node_id))

    compiled_root = _compiled_root(config)
    compiled_json_root = _compiled_json_root(config)
    compiled_md_root = _compiled_md_root(config)
    compiled_json_root.mkdir(parents=True, exist_ok=True)
    compiled_md_root.mkdir(parents=True, exist_ok=True)

    compiled_json_files: list[str] = []
    compiled_md_files: list[str] = []
    for source_path, source_entries in by_source.items():
        source_file = config.iwp_root_path / source_path
        source_text = source_file.read_text(encoding="utf-8")
        source_hash = _source_hash(source_text)
        source_lines = source_text.splitlines()

        kind_dict, kind_idx = _build_dict(source_entries, lambda item: item.computed_kind)
        title_dict, title_idx = _build_dict(source_entries, lambda item: item.title_path)
        section_dict, section_idx = _build_dict(source_entries, lambda item: item.section_key)
        file_type_dict, file_type_idx = _build_dict(source_entries, lambda item: item.file_type_id)

        nodes_payload: list[list[Any]] = []
        for entry in source_entries:
            line_start = entry.source_line_start
            line_end = entry.source_line_end
            nodes_payload.append(
                [
                    entry.node_id,
                    entry.anchor_text,
                    kind_idx[entry.computed_kind],
                    title_idx[entry.title_path],
                    section_idx[entry.section_key],
                    file_type_idx[entry.file_type_id],
                    1 if entry.is_critical else 0,
                    line_start,
                    line_end,
                    _line_block(source_lines, line_start, line_end),
                ]
            )

        payload = {
            "artifact": "iwc",
            "version": IWC_JSON_FORMAT_VERSION,
            "schema_version": schema_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_path": source_path,
            "source_hash": source_hash,
            "dict": {
                "kinds": kind_dict,
                "titles": title_dict,
                "sections": section_dict,
                "file_types": file_type_dict,
            },
            "node_columns": [
                "node_id",
                "anchor_text",
                "kind_idx",
                "title_idx",
                "section_idx",
                "file_type_idx",
                "is_critical",
                "source_line_start",
                "source_line_end",
                "block_text",
            ],
            "entry_count": len(nodes_payload),
            "nodes": nodes_payload,
        }

        json_output_path = _compiled_json_file_path(config, source_path)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        compiled_json_files.append(json_output_path.as_posix())

        md_output_path = _compiled_md_file_path(config, source_path)
        md_output_path.parent.mkdir(parents=True, exist_ok=True)
        md_output_path.write_text(
            _render_iwc_markdown(
                source_entries=source_entries,
                source_lines=source_lines,
                source_path=source_path,
                source_hash=source_hash,
                schema_version=schema_version,
                generated_at=str(payload["generated_at"]),
            ),
            encoding="utf-8",
        )
        compiled_md_files.append(md_output_path.as_posix())

    removed_files: list[str] = []
    existing_sources = set(by_source.keys())
    if normalized_sources:
        for source_path in normalized_sources:
            if source_path in existing_sources:
                continue
            removed = _remove_compiled_for_source(config, source_path)
            if removed is not None:
                removed_files.append(removed)
    else:
        stale_files = _find_compiled_sources_not_in_set(config, existing_sources)
        for stale_source in stale_files:
            removed = _remove_compiled_for_source(config, stale_source)
            if removed is not None:
                removed_files.append(removed)

    return {
        "compiled_dir": compiled_root.as_posix(),
        "compiled_json_dir": compiled_json_root.as_posix(),
        "compiled_md_dir": compiled_md_root.as_posix(),
        "source_filters": normalized_sources,
        "compiled_count": len(compiled_json_files),
        "compiled_json_files": sorted(compiled_json_files),
        "compiled_md_files": sorted(compiled_md_files),
        "removed_count": len(removed_files),
        "removed_files": sorted(removed_files),
    }


def _compiled_root(config: LintConfig) -> Path:
    return (config.project_root / config.compiled_dir).resolve()


def _compiled_json_root(config: LintConfig) -> Path:
    return (_compiled_root(config) / "json").resolve()


def _compiled_md_root(config: LintConfig) -> Path:
    return (_compiled_root(config) / "md").resolve()


def _compiled_json_file_path(config: LintConfig, source_path: str) -> Path:
    rel = Path(source_path + ".iwc.json")
    return (_compiled_json_root(config) / rel).resolve()


def _compiled_md_file_path(config: LintConfig, source_path: str) -> Path:
    rel = Path(source_path + ".iwc.md")
    return (_compiled_md_root(config) / rel).resolve()


def _remove_compiled_for_source(config: LintConfig, source_path: str) -> str | None:
    json_path = _compiled_json_file_path(config, source_path)
    md_path = _compiled_md_file_path(config, source_path)
    removed = False
    if json_path.exists():
        json_path.unlink()
        _cleanup_empty_parents(json_path.parent, _compiled_json_root(config))
        removed = True
    if md_path.exists():
        md_path.unlink()
        _cleanup_empty_parents(md_path.parent, _compiled_md_root(config))
        removed = True
    if not removed:
        return None
    return source_path


def _cleanup_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.exists():
        if any(current.iterdir()):
            return
        current.rmdir()
        current = current.parent


def _find_compiled_sources_not_in_set(config: LintConfig, expected_sources: set[str]) -> list[str]:
    json_root = _compiled_json_root(config)
    md_root = _compiled_md_root(config)
    if not json_root.exists() and not md_root.exists():
        return []
    stale: set[str] = set()
    for path in json_root.rglob("*.iwc.json"):
        rel = path.relative_to(json_root).as_posix()
        source_path = rel[: -len(".iwc.json")]
        if source_path not in expected_sources:
            stale.add(source_path)
    for path in md_root.rglob("*.iwc.md"):
        rel = path.relative_to(md_root).as_posix()
        source_path = rel[: -len(".iwc.md")]
        if source_path not in expected_sources:
            stale.add(source_path)
    return sorted(stale)


def _render_iwc_markdown(
    *,
    source_entries: list[NodeCatalogEntry],
    source_lines: list[str],
    source_path: str,
    source_hash: str,
    schema_version: str,
    generated_at: str,
) -> str:
    lines = [
        "<!-- @iwp.meta artifact=iwc_md -->",
        f"<!-- @iwp.meta version={IWC_MD_META_VERSION} -->",
        f"<!-- @iwp.meta source_path={source_path} -->",
        f"<!-- @iwp.meta source_hash={source_hash} -->",
        f"<!-- @iwp.meta schema_version={schema_version} -->",
        f"<!-- @iwp.meta generated_at={generated_at} -->",
        f"<!-- @iwp.meta entry_count={len(source_entries)} -->",
        "",
    ]
    for idx, entry in enumerate(source_entries):
        block_text = _line_block(source_lines, entry.source_line_start, entry.source_line_end)
        if block_text:
            lines.append(block_text)
        lines.append(f"<!-- @iwp.node id={entry.node_id} -->")
        if idx != len(source_entries) - 1:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _source_hash(content: str) -> str:
    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


def _line_block(lines: list[str], start_line: int, end_line: int) -> str:
    start_idx = max(start_line - 1, 0)
    end_idx = max(end_line, start_idx + 1)
    if start_idx >= len(lines):
        return ""
    return "\n".join(lines[start_idx:end_idx]).strip("\n")


def _build_dict(
    source_entries: list[NodeCatalogEntry],
    key_fn,
) -> tuple[list[str], dict[str, int]]:
    values: list[str] = []
    index: dict[str, int] = {}
    for entry in source_entries:
        value = str(key_fn(entry))
        if value not in index:
            index[value] = len(values)
            values.append(value)
    return values, index
