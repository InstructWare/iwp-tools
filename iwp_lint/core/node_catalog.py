from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import LintConfig, resolve_schema_source
from ..parsers.md_parser import parse_markdown_nodes
from ..schema.schema_loader import load_schema_profile
from ..versioning import NODE_CATALOG_FORMAT_VERSION
from .catalog_query import export_node_catalog as _export_node_catalog
from .catalog_query import query_node_catalog as _query_node_catalog
from .catalog_types import NodeCatalogEntry
from .code_sidecar_writer import verify_code_sidecar_freshness, write_code_sidecar
from .compiled_verifier import verify_compiled_context as _verify_compiled_context
from .compiled_writer import write_compiled_context
from .node_index_store import load_entries_with_index_fallback, write_node_index


def build_node_catalog(config: LintConfig) -> dict[str, Any]:
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        config.iwp_root_path,
        config.critical_node_patterns,
        schema_path,
        critical_granularity=config.critical_granularity,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
        node_id_min_length=config.node_id_min_length,
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
            anchor_level=node.anchor_level,
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
        "version": NODE_CATALOG_FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": [entry.to_dict() for entry in entries],
    }
    catalog_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_node_index(_node_index_path(config), entries)
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
) -> dict[str, object]:
    return _query_node_catalog(
        config=config,
        source_path=source_path,
        text=text,
        line=line,
        limit=limit,
        exact_text=exact_text,
    )


def export_node_catalog(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, object]:
    return _export_node_catalog(config=config, source_paths=source_paths)


def compile_node_context(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, object]:
    build_result = build_node_catalog(config)
    entries = load_entries_with_index_fallback(
        index_db_path=_node_index_path(config),
        catalog_path=_catalog_path(config),
    )
    compiled = write_compiled_context(
        config=config,
        entries=entries,
        source_paths=source_paths,
        schema_version=_schema_version(config),
    )
    compiled["node_catalog_path"] = build_result.get("catalog_path")
    compiled["index_db_path"] = build_result.get("index_db_path")
    return compiled


def verify_compiled_context(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, object]:
    return _verify_compiled_context(config=config, source_paths=source_paths)


def build_code_sidecar_context(
    config: LintConfig,
    *,
    compiled_from_baseline_id: int | None = None,
) -> dict[str, object]:
    build_result = build_node_catalog(config)
    entries = load_entries_with_index_fallback(
        index_db_path=_node_index_path(config),
        catalog_path=_catalog_path(config),
    )
    sidecar = write_code_sidecar(
        config=config,
        entries=entries,
        compiled_from_baseline_id=compiled_from_baseline_id,
    )
    sidecar["node_catalog_path"] = build_result.get("catalog_path")
    sidecar["index_db_path"] = build_result.get("index_db_path")
    return sidecar


def verify_code_sidecar_freshness_context(config: LintConfig) -> dict[str, object]:
    return verify_code_sidecar_freshness(config=config)


def _catalog_path(config: LintConfig) -> Path:
    return (config.project_root / config.node_catalog_file).resolve()


def _node_index_path(config: LintConfig) -> Path:
    return (config.project_root / config.node_index_db_file).resolve()


def _schema_version(config: LintConfig) -> str:
    schema_path = resolve_schema_source(config)
    profile = load_schema_profile(schema_path)
    return profile.schema_version
