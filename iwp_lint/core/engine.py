from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import LintConfig, resolve_schema_source
from ..parsers.comment_scanner import (
    discover_code_files,
    is_test_file,
    scan_links,
    validate_link_protocol,
)
from ..parsers.md_parser import parse_markdown_nodes
from ..schema.schema_validator import validate_markdown_schema
from ..vcs.diff_resolver import impacted_nodes, load_diff
from .coverage_policy import (
    compute_metrics,
    kind_breakdown,
    profile_breakdown,
    profile_threshold_diagnostics,
    resolve_node_profiles,
    threshold_diagnostics,
)
from .errors import Diagnostic
from .link_validation import validate_links_against_nodes
from .models import CoverageMetrics, LinkAnnotation, MarkdownNode
from .report_helpers import build_link_migration_suggestions, build_repair_summary

NodeKey = tuple[str, str]


def run_full(config: LintConfig) -> dict[str, Any]:
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
    return _run_core(config, nodes, mode="full", changed_md_files=None, changed_code_files=None)


def run_diff(config: LintConfig, base: str | None, head: str | None) -> dict[str, Any]:
    diff = load_diff(
        config=config,
        base=base or config.diff_base,
        head=head or config.diff_head,
        cwd=config.project_root,
        strict=config.diff_strict,
    )
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
    impacted = impacted_nodes(nodes, diff)
    changed_md = {
        f for f in diff.changed_files if f.startswith(f"{config.iwp_root}/") and f.endswith(".md")
    }
    changed_code = {f for f in diff.changed_files if Path(f).suffix in set(config.include_ext)}
    changed_md_rel = {Path(p).relative_to(config.iwp_root).as_posix() for p in changed_md}
    link_scope_nodes = [node for node in nodes if node.source_path in changed_md_rel]
    return _run_core(
        config,
        impacted,
        mode="diff",
        changed_md_files=changed_md_rel,
        changed_code_files=changed_code,
        all_nodes_count=len(nodes),
        link_scope_nodes=link_scope_nodes,
    )


def run_schema(config: LintConfig, mode_override: str | None = None) -> dict[str, Any]:
    schema_path = resolve_schema_source(config)
    schema_result = validate_markdown_schema(
        iwp_root=config.iwp_root_path,
        schema_path=schema_path,
        mode=mode_override or config.schema_mode,
        target_rel_paths=None,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        page_only_enabled=config.page_only.enabled,
    )
    diagnostics = sorted(
        schema_result.diagnostics,
        key=lambda d: (d.file_path, d.line, d.column, d.code),
    )
    error_count = len([d for d in diagnostics if d.severity == "error"])
    warning_count = len([d for d in diagnostics if d.severity != "error"])
    return {
        "mode": "schema",
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "page_only_enabled": bool(config.page_only.enabled),
            "total_nodes_in_scope": 0,
            "total_nodes_all": 0,
            "covered_nodes": 0,
            "schema_checked_files": schema_result.checked_files,
            "schema_matched_files": schema_result.matched_files,
        },
        "metrics": CoverageMetrics(
            total_nodes=0,
            linked_nodes=0,
            critical_nodes=0,
            linked_critical_nodes=0,
            tested_nodes=0,
            node_linked_percent=0.0,
            critical_linked_percent=0.0,
            node_tested_percent=0.0,
        ).to_dict(),
        "diagnostics": [d.to_dict() for d in diagnostics],
        "nodes": [],
        "links_valid": [],
    }


