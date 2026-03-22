from __future__ import annotations

from dataclasses import asdict

from .errors import Diagnostic
from .models import LinkAnnotation, MarkdownNode

NodeKey = tuple[str, str]


def validate_links_against_nodes(
    *,
    links: list[LinkAnnotation],
    link_nodes: list[MarkdownNode],
    changed_md_files: set[str] | None,
    mode: str,
) -> tuple[list[LinkAnnotation], list[dict[str, object]], list[LinkAnnotation], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    link_node_index: dict[NodeKey, MarkdownNode] = {
        (node.source_path, node.node_id): node for node in link_nodes
    }
    link_node_source_index: set[NodeKey] = {(node.source_path, node.node_id) for node in link_nodes}
    link_source_paths = {node.source_path for node in link_nodes}

    valid_links: list[LinkAnnotation] = []
    valid_link_reports: list[dict[str, object]] = []
    stale_links: list[LinkAnnotation] = []
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
            stale_links.append(link)
            diagnostics.append(
                Diagnostic(
                    code="IWP105",
                    message=f"node_id does not exist in source_path: {link.source_path}::{link.node_id}",
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
                "anchor_level": node.anchor_level,
            }
        )
    return valid_links, valid_link_reports, stale_links, diagnostics
