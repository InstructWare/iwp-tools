from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .catalog_types import NodeCatalogEntry


def write_node_index(index_db_path: Path, entries: list[NodeCatalogEntry]) -> None:
    index_db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(index_db_path)) as conn:
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
                    anchor_level TEXT NOT NULL,
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
                    computed_kind, anchor_level, line_start, line_end, is_critical,
                    source_line_start, source_line_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        item.anchor_level,
                        item.line_start,
                        item.line_end,
                        1 if item.is_critical else 0,
                        item.source_line_start,
                        item.source_line_end,
                    )
                    for item in entries
                ],
            )


def load_entries_with_index_fallback(
    *,
    index_db_path: Path,
    catalog_path: Path,
) -> list[NodeCatalogEntry]:
    entries = _load_entries_from_index(index_db_path)
    if entries:
        return entries
    return _load_entries_from_catalog(catalog_path)


def _load_entries_from_catalog(catalog_path: Path) -> list[NodeCatalogEntry]:
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


def _load_entries_from_index(index_db_path: Path) -> list[NodeCatalogEntry]:
    if not index_db_path.exists():
        return []
    with closing(sqlite3.connect(index_db_path)) as conn:
        try:
            rows = conn.execute(
                """
                SELECT
                    source_path, node_id, anchor_text, title_path, section_key,
                    file_type_id, computed_kind, anchor_level, line_start, line_end,
                    is_critical, source_line_start, source_line_end
                FROM node_index
                ORDER BY source_path, line_start, node_id
                """
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
    return [
        NodeCatalogEntry(
            source_path=str(item[0]),
            node_id=str(item[1]),
            anchor_text=str(item[2]),
            title_path=str(item[3]),
            section_key=str(item[4]),
            file_type_id=str(item[5]),
            computed_kind=str(item[6]),
            anchor_level=str(item[7]),
            line_start=int(item[8]),
            line_end=int(item[9]),
            is_critical=bool(item[10]),
            source_line_start=int(item[11]),
            source_line_end=int(item[12]),
        )
        for item in rows
    ]