def _run_core(
    config: LintConfig,
    target_nodes: list[MarkdownNode],
    mode: str,
    changed_md_files: set[str] | None,
    changed_code_files: set[str] | None,
    all_nodes_count: int | None = None,
    link_scope_nodes: list[MarkdownNode] | None = None,
) -> dict[str, Any]:
    diagnostics: list[Diagnostic] = []

    schema_path = resolve_schema_source(config)
    schema_result = validate_markdown_schema(
        iwp_root=config.iwp_root_path,
        schema_path=schema_path,
        mode=config.schema_mode,
        target_rel_paths=changed_md_files if mode == "diff" else None,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        page_only_enabled=config.page_only.enabled,
    )
    diagnostics.extend(schema_result.diagnostics)
    code_files = discover_code_files(
        config.project_root,
        config.code_roots,
        config.include_ext,
        config.code_exclude_globs,
    )
    links, scan_diagnostics = scan_links(
        config.project_root, code_files, config.allow_multi_link_per_symbol
    )

    if mode == "diff" and changed_code_files is not None:
        scan_diagnostics = [d for d in scan_diagnostics if d.file_path in changed_code_files]
        links = [
            link
            for link in links
            if link.file_path in changed_code_files
            or link.source_path in (changed_md_files or set())
        ]

    diagnostics.extend(scan_diagnostics)
    diagnostics.extend(validate_link_protocol(links))

    link_nodes = link_scope_nodes if link_scope_nodes is not None else target_nodes
    valid_links: list[LinkAnnotation] = []
    valid_link_reports: list[dict[str, Any]] = []
    stale_links: list[LinkAnnotation] = []
    should_check_link_mapping = not (mode == "diff" and not target_nodes)
    if should_check_link_mapping:
        (
            valid_links,
            valid_link_reports,
            stale_links,
            mapping_diags,
        ) = validate_links_against_nodes(
            links=links,
            link_nodes=link_nodes,
            changed_md_files=changed_md_files,
            mode=mode,
        )
        diagnostics.extend(mapping_diags)

    linked_node_keys: set[NodeKey] = {(link.source_path, link.node_id) for link in valid_links}
    tested_node_keys: set[NodeKey] = {
        (link.source_path, link.node_id)
        for link in valid_links
        if is_test_file(link.file_path, config.test_globs)
    }
    target_node_keys: set[NodeKey] = {(node.source_path, node.node_id) for node in target_nodes}
    # Keep diff/full coverage metrics scoped to target_nodes to avoid percent > 100.
    linked_node_keys &= target_node_keys
    tested_node_keys &= target_node_keys
    critical_node_keys: set[NodeKey] = {
        (node.source_path, node.node_id) for node in target_nodes if node.is_critical
    }
    linked_critical_node_keys = critical_node_keys & linked_node_keys
    kind_stats = kind_breakdown(valid_link_reports)
    profile_by_node = resolve_node_profiles(target_nodes, config.coverage_profiles)
    uncovered = [
        node for node in target_nodes if (node.source_path, node.node_id) not in linked_node_keys
    ]
    trace_required_nodes = [node for node in target_nodes if node.trace_required]
    trace_required_uncovered_nodes = [
        node
        for node in trace_required_nodes
        if (node.source_path, node.node_id) not in linked_node_keys
    ]
    for node in uncovered:
        profile = profile_by_node.get((node.source_path, node.node_id))
        is_trace_required_uncovered = (
            node.trace_required and (node.source_path, node.node_id) not in linked_node_keys
        )
        severity = (
            "error"
            if is_trace_required_uncovered
            else (profile.missing_severity if profile is not None else "error")
        )
        diagnostics.append(
            Diagnostic(
                code="IWP107",
                message=f"Node not covered: {node.node_id}",
                file_path=node.source_path,
                line=node.line_start,
                severity=severity,
            )
        )
    kind_unknown_nodes = [node for node in target_nodes if node.section_key == "unknown_section"]
    kind_unknown_severity = (
        "error" if config.authoring.kind_unknown_policy == "error" else "warning"
    )
    for node in kind_unknown_nodes:
        diagnostics.append(
            Diagnostic(
                code="IWP110",
                message=f"Node semantic kind is unknown: {node.node_id}",
                file_path=node.source_path,
                line=node.line_start,
                severity=kind_unknown_severity,
            )
        )

    uncovered_critical = [
        node
        for node in target_nodes
        if node.is_critical and (node.source_path, node.node_id) not in linked_node_keys
    ]
    for node in uncovered_critical:
        diagnostics.append(
            Diagnostic(
                code="IWP108",
                message=f"Critical node not covered: {node.node_id}",
                file_path=node.source_path,
                line=node.line_start,
            )
        )

    metrics = compute_metrics(
        target_nodes,
        linked_node_keys,
        critical_node_keys,
        linked_critical_node_keys,
        tested_node_keys,
    )
    threshold_diags = threshold_diagnostics(config, metrics, mode=mode)
    threshold_diags.extend(
        profile_threshold_diagnostics(
            nodes=target_nodes,
            linked_node_keys=linked_node_keys,
            config=config,
            profile_by_node=profile_by_node,
        )
    )
    diagnostics.extend(threshold_diags)
    link_migration_suggestions = build_link_migration_suggestions(
        stale_links=stale_links,
        candidate_nodes=link_nodes,
        linked_node_keys=linked_node_keys,
    )
    repair_summary = build_repair_summary(
        nodes=target_nodes,
        linked_node_keys=linked_node_keys,
        valid_links=valid_links,
    )

    diagnostics.sort(key=lambda d: (d.file_path, d.line, d.column, d.code))
    error_count = len([d for d in diagnostics if d.severity == "error"])
    warning_count = len([d for d in diagnostics if d.severity != "error"])
    return {
        "mode": mode,
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "page_only_enabled": bool(config.page_only.enabled),
            "total_nodes_in_scope": len(target_nodes),
            "total_nodes_all": all_nodes_count or len(target_nodes),
            "covered_nodes": metrics.linked_nodes,
            "schema_checked_files": schema_result.checked_files,
            "schema_matched_files": schema_result.matched_files,
            "kind_breakdown": kind_stats,
            "profile_breakdown": profile_breakdown(target_nodes, linked_node_keys, profile_by_node),
            "trace_required_nodes": len(trace_required_nodes),
            "trace_required_uncovered_nodes": len(trace_required_uncovered_nodes),
            "trace_token_profile_enabled": bool(config.authoring.tokens.enabled),
            "kind_unknown_nodes": len(kind_unknown_nodes),
        },
        "metrics": metrics.to_dict(),
        "profile_metrics": profile_breakdown(target_nodes, linked_node_keys, profile_by_node),
        "diagnostics": [d.to_dict() for d in diagnostics],
        "nodes": [node.to_dict() for node in target_nodes],
        "links_valid": valid_link_reports,
        "link_migration_suggestions": link_migration_suggestions,
        "repair_summary": repair_summary,
    }


