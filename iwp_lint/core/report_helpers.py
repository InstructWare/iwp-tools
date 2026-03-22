from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .models import LinkAnnotation, MarkdownNode

NodeKey = tuple[str, str]


def build_link_migration_suggestions(
    *,
    stale_links: list[LinkAnnotation],
    candidate_nodes: list[MarkdownNode],
    linked_node_keys: set[NodeKey],
    max_candidates: int = 3,
) -> list[dict[str, Any]]:
    if not stale_links:
        return []
    by_source: dict[str, list[MarkdownNode]] = defaultdict(list)
    for node in candidate_nodes:
        by_source[node.source_path].append(node)

    suggestions: list[dict[str, Any]] = []
    for link in stale_links:
        candidates = by_source.get(link.source_path, [])
        ranked: list[tuple[float, MarkdownNode, list[str]]] = []
        for node in candidates:
            score, reasons = _candidate_score(
                stale_node_id=link.node_id,
                candidate=node,
                linked_node_keys=linked_node_keys,
            )
            if score <= 0.0:
                continue
            ranked.append((score, node, reasons))
        ranked.sort(key=lambda item: (-item[0], item[1].line_start, item[1].node_id))
        top = ranked[: max(max_candidates, 1)]
        suggestions.append(
            {
                "source_path": link.source_path,
                "stale_node_id": link.node_id,
                "candidates": [
                    {
                        "node_id": node.node_id,
                        "score": round(score, 4),
                        "reasons": reasons,
                    }
                    for score, node, reasons in top
                ],
            }
        )
    return suggestions


def build_repair_summary(
    *,
    nodes: list[MarkdownNode],
    linked_node_keys: set[NodeKey],
    valid_links: list[LinkAnnotation],
) -> dict[str, Any]:
    by_file_missing: dict[str, dict[str, int]] = defaultdict(
        lambda: {"critical_missing": 0, "missing": 0}
    )
    for node in nodes:
        key = (node.source_path, node.node_id)
        if key in linked_node_keys:
            continue
        bucket = by_file_missing[node.source_path]
        bucket["missing"] += 1
        if node.is_critical:
            bucket["critical_missing"] += 1

    source_to_targets = _source_target_distribution(valid_links)
    global_targets = _global_target_distribution(valid_links)
    by_file = []
    for source_path in sorted(by_file_missing.keys()):
        bucket = by_file_missing[source_path]
        source_targets = source_to_targets.get(source_path, [])
        suggested_targets = source_targets or global_targets
        by_file.append(
            {
                "source_path": source_path,
                "critical_missing": bucket["critical_missing"],
                "missing": bucket["missing"],
                "suggested_targets": suggested_targets[:3],
            }
        )

    critical_missing_total = sum(item["critical_missing"] for item in by_file)
    missing_total = sum(item["missing"] for item in by_file)
    return {
        "critical_missing_total": critical_missing_total,
        "missing_total": missing_total,
        "by_file": by_file,
        "next_actions": [
            "patch critical links first",
            "patch remaining missing links",
            "rerun build --mode diff, then rerun verify",
        ],
    }


def _source_target_distribution(valid_links: list[LinkAnnotation]) -> dict[str, list[str]]:
    by_source_target: dict[str, Counter[str]] = defaultdict(Counter)
    for link in valid_links:
        by_source_target[link.source_path][link.file_path] += 1
    out: dict[str, list[str]] = {}
    for source_path, counts in by_source_target.items():
        out[source_path] = [
            file_path
            for file_path, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    return out


def _global_target_distribution(valid_links: list[LinkAnnotation]) -> list[str]:
    counts: Counter[str] = Counter(link.file_path for link in valid_links)
    return [
        file_path for file_path, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _candidate_score(
    *,
    stale_node_id: str,
    candidate: MarkdownNode,
    linked_node_keys: set[NodeKey],
) -> tuple[float, list[str]]:
    reasons = ["same_source_path"]
    score = 0.05

    prefix = _id_prefix_similarity(stale_node_id, candidate.node_id)
    if prefix > 0.0:
        score += 0.55 * prefix
        reasons.append("node_id_prefix")
    key = (candidate.source_path, candidate.node_id)
    if key not in linked_node_keys:
        score += 0.30
        reasons.append("currently_unlinked")
    if candidate.is_critical:
        score += 0.15
        reasons.append("critical_node")
    return score, reasons


def _id_prefix_similarity(stale_node_id: str, node_id: str) -> float:
    stale = stale_node_id.split(".", 1)[1] if "." in stale_node_id else stale_node_id
    curr = node_id.split(".", 1)[1] if "." in node_id else node_id
    if not stale or not curr:
        return 0.0
    max_len = max(len(stale), len(curr))
    common = 0
    for left, right in zip(stale, curr, strict=False):
        if left != right:
            break
        common += 1
    return common / max_len
