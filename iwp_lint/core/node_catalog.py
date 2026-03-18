from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import LintConfig, resolve_schema_source
from ..parsers.md_parser import parse_markdown_nodes
from ..parsers.node_registry import normalize_text
from ..schema.schema_loader import load_schema_profile
from ..schema.schema_validator import list_markdown_rel_paths


@dataclass(frozen=True)
class NodeCatalogEntry:
    source_path: str
    node_id: str
    anchor_text: str
    title_path: str
    section_key: str
    file_type_id: str
    computed_kind: str
    line_start: int
    line_end: int
    is_critical: bool
    source_line_start: int
    source_line_end: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NodeCatalogEntry:
        line_start = int(raw["line_start"])
        line_end = int(raw["line_end"])
        return cls(
            source_path=str(raw["source_path"]),
            node_id=str(raw["node_id"]),
            anchor_text=str(raw["anchor_text"]),
            title_path=str(raw["title_path"]),
            section_key=str(raw["section_key"]),
            file_type_id=str(raw["file_type_id"]),
            computed_kind=str(raw["computed_kind"]),
            line_start=line_start,
            line_end=line_end,
            is_critical=bool(raw.get("is_critical", False)),
            source_line_start=int(raw.get("source_line_start", line_start)),
            source_line_end=int(raw.get("source_line_end", line_end)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_node_catalog(config: LintConfig) -> dict[str, Any]:
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        config.iwp_root_path,
        config.critical_node_patterns,
        schema_path,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
    )
    entries = [
        NodeCatalogEntry(
            source_path=node.source_path,
            node_id=node.node_id,
            anchor_text=node.anchor_text,
            title_path=node.title_path,
            section_key=node.section_key,
            file_type_id=node.file_type_id,
            computed_kind=node.computed_kind,
            line_start=node.line_start,
            line_end=node.line_end,
            is_critical=node.is_critical,
            source_line_start=node.line_start,
            source_line_end=node.line_end,
        )
        for node in nodes
    ]
    catalog_path = _catalog_path(config)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": [entry.to_dict() for entry in entries],
    }
    catalog_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_node_index(config, entries)
    return {
        "catalog_path": catalog_path.as_posix(),
        "index_db_path": _node_index_path(config).as_posix(),
        "entry_count": len(entries),
    }


def query_node_catalog(
    config: LintConfig,
    source_path: str | None,
    text: str | None,
    line: int | None,
    limit: int,
    exact_text: bool = False,
) -> dict[str, Any]:
    entries = _load_entries_with_index_fallback(config)
    candidates = entries

    if source_path:
        candidates = [item for item in candidates if item.source_path == source_path]
    if line is not None:
        candidates = [item for item in candidates if item.line_start <= line <= item.line_end]

    text_norm = normalize_text(text) if text else None
    ranked = [{"entry": item, "score": _score(item, text_norm, exact_text)} for item in candidates]

    if text_norm is not None:
        ranked = [item for item in ranked if item["score"] > 0.0]

    ranked.sort(
        key=lambda item: (
            -float(item["score"]),
            item["entry"].source_path,
            item["entry"].line_start,
            item["entry"].node_id,
        )
    )

    top = ranked[: max(limit, 1)]
    return {
        "catalog_path": _catalog_path(config).as_posix(),
        "total_candidates": len(candidates),
        "returned": len(top),
        "results": [
            {
                **item["entry"].to_dict(),
                "score": round(float(item["score"]), 4),
            }
            for item in top
        ],
    }


