from __future__ import annotations

import json
from dataclasses import asdict
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
from .errors import Diagnostic
from .models import CoverageMetrics, LinkAnnotation, MarkdownNode

NodeKey = tuple[str, str]


def run_full(config: LintConfig) -> dict[str, Any]:
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        config.iwp_root_path,
        config.critical_node_patterns,
        schema_path,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
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
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
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
    )
    diagnostics.extend(schema_result.diagnostics)
    code_files = discover_code_files(config.project_root, config.code_roots, config.include_ext)
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
    link_node_index: dict[NodeKey, MarkdownNode] = {
        (node.source_path, node.node_id): node for node in link_nodes
    }
    link_node_source_index: set[NodeKey] = {(node.source_path, node.node_id) for node in link_nodes}
    link_source_paths = {node.source_path for node in link_nodes}

    valid_links: list[LinkAnnotation] = []
    valid_link_reports: list[dict[str, Any]] = []
    should_check_link_mapping = not (mode == "diff" and not target_nodes)
    if should_check_link_mapping:
        for link in links:
            if changed_md_files is not None and mode == "diff":
                if link.source_path not in changed_md_files:
                    continue

            if link.source_path not in link_source_paths:
                diagnostics.append(
                    Diagnostic(
                        code="IWP103",
                        message=f"source_path not found in target markdown set: {link.source_path}",
                        file_path=link.file_path,
                        line=link.line,
                        column=link.column,
                    )
                )
                continue
            if (link.source_path, link.node_id) not in link_node_source_index:
                diagnostics.append(
                    Diagnostic(
                        code="IWP105",
                        message=(
                            "node_id does not exist in source_path: "
                            f"{link.source_path}::{link.node_id}"
                        ),
                        file_path=link.file_path,
                        line=link.line,
                        column=link.column,
                    )
                )
                continue
            valid_links.append(link)
            node = link_node_index[(link.source_path, link.node_id)]
            valid_link_reports.append(
                {
                    **asdict(link),
                    "computed_kind": node.computed_kind,
                }
            )

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
    kind_breakdown = _kind_breakdown(valid_link_reports)

    uncovered = [
        node for node in target_nodes if (node.source_path, node.node_id) not in linked_node_keys
    ]
    for node in uncovered:
        diagnostics.append(
            Diagnostic(
                code="IWP107",
                message=f"Node not covered: {node.node_id}",
                file_path=node.source_path,
                line=node.line_start,
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

    metrics = _compute_metrics(
        target_nodes,
        linked_node_keys,
        critical_node_keys,
        linked_critical_node_keys,
        tested_node_keys,
    )
    threshold_diags = _threshold_diagnostics(config, metrics)
    diagnostics.extend(threshold_diags)

    diagnostics.sort(key=lambda d: (d.file_path, d.line, d.column, d.code))
    error_count = len([d for d in diagnostics if d.severity == "error"])
    warning_count = len([d for d in diagnostics if d.severity != "error"])
    return {
        "mode": mode,
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "total_nodes_in_scope": len(target_nodes),
            "total_nodes_all": all_nodes_count or len(target_nodes),
            "covered_nodes": metrics.linked_nodes,
            "schema_checked_files": schema_result.checked_files,
            "schema_matched_files": schema_result.matched_files,
            "kind_breakdown": kind_breakdown,
        },
        "metrics": metrics.to_dict(),
        "diagnostics": [d.to_dict() for d in diagnostics],
        "nodes": [node.to_dict() for node in target_nodes],
        "links_valid": valid_link_reports,
    }


def _compute_metrics(
    nodes: list[MarkdownNode],
    linked_node_keys: set[NodeKey],
    critical_node_keys: set[NodeKey],
    linked_critical_node_keys: set[NodeKey],
    tested_node_keys: set[NodeKey],
) -> CoverageMetrics:
    total_nodes = len(nodes)
    critical_nodes = len(critical_node_keys)
    linked_nodes = len(linked_node_keys)
    linked_critical_nodes = len(linked_critical_node_keys)
    tested_nodes = len(tested_node_keys)

    return CoverageMetrics(
        total_nodes=total_nodes,
        linked_nodes=linked_nodes,
        critical_nodes=critical_nodes,
        linked_critical_nodes=linked_critical_nodes,
        tested_nodes=tested_nodes,
        node_linked_percent=_pct(linked_nodes, total_nodes),
        critical_linked_percent=_pct(linked_critical_nodes, critical_nodes),
        node_tested_percent=_pct(tested_nodes, total_nodes),
    )


def _threshold_diagnostics(config: LintConfig, metrics: CoverageMetrics) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if metrics.node_linked_percent < config.thresholds.node_linked_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "NodeLinked% below threshold: "
                    f"{metrics.node_linked_percent:.2f} < {config.thresholds.node_linked_min:.2f}"
                ),
                file_path=config.iwp_root,
            )
        )
    if metrics.critical_linked_percent < config.thresholds.critical_linked_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "CriticalNodeLinked% below threshold: "
                    f"{metrics.critical_linked_percent:.2f} < {config.thresholds.critical_linked_min:.2f}"
                ),
                file_path=config.iwp_root,
            )
        )
    if metrics.node_tested_percent < config.thresholds.node_tested_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "NodeTested% below threshold: "
                    f"{metrics.node_tested_percent:.2f} < {config.thresholds.node_tested_min:.2f}"
                ),
                file_path=config.iwp_root,
            )
        )
    return diags


def _pct(num: int, den: int) -> float:
    if den == 0:
        return 100.0
    return round((num / den) * 100, 2)


def _kind_breakdown(valid_link_reports: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in valid_link_reports:
        key = str(item["computed_kind"])
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def print_console_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    metrics = report["metrics"]
    print(
        f"[iwp-lint] mode={report['mode']} errors={summary['error_count']} "
        f"warnings={summary.get('warning_count', 0)} "
        f"nodes={summary['total_nodes_in_scope']} covered={summary['covered_nodes']}"
    )
    print(
        f"[iwp-lint] NodeLinked={metrics['node_linked_percent']}% "
        f"CriticalNodeLinked={metrics['critical_linked_percent']}% "
        f"NodeTested={metrics['node_tested_percent']}%"
    )
    for diag in report["diagnostics"]:
        loc = f"{diag['file_path']}:{diag['line']}:{diag['column']}"
        print(f"{diag['code']} {loc} {diag['message']}")


def write_json_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
