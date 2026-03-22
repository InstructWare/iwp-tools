from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from ..config import LintConfig
from ..parsers.node_registry import normalize_text
from .catalog_types import NodeCatalogEntry
from .node_index_store import load_entries_with_index_fallback


def query_node_catalog(
    config: LintConfig,
    source_path: str | None,
    text: str | None,
    line: int | None,
    limit: int,
    exact_text: bool = False,
) -> dict[str, object]:
    catalog_path = (config.project_root / config.node_catalog_file).resolve()
    index_db_path = (config.project_root / config.node_index_db_file).resolve()
    entries = load_entries_with_index_fallback(
        index_db_path=index_db_path, catalog_path=catalog_path
    )
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
        "catalog_path": catalog_path.as_posix(),
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
    config: LintConfig, source_paths: list[str] | None = None
) -> dict[str, object]:
    catalog_path = (config.project_root / config.node_catalog_file).resolve()
    index_db_path = (config.project_root / config.node_index_db_file).resolve()
    entries = load_entries_with_index_fallback(
        index_db_path=index_db_path, catalog_path=catalog_path
    )
    normalized_sources = [item.strip() for item in (source_paths or []) if item and item.strip()]
    if normalized_sources:
        source_set = set(normalized_sources)
        entries = [item for item in entries if item.source_path in source_set]
    return {
        "catalog_path": catalog_path.as_posix(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_filters": normalized_sources,
        "entry_count": len(entries),
        "entries": [entry.to_dict() for entry in entries],
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
