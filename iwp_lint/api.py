from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import LintConfig, resolve_schema_source
from .core.engine import run_full
from .core.link_normalizer import normalize_links
from .core.models import MarkdownNode
from .core.node_catalog import (
    build_code_sidecar_context,
    compile_node_context,
    verify_code_sidecar_freshness_context,
    verify_compiled_context,
)
from .core.session_service import SessionService
from .parsers.md_parser import parse_markdown_nodes
from .vcs.diff_resolver import impacted_nodes, load_diff
from .vcs.snapshot_store import SnapshotStore, collect_workspace_files


def snapshot_action(config: LintConfig, action: str) -> dict[str, Any]:
    store = SnapshotStore((config.project_root / config.snapshot_db_file).resolve())
    if action in {"init", "update"}:
        files = collect_workspace_files(
            project_root=config.project_root,
            iwp_root=config.iwp_root,
            iwp_root_path=config.iwp_root_path,
            code_roots=config.code_roots,
            include_ext=config.include_ext,
            code_exclude_globs=config.code_exclude_globs,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
        )
        snapshot_id = store.create_snapshot(files)
        return {
            "snapshot_id": snapshot_id,
            "file_count": len(files),
            "db_path": str((config.project_root / config.snapshot_db_file).resolve().as_posix()),
        }

    if action == "diff":
        diff = load_diff(
            config=config,
            base=config.diff_base,
            head=config.diff_head,
            cwd=config.project_root,
            strict=config.diff_strict,
            provider_name="filesystem_snapshot",
        )
        changed_md = {
            Path(item).relative_to(config.iwp_root).as_posix()
            for item in diff.changed_files
            if item.startswith(f"{config.iwp_root}/") and item.endswith(".md")
        }
        changed_code = {
            item for item in diff.changed_files if Path(item).suffix in set(config.include_ext)
        }
        nodes = _compute_impacted_nodes(config, diff)
        return {
            "changed_files": sorted(diff.changed_files),
            "changed_md_files": sorted(changed_md),
            "changed_code_files": sorted(changed_code),
            "changed_count": len(diff.changed_files),
            "impacted_nodes": [node.to_dict() for node in nodes],
        }

    raise RuntimeError(f"unknown snapshot action: {action}")


def baseline_status(config: LintConfig) -> dict[str, Any]:
    db_path = (config.project_root / config.snapshot_db_file).resolve()
    store = SnapshotStore(db_path)
    latest = store.latest_snapshot_info()
    if latest is None:
        return {
            "snapshot_db_path": db_path.as_posix(),
            "baseline_exists": False,
            "baseline_snapshot_id": None,
            "baseline_created_at": None,
        }
    return {
        "snapshot_db_path": db_path.as_posix(),
        "baseline_exists": True,
        "baseline_snapshot_id": latest["snapshot_id"],
        "baseline_created_at": latest["created_at"],
    }


def run_quality_gate(config: LintConfig) -> dict[str, Any]:
    report = run_full(config)
    lint_exit_code = 1 if report["summary"]["error_count"] > 0 else 0
    return {
        "lint_exit_code": lint_exit_code,
        "lint_report": report,
    }


def run_gate_suite(config: LintConfig) -> dict[str, Any]:
    service = SessionService(config)
    return service.run_gate_suite()


def compile_context(config: LintConfig, source_paths: list[str] | None = None) -> dict[str, Any]:
    return compile_node_context(config=config, source_paths=source_paths)


def verify_compiled(config: LintConfig, source_paths: list[str] | None = None) -> dict[str, Any]:
    return verify_compiled_context(config=config, source_paths=source_paths)


def normalize_annotations(config: LintConfig, write: bool = False) -> dict[str, Any]:
    return normalize_links(config=config, write=write)


def build_code_sidecar(
    config: LintConfig,
    *,
    compiled_from_baseline_id: int | None = None,
) -> dict[str, Any]:
    return build_code_sidecar_context(
        config=config,
        compiled_from_baseline_id=compiled_from_baseline_id,
    )


def verify_code_sidecar_freshness(config: LintConfig) -> dict[str, Any]:
    return verify_code_sidecar_freshness_context(config=config)


def session_start(
    config: LintConfig,
    *,
    session_id: str | None = None,
    metadata: dict[str, object] | None = None,
    allow_custom_id: bool = False,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.start(
        session_id=session_id,
        metadata=metadata,
        allow_custom_id=allow_custom_id,
    )


def session_diff(
    config: LintConfig,
    *,
    session_id: str,
    code_diff_level: str | None = None,
    code_diff_context_lines: int | None = None,
    code_diff_max_chars: int | None = None,
    node_severity: str | None = None,
    node_file_types: list[str] | None = None,
    node_anchor_levels: list[str] | None = None,
    node_kind_prefixes: list[str] | None = None,
    critical_only: bool = False,
    markdown_excerpt_max_chars: int | None = None,
    include_baseline_gaps: bool = False,
    focus_path: str | None = None,
    max_gap_items: int | None = None,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.diff(
        session_id=session_id,
        code_diff_level=code_diff_level,
        code_diff_context_lines=code_diff_context_lines,
        code_diff_max_chars=code_diff_max_chars,
        node_severity=node_severity,
        node_file_types=node_file_types,
        node_anchor_levels=node_anchor_levels,
        node_kind_prefixes=node_kind_prefixes,
        critical_only=critical_only,
        markdown_excerpt_max_chars=markdown_excerpt_max_chars,
        include_baseline_gaps=include_baseline_gaps,
        focus_path=focus_path,
        max_gap_items=max_gap_items,
    )


def session_commit(
    config: LintConfig,
    *,
    session_id: str,
    enforce_gate: bool = True,
    allow_stale_sidecar: bool = False,
    code_diff_level: str | None = None,
    code_diff_context_lines: int | None = None,
    code_diff_max_chars: int | None = None,
    node_severity: str | None = None,
    node_file_types: list[str] | None = None,
    node_anchor_levels: list[str] | None = None,
    node_kind_prefixes: list[str] | None = None,
    critical_only: bool = False,
    markdown_excerpt_max_chars: int | None = None,
    include_evidence: bool = False,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.commit(
        session_id=session_id,
        enforce_gate=enforce_gate,
        allow_stale_sidecar=allow_stale_sidecar,
        code_diff_level=code_diff_level,
        code_diff_context_lines=code_diff_context_lines,
        code_diff_max_chars=code_diff_max_chars,
        node_severity=node_severity,
        node_file_types=node_file_types,
        node_anchor_levels=node_anchor_levels,
        node_kind_prefixes=node_kind_prefixes,
        critical_only=critical_only,
        markdown_excerpt_max_chars=markdown_excerpt_max_chars,
        include_evidence=include_evidence,
    )


def session_gate(
    config: LintConfig,
    *,
    session_id: str,
) -> dict[str, Any]:
    service = SessionService(config)
    return service.gate(session_id=session_id)


def session_audit(config: LintConfig, *, session_id: str) -> dict[str, Any]:
    service = SessionService(config)
    return service.audit(session_id=session_id)


def session_current(config: LintConfig) -> dict[str, Any]:
    service = SessionService(config)
    return service.current()


def _compute_impacted_nodes(config: LintConfig, diff) -> list[MarkdownNode]:
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
    return impacted_nodes(nodes, diff)
