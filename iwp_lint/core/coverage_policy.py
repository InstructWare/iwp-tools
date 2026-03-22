from __future__ import annotations

from typing import Any

from ..config import CoverageProfile, LintConfig, LintThresholds
from .errors import Diagnostic
from .models import CoverageMetrics, MarkdownNode

NodeKey = tuple[str, str]


def compute_metrics(
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
        node_linked_percent=pct(linked_nodes, total_nodes),
        critical_linked_percent=pct(linked_critical_nodes, critical_nodes),
        node_tested_percent=pct(tested_nodes, total_nodes),
    )


def threshold_diagnostics(
    config: LintConfig,
    metrics: CoverageMetrics,
    *,
    mode: str,
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    thresholds = thresholds_for_mode(config, mode)
    if metrics.node_linked_percent < thresholds.node_linked_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "NodeLinked% below threshold: "
                    f"{metrics.node_linked_percent:.2f} < {thresholds.node_linked_min:.2f}"
                ),
                file_path=config.iwp_root,
            )
        )
    if metrics.critical_linked_percent < thresholds.critical_linked_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "CriticalNodeLinked% below threshold: "
                    f"{metrics.critical_linked_percent:.2f} < {thresholds.critical_linked_min:.2f}"
                ),
                file_path=config.iwp_root,
            )
        )
    tiny_diff_active = (
        mode == "diff" and 0 < metrics.total_nodes < config.tiny_diff.min_impacted_nodes
    )
    tiny_diff_suffix = (
        " (tiny-diff guardrail active: "
        f"impacted_nodes={metrics.total_nodes}, "
        f"tested_nodes={metrics.tested_nodes}, "
        f"min_impacted_nodes={config.tiny_diff.min_impacted_nodes})"
        if tiny_diff_active
        else ""
    )
    tested_severity = (
        "warning" if tiny_diff_active and config.tiny_diff.degrade_to_warning else "error"
    )
    if metrics.node_tested_percent < thresholds.node_tested_min:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "NodeTested% below threshold: "
                    f"{metrics.node_tested_percent:.2f} < {thresholds.node_tested_min:.2f}"
                    f"{tiny_diff_suffix}"
                ),
                file_path=config.iwp_root,
                severity=tested_severity,
            )
        )
    if tiny_diff_active and metrics.tested_nodes < config.tiny_diff.node_tested_min_count:
        diags.append(
            Diagnostic(
                code="IWP109",
                message=(
                    "Tiny-diff tested node count below minimum: "
                    f"{metrics.tested_nodes} < {config.tiny_diff.node_tested_min_count}"
                ),
                file_path=config.iwp_root,
                severity=tested_severity,
            )
        )
    return diags


def thresholds_for_mode(config: LintConfig, mode: str) -> LintThresholds:
    if mode == "diff":
        return config.thresholds_by_mode.diff
    if mode in {"full", "schema"}:
        return config.thresholds_by_mode.full
    return config.thresholds


def profile_threshold_diagnostics(
    *,
    nodes: list[MarkdownNode],
    linked_node_keys: set[NodeKey],
    config: LintConfig,
    profile_by_node: dict[NodeKey, CoverageProfile | None],
) -> list[Diagnostic]:
    if not config.enable_profile_coverage:
        return []
    diags: list[Diagnostic] = []
    profile_index = profile_breakdown(nodes, linked_node_keys, profile_by_node)
    for profile in config.coverage_profiles:
        stats = profile_index.get(profile.name)
        if not isinstance(stats, dict):
            continue
        node_linked_percent = float(stats.get("node_linked_percent", 100.0))
        critical_linked_percent = float(stats.get("critical_linked_percent", 100.0))
        if node_linked_percent < profile.node_linked_min:
            diags.append(
                Diagnostic(
                    code="IWP109",
                    message=(
                        f"[{profile.name}] NodeLinked% below threshold: "
                        f"{node_linked_percent:.2f} < {profile.node_linked_min:.2f}"
                    ),
                    file_path=config.iwp_root,
                    severity=profile.missing_severity,
                )
            )
        if critical_linked_percent < profile.critical_linked_min:
            diags.append(
                Diagnostic(
                    code="IWP109",
                    message=(
                        f"[{profile.name}] CriticalNodeLinked% below threshold: "
                        f"{critical_linked_percent:.2f} < {profile.critical_linked_min:.2f}"
                    ),
                    file_path=config.iwp_root,
                    severity=profile.missing_severity,
                )
            )
    return diags


def resolve_node_profiles(
    nodes: list[MarkdownNode], profiles: list[CoverageProfile]
) -> dict[NodeKey, CoverageProfile | None]:
    out: dict[NodeKey, CoverageProfile | None] = {}
    for node in nodes:
        key = (node.source_path, node.node_id)
        out[key] = first_matching_profile(node, profiles)
    return out


def first_matching_profile(
    node: MarkdownNode, profiles: list[CoverageProfile]
) -> CoverageProfile | None:
    for profile in profiles:
        if profile.file_type_ids and node.file_type_id not in set(profile.file_type_ids):
            continue
        if profile.section_keys and node.section_key not in set(profile.section_keys):
            continue
        if profile.anchor_levels and node.anchor_level not in set(profile.anchor_levels):
            continue
        if profile.computed_kind_prefixes and not any(
            node.computed_kind.startswith(prefix) for prefix in profile.computed_kind_prefixes
        ):
            continue
        return profile
    return None


def profile_breakdown(
    nodes: list[MarkdownNode],
    linked_node_keys: set[NodeKey],
    profile_by_node: dict[NodeKey, CoverageProfile | None],
) -> dict[str, dict[str, float | int | str]]:
    by_profile: dict[str, dict[str, int]] = {}
    for node in nodes:
        key = (node.source_path, node.node_id)
        profile = profile_by_node.get(key)
        profile_name = profile.name if profile is not None else "default"
        slot = by_profile.setdefault(
            profile_name,
            {"total_nodes": 0, "linked_nodes": 0, "critical_nodes": 0, "linked_critical_nodes": 0},
        )
        slot["total_nodes"] += 1
        if key in linked_node_keys:
            slot["linked_nodes"] += 1
        if node.is_critical:
            slot["critical_nodes"] += 1
            if key in linked_node_keys:
                slot["linked_critical_nodes"] += 1
    report: dict[str, dict[str, float | int | str]] = {}
    for profile_name, stats in by_profile.items():
        total = stats["total_nodes"]
        critical_total = stats["critical_nodes"]
        report[profile_name] = {
            "profile": profile_name,
            "total_nodes": total,
            "linked_nodes": stats["linked_nodes"],
            "critical_nodes": critical_total,
            "linked_critical_nodes": stats["linked_critical_nodes"],
            "node_linked_percent": pct(stats["linked_nodes"], total),
            "critical_linked_percent": pct(stats["linked_critical_nodes"], critical_total),
        }
    return report


def kind_breakdown(valid_link_reports: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in valid_link_reports:
        key = str(item["computed_kind"])
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def pct(num: int, den: int) -> float:
    if den == 0:
        return 100.0
    return round((num / den) * 100, 2)
