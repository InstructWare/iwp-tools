from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import LintConfig
from ..parsers.comment_scanner import discover_code_files, scan_links
from .catalog_types import NodeCatalogEntry

_PURE_LINK_LINE_RE = re.compile(
    r"^\s*(?:(?://|#|/\*+|\*|<!--)\s*)?@iwp\.link\s+[^:\s]+\.md::[^:\s]+(?:\s*(?:\*/|-->)\s*)?$"
)


def write_code_sidecar(
    *,
    config: LintConfig,
    entries: list[NodeCatalogEntry],
    compiled_from_baseline_id: int | None = None,
) -> dict[str, object]:
    sidecar_cfg = config.code_sidecar
    sidecar_root = _code_sidecar_root(config)
    if sidecar_root.exists():
        shutil.rmtree(sidecar_root)
    sidecar_root.mkdir(parents=True, exist_ok=True)

    code_files = discover_code_files(
        config.project_root,
        config.code_roots,
        config.include_ext,
        config.code_exclude_globs,
    )
    links, scan_diagnostics = scan_links(
        config.project_root,
        code_files,
        config.allow_multi_link_per_symbol,
    )

    entry_index = {(item.source_path, item.node_id): item for item in entries}
    source_cache: dict[str, list[str]] = {}
    links_by_code: dict[str, list[Any]] = {}
    for link in sorted(links, key=lambda item: (item.file_path, item.line, item.column, item.node_id)):
        links_by_code.setdefault(link.file_path, []).append(link)

    files_written: list[str] = []
    links_found = 0
    resolved = 0
    unresolved = 0
    diagnostics: list[dict[str, object]] = []
    for file_path in code_files:
        rel_code = file_path.relative_to(config.project_root).as_posix()
        target_path = (sidecar_root / rel_code).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        file_links = links_by_code.get(rel_code, [])
        links_found += len(file_links)
        if not file_links:
            target_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
            files_written.append(target_path.as_posix())
            continue

        source_lines = file_path.read_text(encoding="utf-8").splitlines()
        links_by_line: dict[int, list[Any]] = {}
        for link in file_links:
            links_by_line.setdefault(int(link.line), []).append(link)

        rewritten: list[str] = []
        for line_no, line in enumerate(source_lines, start=1):
            line_links = sorted(
                links_by_line.get(line_no, []),
                key=lambda item: (item.column, item.node_id),
            )
            if not line_links:
                rewritten.append(line)
                continue

            resolved_blocks: list[tuple[Any, list[str]]] = []
            for link in line_links:
                entry = entry_index.get((link.source_path, link.node_id))
                if entry is None:
                    unresolved += 1
                    _append_diagnostic(
                        diagnostics,
                        {
                            "code": "IWP305",
                            "message": (
                                "node reference not found while building code sidecar: "
                                f"{link.source_path}::{link.node_id}"
                            ),
                            "file_path": link.file_path,
                            "line": link.line,
                            "column": link.column,
                        },
                    )
                    continue
                resolved += 1
                resolved_blocks.append(
                    (
                        link,
                        _render_context_block(
                            config=config,
                            entry=entry,
                            source_lines=_load_source_lines(config, source_cache, entry.source_path),
                        ),
                    )
                )

            if not resolved_blocks:
                rewritten.append(line)
                continue

            can_replace = (
                sidecar_cfg.replace_pure_link_line
                and len(line_links) == 1
                and len(resolved_blocks) == 1
                and _is_pure_link_line(line)
            )
            if can_replace:
                rewritten.extend(resolved_blocks[0][1])
                continue

            rewritten.append(line)
            for _, block_lines in resolved_blocks:
                rewritten.extend(block_lines)

        target_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        files_written.append(target_path.as_posix())

    for item in scan_diagnostics:
        _append_diagnostic(diagnostics, item.to_dict())

    manifest_path = (sidecar_root / ".iwp_sidecar_meta.v1.json").resolve()
    manifest_payload = {
        "schema_version": "iwp.code_sidecar.meta.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compiled_from_baseline_id": compiled_from_baseline_id,
        "node_catalog": _node_catalog_fingerprint(config),
        "code_files": _build_code_fingerprints(config, code_files),
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "code_sidecar_dir": sidecar_root.as_posix(),
        "enabled": sidecar_cfg.enabled,
        "files_scanned": len(code_files),
        "files_written": len(files_written),
        "links_found": links_found,
        "resolved_links": resolved,
        "unresolved_links": unresolved,
        "diagnostics": diagnostics[: sidecar_cfg.max_diagnostics],
        "total_diagnostics": len(diagnostics),
        "manifest_path": manifest_path.as_posix(),
        "compiled_at": manifest_payload["generated_at"],
        "compiled_from_baseline_id": compiled_from_baseline_id,
    }