def export_node_catalog(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    entries = _load_entries_with_index_fallback(config)
    normalized_sources = [item.strip() for item in (source_paths or []) if item and item.strip()]
    if normalized_sources:
        source_set = set(normalized_sources)
        entries = [item for item in entries if item.source_path in source_set]
    return {
        "catalog_path": _catalog_path(config).as_posix(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_filters": normalized_sources,
        "entry_count": len(entries),
        "entries": [entry.to_dict() for entry in entries],
    }


def compile_node_context(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    build_result = build_node_catalog(config)
    entries = _load_entries_with_index_fallback(config)
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
    schema_version = _schema_version(config)
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
            "version": 2,
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
        _remove_legacy_compiled_for_source(config, source_path)

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
        for stale_source in _find_legacy_compiled_sources_not_in_set(config, existing_sources):
            removed = _remove_legacy_compiled_for_source(config, stale_source)
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
        "node_catalog_path": build_result.get("catalog_path"),
        "index_db_path": build_result.get("index_db_path"),
    }


def verify_compiled_context(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    all_sources = list_markdown_rel_paths(
        config.iwp_root_path, config.schema_exclude_markdown_globs
    )
    normalized_sources = [item.strip() for item in (source_paths or []) if item and item.strip()]
    if normalized_sources:
        source_set = set(normalized_sources)
        all_sources = [source for source in all_sources if source in source_set]

    missing_files: list[str] = []
    stale_files: list[str] = []
    invalid_files: list[str] = []
    missing_json_files: list[str] = []
    missing_md_files: list[str] = []

    for source_path in all_sources:
        compiled_json_path = _compiled_json_file_path(config, source_path)
        compiled_md_path = _compiled_md_file_path(config, source_path)
        source_file = config.iwp_root_path / source_path
        source_hash = _source_hash(source_file.read_text(encoding="utf-8"))
        if not compiled_json_path.exists():
            missing_json_files.append(source_path)
            missing_files.append(source_path)
            continue
        if not compiled_md_path.exists():
            missing_md_files.append(source_path)
            missing_files.append(source_path)
            continue
        try:
            raw = json.loads(compiled_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            invalid_files.append(source_path)
            continue

        if str(raw.get("artifact", "")) != "iwc" or raw.get("version") != 2:
            invalid_files.append(source_path)
            continue

        payload_source_path = str(raw.get("source_path", ""))
        payload_source_hash = str(raw.get("source_hash", ""))
        if payload_source_path != source_path or payload_source_hash != source_hash:
            stale_files.append(source_path)
            continue

        dict_payload = raw.get("dict")
        nodes = raw.get("nodes")
        if not _is_valid_iwc_dict(dict_payload):
            invalid_files.append(source_path)
            continue
        if not isinstance(nodes, list) or not _is_valid_iwc_nodes(nodes, dict_payload):
            invalid_files.append(source_path)
            continue

        markdown_text = compiled_md_path.read_text(encoding="utf-8")
        markdown_meta = _parse_iwc_markdown_meta(markdown_text)
        if markdown_meta is None:
            invalid_files.append(source_path)
            continue
        if (
            markdown_meta["source_path"] != source_path
            or markdown_meta["source_hash"] != source_hash
        ):
            stale_files.append(source_path)
            continue
        if markdown_meta["schema_version"] != str(raw.get("schema_version", "")):
            invalid_files.append(source_path)
            continue
        if markdown_meta["entry_count"] != len(nodes):
            invalid_files.append(source_path)
            continue
        markdown_node_ids = _parse_iwc_markdown_node_ids(markdown_text)
        json_node_ids = [str(item[0]) for item in nodes]
        if markdown_node_ids != json_node_ids:
            invalid_files.append(source_path)
            continue

    ok = not missing_files and not stale_files and not invalid_files
    return {
        "compiled_dir": _compiled_root(config).as_posix(),
        "compiled_json_dir": _compiled_json_root(config).as_posix(),
        "compiled_md_dir": _compiled_md_root(config).as_posix(),
        "source_filters": normalized_sources,
        "checked_sources": len(all_sources),
        "ok": ok,
        "missing_files": sorted(set(missing_files)),
        "missing_json_files": sorted(set(missing_json_files)),
        "missing_md_files": sorted(set(missing_md_files)),
        "stale_files": sorted(set(stale_files)),
        "invalid_files": sorted(set(invalid_files)),
    }


def _score(entry: NodeCatalogEntry, text_norm: str | None, exact_text: bool) -> float:
    if text_norm is None:
        return 1.0
    anchor_norm = normalize_text(entry.anchor_text)
    title_norm = normalize_text(entry.title_path.replace(".", " "))
    if exact_text:
        return 1.0 if anchor_norm == text_norm else 0.0
    anchor_score = SequenceMatcher(None, text_norm, anchor_norm).ratio() if anchor_norm else 0.0
    title_score = SequenceMatcher(None, text_norm, title_norm).ratio() if title_norm else 0.0
    return (0.85 * anchor_score) + (0.15 * title_score)


def _load_entries(catalog_path: Path) -> list[NodeCatalogEntry]:
    if not catalog_path.exists():
        raise RuntimeError(
            f"node catalog not found: {catalog_path}. Run `iwp-lint nodes build` first."
        )
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"node catalog is not valid JSON: {catalog_path}") from exc
    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        raise RuntimeError(f"node catalog has invalid entries payload: {catalog_path}")
    return [NodeCatalogEntry.from_dict(item) for item in entries_raw if isinstance(item, dict)]


def _catalog_path(config: LintConfig) -> Path:
    return (config.project_root / config.node_catalog_file).resolve()


def _node_index_path(config: LintConfig) -> Path:
    return (config.project_root / config.node_index_db_file).resolve()


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


def _legacy_compiled_file_path(config: LintConfig, source_path: str) -> Path:
    rel = Path(source_path + ".iwc.json")
    return (_compiled_root(config) / rel).resolve()


def _remove_legacy_compiled_for_source(config: LintConfig, source_path: str) -> str | None:
    path = _legacy_compiled_file_path(config, source_path)
    if not path.exists():
        return None
    path.unlink()
    _cleanup_empty_parents(path.parent, _compiled_root(config))
    return source_path


def _find_legacy_compiled_sources_not_in_set(
    config: LintConfig, expected_sources: set[str]
) -> list[str]:
    compiled_root = _compiled_root(config)
    if not compiled_root.exists():
        return []
    stale: list[str] = []
    for path in compiled_root.rglob("*.iwc.json"):
        rel_path = path.relative_to(compiled_root)
        if rel_path.parts and rel_path.parts[0] == "json":
            continue
        source_path = rel_path.as_posix()[: -len(".iwc.json")]
        if source_path not in expected_sources:
            stale.append(source_path)
    return sorted(stale)


def _render_iwc_markdown(
    source_entries: list[NodeCatalogEntry],
    source_lines: list[str],
    source_path: str,
    source_hash: str,
    schema_version: str,
    generated_at: str,
) -> str:
    lines = [
        "<!-- @iwp.meta artifact=iwc_md -->",
        "<!-- @iwp.meta version=1 -->",
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


_IWC_MD_META_PATTERN = re.compile(r"^<!--\s*@iwp\.meta\s+([a-z_]+)=([^\s].*?)\s*-->$")
_IWC_MD_NODE_PATTERN = re.compile(r"^<!--\s*@iwp\.node\s+id=(n\.[a-z0-9]+)\s*-->$")


def _parse_iwc_markdown_meta(text: str) -> dict[str, Any] | None:
    required = {
        "artifact",
        "version",
        "source_path",
        "source_hash",
        "schema_version",
        "generated_at",
        "entry_count",
    }
    meta: dict[str, str] = {}
    for line in text.splitlines():
        match = _IWC_MD_META_PATTERN.match(line.strip())
        if match:
            meta[match.group(1)] = match.group(2)
    if required - set(meta.keys()):
        return None
    if meta.get("artifact") != "iwc_md":
        return None
    if meta.get("version") != "1":
        return None
    try:
        entry_count = int(meta["entry_count"])
    except ValueError:
        return None
    return {
        "source_path": meta["source_path"],
        "source_hash": meta["source_hash"],
        "schema_version": meta["schema_version"],
        "generated_at": meta["generated_at"],
        "entry_count": entry_count,
    }


def _parse_iwc_markdown_node_ids(text: str) -> list[str]:
    ids: list[str] = []
    for line in text.splitlines():
        match = _IWC_MD_NODE_PATTERN.match(line.strip())
        if match:
            ids.append(match.group(1))
    return ids


def _write_node_index(config: LintConfig, entries: list[NodeCatalogEntry]) -> None:
    db_path = _node_index_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.execute("DROP TABLE IF EXISTS node_index")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS node_index (
                    source_path TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    anchor_text TEXT NOT NULL,
                    title_path TEXT NOT NULL,
                    section_key TEXT NOT NULL,
                    file_type_id TEXT NOT NULL,
                    computed_kind TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    is_critical INTEGER NOT NULL,
                    source_line_start INTEGER NOT NULL,
                    source_line_end INTEGER NOT NULL,
                    PRIMARY KEY (source_path, node_id)
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO node_index(
                    source_path, node_id, anchor_text, title_path, section_key, file_type_id,
                    computed_kind, line_start, line_end, is_critical,
                    source_line_start, source_line_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.source_path,
                        item.node_id,
                        item.anchor_text,
                        item.title_path,
                        item.section_key,
                        item.file_type_id,
                        item.computed_kind,
                        item.line_start,
                        item.line_end,
                        1 if item.is_critical else 0,
                        item.source_line_start,
                        item.source_line_end,
                    )
                    for item in entries
                ],
            )


def _load_entries_from_index(db_path: Path) -> list[NodeCatalogEntry]:
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT source_path, node_id, anchor_text, title_path, section_key, file_type_id,
                   computed_kind, line_start, line_end, is_critical,
                   source_line_start, source_line_end
            FROM node_index
            ORDER BY source_path, line_start, node_id
            """
        ).fetchall()
    return [
        NodeCatalogEntry(
            source_path=str(source_path),
            node_id=str(node_id),
            anchor_text=str(anchor_text),
            title_path=str(title_path),
            section_key=str(section_key),
            file_type_id=str(file_type_id),
            computed_kind=str(computed_kind),
            line_start=int(line_start),
            line_end=int(line_end),
            is_critical=bool(int(is_critical)),
            source_line_start=int(source_line_start),
            source_line_end=int(source_line_end),
        )
        for (
            source_path,
            node_id,
            anchor_text,
            title_path,
            section_key,
            file_type_id,
            computed_kind,
            line_start,
            line_end,
            is_critical,
            source_line_start,
            source_line_end,
        ) in rows
    ]


def _load_entries_with_index_fallback(config: LintConfig) -> list[NodeCatalogEntry]:
    indexed = _load_entries_from_index(_node_index_path(config))
    if indexed:
        return indexed
    return _load_entries(_catalog_path(config))


def _source_hash(content: str) -> str:
    digest = sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _line_block(lines: list[str], start_line: int, end_line: int) -> str:
    if not lines:
        return ""
    start = max(start_line, 1) - 1
    end = min(max(end_line, start_line), len(lines))
    if end <= start:
        return ""
    return "\n".join(lines[start:end])


def _schema_version(config: LintConfig) -> str:
    schema_path = resolve_schema_source(config)
    profile = load_schema_profile(schema_path)
    return profile.schema_version


def _build_dict(
    items: list[NodeCatalogEntry],
    selector: Callable[[NodeCatalogEntry], str],
) -> tuple[list[str], dict[str, int]]:
    dictionary: list[str] = []
    indexes: dict[str, int] = {}
    for item in items:
        value = selector(item)
        if value in indexes:
            continue
        indexes[value] = len(dictionary)
        dictionary.append(value)
    return dictionary, indexes


def _is_valid_iwc_dict(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    for key in ("kinds", "titles", "sections", "file_types"):
        items = raw.get(key)
        if not isinstance(items, list):
            return False
        if any(not isinstance(item, str) for item in items):
            return False
    return True


def _is_valid_iwc_nodes(nodes: list[Any], raw_dict: dict[str, Any]) -> bool:
    kinds = raw_dict.get("kinds", [])
    titles = raw_dict.get("titles", [])
    sections = raw_dict.get("sections", [])
    file_types = raw_dict.get("file_types", [])
    if not all(isinstance(pool, list) for pool in (kinds, titles, sections, file_types)):
        return False

    for item in nodes:
        if not isinstance(item, list):
            return False
        if len(item) != 10:
            return False
        if not isinstance(item[0], str) or not item[0]:
            return False
        if not isinstance(item[1], str):
            return False
        if not isinstance(item[2], int) or item[2] < 0 or item[2] >= len(kinds):
            return False
        if not isinstance(item[3], int) or item[3] < 0 or item[3] >= len(titles):
            return False
        if not isinstance(item[4], int) or item[4] < 0 or item[4] >= len(sections):
            return False
        if not isinstance(item[5], int) or item[5] < 0 or item[5] >= len(file_types):
            return False
        if item[6] not in (0, 1):
            return False
        if not isinstance(item[7], int) or item[7] < 1:
            return False
        if not isinstance(item[8], int) or item[8] < item[7]:
            return False
        if not isinstance(item[9], str):
            return False
    return True
