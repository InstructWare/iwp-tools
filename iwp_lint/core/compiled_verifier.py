from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import LintConfig, resolve_schema_source
from ..parsers.md_parser import parse_markdown_nodes
from ..schema.schema_validator import list_markdown_rel_paths
from ..versioning import IWC_MD_META_VERSION, SUPPORTED_IWC_JSON_VERSIONS

_IWC_MD_META_PATTERN = re.compile(r"^<!--\s*@iwp\.meta\s+([a-z_]+)=([^\s].*?)\s*-->$")
_IWC_MD_NODE_PATTERN = re.compile(r"^<!--\s*@iwp\.node\s+id=(n\.[a-z0-9]+)\s*-->$")


def verify_compiled_context(
    config: LintConfig,
    source_paths: list[str] | None = None,
) -> dict[str, object]:
    all_sources = _expected_compiled_sources(config)
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

        if (
            str(raw.get("artifact", "")) != "iwc"
            or raw.get("version") not in SUPPORTED_IWC_JSON_VERSIONS
        ):
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


def _expected_compiled_sources(config: LintConfig) -> list[str]:
    node_generation_mode = str(getattr(config.authoring, "node_generation_mode", "structural")).strip()
    if node_generation_mode == "annotated_only":
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            config.iwp_root_path,
            config.critical_node_patterns,
            schema_path,
            critical_granularity=config.critical_granularity,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            node_id_min_length=config.node_id_min_length,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode=config.authoring.node_generation_mode,
        )
        return sorted({node.source_path for node in nodes})
    return list_markdown_rel_paths(config.iwp_root_path, config.schema_exclude_markdown_globs)


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


def _source_hash(content: str) -> str:
    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


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
    if meta.get("version") != str(IWC_MD_META_VERSION):
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


def _is_valid_iwc_dict(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    for key in ("kinds", "titles", "sections", "file_types"):
        value = raw.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
    return True


def _is_valid_iwc_nodes(nodes: list[Any], raw_dict: dict[str, Any]) -> bool:
    limit_kind = len(raw_dict.get("kinds", []))
    limit_title = len(raw_dict.get("titles", []))
    limit_section = len(raw_dict.get("sections", []))
    limit_file_type = len(raw_dict.get("file_types", []))
    for item in nodes:
        if not isinstance(item, list) or len(item) != 10:
            return False
        node_id, anchor_text = item[0], item[1]
        if not isinstance(node_id, str) or not node_id.startswith("n."):
            return False
        if not isinstance(anchor_text, str):
            return False
        for idx, max_value in (
            (2, limit_kind),
            (3, limit_title),
            (4, limit_section),
            (5, limit_file_type),
        ):
            value = item[idx]
            if not isinstance(value, int) or value < 0 or value >= max_value:
                return False
        if item[6] not in (0, 1):
            return False
        if not isinstance(item[7], int) or not isinstance(item[8], int):
            return False
        if not isinstance(item[9], str):
            return False
    return True