def verify_code_sidecar_freshness(
    *,
    config: LintConfig,
) -> dict[str, object]:
    sidecar_root = _code_sidecar_root(config)
    manifest_path = (sidecar_root / ".iwp_sidecar_meta.v1.json").resolve()
    checked_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, object] = {
        "checked_at": checked_at,
        "manifest_path": manifest_path.as_posix(),
        "compiled_at": None,
        "compiled_from_baseline_id": None,
        "fresh": False,
        "stale_reasons": [],
    }
    if not bool(getattr(config.code_sidecar, "enabled", True)):
        payload["fresh"] = True
        payload["stale_reasons"] = []
        return payload
    if not manifest_path.exists():
        payload["stale_reasons"] = ["missing_sidecar_manifest"]
        return payload
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload["stale_reasons"] = ["invalid_sidecar_manifest"]
        return payload
    if not isinstance(raw, dict):
        payload["stale_reasons"] = ["invalid_sidecar_manifest"]
        return payload

    payload["compiled_at"] = raw.get("generated_at")
    payload["compiled_from_baseline_id"] = raw.get("compiled_from_baseline_id")
    stale_reasons: list[str] = []
    schema_version = str(raw.get("schema_version", ""))
    if schema_version != "iwp.code_sidecar.meta.v1":
        stale_reasons.append("unsupported_sidecar_manifest_schema")

    manifest_catalog = raw.get("node_catalog")
    current_catalog = _node_catalog_fingerprint(config)
    if not isinstance(manifest_catalog, dict):
        stale_reasons.append("missing_node_catalog_fingerprint")
    else:
        if str(manifest_catalog.get("sha256", "")) != str(current_catalog.get("sha256", "")):
            stale_reasons.append("node_catalog_hash_mismatch")

    manifest_code_files = raw.get("code_files")
    if not isinstance(manifest_code_files, list):
        stale_reasons.append("missing_code_fingerprints")
    else:
        current_files = discover_code_files(
            config.project_root,
            config.code_roots,
            config.include_ext,
            config.code_exclude_globs,
        )
        expected = {
            str(item.get("path", "")): str(item.get("sha256", ""))
            for item in manifest_code_files
            if isinstance(item, dict)
        }
        current = {
            file_path.relative_to(config.project_root).as_posix(): _file_sha256(file_path)
            for file_path in current_files
        }
        if set(expected.keys()) != set(current.keys()):
            stale_reasons.append("code_file_set_mismatch")
        else:
            for rel_path, digest in current.items():
                if expected.get(rel_path) != digest:
                    stale_reasons.append("code_hash_mismatch")
                    break

    payload["stale_reasons"] = stale_reasons
    payload["fresh"] = len(stale_reasons) == 0
    return payload


def _code_sidecar_root(config: LintConfig) -> Path:
    return (config.project_root / config.code_sidecar.dir).resolve()


def _load_source_lines(
    config: LintConfig,
    cache: dict[str, list[str]],
    source_path: str,
) -> list[str]:
    lines = cache.get(source_path)
    if lines is not None:
        return lines
    source_file = (config.iwp_root_path / source_path).resolve()
    if not source_file.exists():
        cache[source_path] = []
        return cache[source_path]
    cache[source_path] = source_file.read_text(encoding="utf-8").splitlines()
    return cache[source_path]


def _render_context_block(
    *,
    config: LintConfig,
    entry: NodeCatalogEntry,
    source_lines: list[str],
) -> list[str]:
    lines = [
        f"<<<IWP_NODE_CONTEXT source={entry.source_path} node={entry.node_id}>>>",
    ]
    sidecar_cfg = config.code_sidecar
    if sidecar_cfg.include_node_anchor_text and entry.anchor_text.strip():
        lines.append(entry.anchor_text.strip())
    if sidecar_cfg.include_node_block_text:
        block = _line_block(source_lines, entry.source_line_start, entry.source_line_end)
        for block_line in block.splitlines():
            text = block_line.rstrip()
            if text:
                lines.append(text)
    lines.append("<<<IWP_NODE_CONTEXT_END>>>")
    return lines


def _line_block(lines: list[str], start_line: int, end_line: int) -> str:
    start_idx = max(start_line - 1, 0)
    end_idx = max(end_line, start_idx + 1)
    if start_idx >= len(lines):
        return ""
    return "\n".join(lines[start_idx:end_idx]).strip("\n")


def _is_pure_link_line(line: str) -> bool:
    return bool(_PURE_LINK_LINE_RE.match(line.strip()))


def _append_diagnostic(diagnostics: list[dict[str, object]], item: object) -> None:
    if not isinstance(item, dict):
        return
    diagnostics.append(
        {
            "code": str(item.get("code", "IWP399")),
            "message": str(item.get("message", "")),
            "file_path": str(item.get("file_path", "")),
            "line": int(item.get("line", 0)),
            "column": int(item.get("column", 0)),
        }
    )


def _build_code_fingerprints(config: LintConfig, code_files: list[Path]) -> list[dict[str, str]]:
    fingerprints: list[dict[str, str]] = []
    for file_path in sorted(code_files):
        rel_path = file_path.relative_to(config.project_root).as_posix()
        fingerprints.append(
            {
                "path": rel_path,
                "sha256": _file_sha256(file_path),
            }
        )
    return fingerprints


def _node_catalog_fingerprint(config: LintConfig) -> dict[str, object]:
    catalog_path = (config.project_root / config.node_catalog_file).resolve()
    if not catalog_path.exists():
        return {"path": config.node_catalog_file, "exists": False, "sha256": ""}
    stable_digest = _stable_node_catalog_digest(catalog_path)
    if stable_digest:
        return {
            "path": config.node_catalog_file,
            "exists": True,
            "sha256": stable_digest,
            "strategy": "catalog_entries_v1",
        }
    return {
        "path": config.node_catalog_file,
        "exists": True,
        "sha256": _file_sha256(catalog_path),
        "strategy": "file_sha256_fallback",
    }


def _file_sha256(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _stable_node_catalog_digest(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    entries = payload.get("entries", [])
    version = payload.get("version", "")
    if not isinstance(entries, list):
        return ""
    normalized = {
        "version": str(version),
        "entries": entries,
    }
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{sha256(raw.encode('utf-8')).hexdigest()}"