def print_console_report(
    report: dict[str, Any],
    *,
    min_severity: str = "warning",
    quiet_warnings: bool = False,
) -> None:
    summary = report["summary"]
    metrics = report["metrics"]
    status = "OK"
    if int(summary["error_count"]) > 0:
        status = "FAIL"
    elif int(summary.get("warning_count", 0)) > 0:
        status = "PASS_WITH_WARNINGS"
    print(
        f"[iwp-lint] mode={report['mode']} errors={summary['error_count']} "
        f"warnings={summary.get('warning_count', 0)} "
        f"nodes={summary['total_nodes_in_scope']} covered={summary['covered_nodes']} "
        f"status={status}"
    )
    print(
        f"[iwp-lint] NodeLinked={metrics['node_linked_percent']}% "
        f"CriticalNodeLinked={metrics['critical_linked_percent']}% "
        f"NodeTested={metrics['node_tested_percent']}%"
    )
    for diag in _filter_console_diagnostics(
        report["diagnostics"],
        min_severity=min_severity,
        quiet_warnings=quiet_warnings,
    ):
        loc = f"{diag['file_path']}:{diag['line']}:{diag['column']}"
        severity_tag = "[E]" if str(diag.get("severity", "error")).lower() == "error" else "[W]"
        print(f"{severity_tag}[{diag['code']}] {loc} {diag['message']}")


def _severity_rank(value: str) -> int:
    return 1 if value.lower() == "error" else 0


def _filter_console_diagnostics(
    diagnostics: list[dict[str, Any]],
    *,
    min_severity: str,
    quiet_warnings: bool,
) -> list[dict[str, Any]]:
    threshold = _severity_rank(min_severity)
    filtered: list[dict[str, Any]] = []
    for item in diagnostics:
        severity = str(item.get("severity", "error")).lower()
        if quiet_warnings and severity != "error":
            continue
        if _severity_rank(severity) < threshold:
            continue
        filtered.append(item)
    return filtered


def write_json_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
