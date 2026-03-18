from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import LintConfig, resolve_schema_source
from .core.engine import run_full
from .core.models import MarkdownNode
from .core.node_catalog import compile_node_context, verify_compiled_context
from .parsers.md_parser import parse_markdown_nodes
from .vcs.diff_resolver import impacted_nodes, load_diff
from .vcs.snapshot_store import SnapshotStore, collect_workspace_files
from .vcs.task_store import list_tasks, load_task, update_task_status


def snapshot_action(config: LintConfig, action: str) -> dict[str, Any]:
    store = SnapshotStore((config.project_root / config.snapshot_db_file).resolve())
    if action in {"init", "update"}:
        files = collect_workspace_files(
            project_root=config.project_root,
            iwp_root=config.iwp_root,
            iwp_root_path=config.iwp_root_path,
            code_roots=config.code_roots,
            include_ext=config.include_ext,
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


def tasks_list(config: LintConfig) -> dict[str, Any]:
    task_dir = (config.project_root / config.task_dir).resolve()
    tasks = list_tasks(task_dir)
    return {"count": len(tasks), "tasks": [item.to_dict() for item in tasks]}


def tasks_show(config: LintConfig, task_id: str) -> dict[str, Any]:
    task_dir = (config.project_root / config.task_dir).resolve()
    task = load_task(task_dir, task_id)
    return {"task": task.to_dict()}


def tasks_complete(config: LintConfig, task_id: str, notes: str = "") -> dict[str, Any]:
    task_dir = (config.project_root / config.task_dir).resolve()
    task = update_task_status(task_dir, task_id, status="done", notes=notes)
    return {"task": task.to_dict()}


def tasks_mark_running(config: LintConfig, task_id: str, notes: str = "") -> dict[str, Any]:
    task_dir = (config.project_root / config.task_dir).resolve()
    task = update_task_status(task_dir, task_id, status="running", notes=notes)
    return {"task": task.to_dict()}


def tasks_mark_failed(config: LintConfig, task_id: str, notes: str = "") -> dict[str, Any]:
    task_dir = (config.project_root / config.task_dir).resolve()
    task = update_task_status(task_dir, task_id, status="failed", notes=notes)
    return {"task": task.to_dict()}


def run_quality_gate(config: LintConfig) -> dict[str, Any]:
    report = run_full(config)
    lint_exit_code = 1 if report["summary"]["error_count"] > 0 else 0
    return {
        "lint_exit_code": lint_exit_code,
        "lint_report": report,
    }


def compile_context(config: LintConfig, source_paths: list[str] | None = None) -> dict[str, Any]:
    return compile_node_context(config=config, source_paths=source_paths)


def verify_compiled(config: LintConfig, source_paths: list[str] | None = None) -> dict[str, Any]:
    return verify_compiled_context(config=config, source_paths=source_paths)


def _compute_impacted_nodes(config: LintConfig, diff) -> list[MarkdownNode]:
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        config.iwp_root_path,
        config.critical_node_patterns,
        schema_path,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
    )
    return impacted_nodes(nodes, diff)
