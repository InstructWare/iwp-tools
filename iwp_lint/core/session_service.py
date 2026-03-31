from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, cast

from ..config import LintConfig, resolve_schema_source
from ..core.coverage_policy import first_matching_profile
from ..core.engine import run_full
from ..core.models import MarkdownNode
from ..parsers.md_parser import parse_markdown_nodes
from ..vcs.diff_resolver import DiffResult, impacted_nodes
from ..vcs.snapshot_diff import (
    CodeDiffOptions,
    compute_code_change_details,
    compute_diff_against_snapshot,
)
from ..vcs.snapshot_store import SnapshotFile, SnapshotStore, collect_workspace_files
from .node_catalog import (
    compile_node_context,
    verify_code_sidecar_freshness_context,
    verify_compiled_context,
)


class SessionService:
    def __init__(self, config: LintConfig) -> None:
        self._config = config
        self._db_path = (config.project_root / config.snapshot_db_file).resolve()
        self._store = SnapshotStore(self._db_path)

    def start(
        self,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
        *,
        allow_custom_id: bool = False,
    ) -> dict[str, Any]:
        if session_id is not None and not allow_custom_id:
            raise RuntimeError(
                "custom session_id is not allowed for session start in current workflow"
            )
        current_open = self._latest_active_session()
        if current_open is not None:
            commands = self._next_step_commands()
            command_lines = "\n".join(f"- {item}" for item in commands)
            raise RuntimeError(
                "open session already exists: "
                f"{current_open.get('session_id')}\n"
                "next steps:\n"
                f"{command_lines}"
            )
        resolved_session_id = session_id or f"s.{uuid.uuid4().hex[:12]}"
        if self._store.get_session(resolved_session_id) is not None:
            raise RuntimeError(f"session already exists: {resolved_session_id}")
        baseline_id = self._store.latest_snapshot_id()
        self._store.create_session(
            session_id=resolved_session_id,
            baseline_id_before=baseline_id,
            metadata=metadata,
        )
        self._store.append_session_event(
            resolved_session_id,
            "session_started",
            {"baseline_id_before": baseline_id},
        )
        return {
            "session_id": resolved_session_id,
            "baseline_id_before": baseline_id,
            "snapshot_db_path": self._db_path.as_posix(),
            "status": "open",
        }

    def current(self) -> dict[str, Any]:
        current_open = self._latest_active_session()
        if current_open is None:
            return {"has_open_session": False, "session": None}
        return {"has_open_session": True, "session": current_open}

    def diff(
        self,
        session_id: str,
        *,
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
        session = self._require_session(session_id)
        baseline_id = session.get("baseline_id_before")
        if baseline_id is None:
            previous: dict[str, SnapshotFile] = {}
        else:
            previous = self._store.load_snapshot(int(cast(int, baseline_id)))
        current_files = collect_workspace_files(
            project_root=self._config.project_root,
            iwp_root=self._config.iwp_root,
            iwp_root_path=self._config.iwp_root_path,
            code_roots=self._config.code_roots,
            include_ext=self._config.snapshot_include_ext,
            code_exclude_globs=self._config.snapshot_exclude_globs,
            exclude_markdown_globs=self._config.schema_exclude_markdown_globs,
        )
        current = {item.path: item for item in current_files}
        changed_files, changed_lines_by_file = compute_diff_against_snapshot(previous, current)
        diff = DiffResult(changed_files=changed_files, changed_lines_by_file=changed_lines_by_file)
        nodes = self._compute_impacted_nodes(diff)
        changed_md_files = sorted(
            {
                Path(item).relative_to(self._config.iwp_root).as_posix()
                for item in changed_files
                if item.startswith(f"{self._config.iwp_root}/") and item.endswith(".md")
            }
        )
        changed_code_files = sorted(
            {
                item
                for item in changed_files
                if Path(item).suffix in set(self._config.protocol_include_ext)
            }
        )
        resolved_level = (code_diff_level or self._config.session.code_diff_level).strip().lower()
        if resolved_level not in {"summary", "hunk"}:
            resolved_level = "summary"
        resolved_level_literal = cast(Literal["summary", "hunk"], resolved_level)
        resolved_context = (
            int(code_diff_context_lines)
            if code_diff_context_lines is not None
            else int(self._config.session.code_diff_context_lines)
        )
        resolved_max_chars = (
            int(code_diff_max_chars)
            if code_diff_max_chars is not None
            else int(self._config.session.code_diff_max_chars)
        )
        code_change_details = compute_code_change_details(
            previous,
            current,
            include_ext=self._config.protocol_include_ext,
            options=CodeDiffOptions(
                level=resolved_level_literal,
                context_lines=max(0, resolved_context),
                max_chars=max(0, resolved_max_chars),
            ),
        )
        resolved_node_severity = (
            str(node_severity or getattr(self._config.session, "diff_node_severity", "all"))
            .strip()
            .lower()
        )
        if resolved_node_severity not in {"all", "error", "warning"}:
            resolved_node_severity = "all"
        resolved_node_file_types = self._normalize_filter_values(node_file_types)
        resolved_node_anchor_levels = self._normalize_filter_values(node_anchor_levels)
        resolved_node_kind_prefixes = self._normalize_filter_values(node_kind_prefixes)
        resolved_excerpt_max_chars = (
            int(markdown_excerpt_max_chars)
            if markdown_excerpt_max_chars is not None
            else int(getattr(self._config.session, "markdown_excerpt_max_chars", 240))
        )
        filtered_nodes = self._filter_impacted_nodes(
            nodes=nodes,
            node_severity=resolved_node_severity,
            node_file_types=resolved_node_file_types,
            node_anchor_levels=resolved_node_anchor_levels,
            node_kind_prefixes=resolved_node_kind_prefixes,
            critical_only=bool(critical_only),
        )
        markdown_change_blocks = self._build_markdown_change_blocks(
            previous=previous,
            current=current,
            changed_md_files=changed_md_files,
            nodes=filtered_nodes,
        )
        markdown_change_text = self._render_markdown_change_text(markdown_change_blocks)
        link_targets = sorted({f"{node.source_path}::{node.node_id}" for node in filtered_nodes})
        density_signals = self._compute_link_density_signals(changed_code_files)
        payload = {
            "meta": {
                "protocol_block": "IWP_DIFF_V1",
                "mode": "diagnostic",
                "schema_version": "iwp.session.diff.v1",
            },
            "session_id": session_id,
            "baseline_id_before": session.get("baseline_id_before"),
            "baseline_id_after": session.get("baseline_id_after"),
            "changed_files": sorted(changed_files),
            "changed_md_files": changed_md_files,
            "changed_code_files": changed_code_files,
            "changed_code_details": code_change_details,
            "changed_count": len(changed_files),
            "impacted_nodes": [
                self._serialize_impacted_node(node, excerpt_max_chars=resolved_excerpt_max_chars)
                for node in filtered_nodes
            ],
            "markdown_change_blocks": markdown_change_blocks,
            "markdown_change_text": markdown_change_text,
            "link_targets_suggested": link_targets,
            "link_density_signals": density_signals,
            "code_diff_level": resolved_level_literal,
            "session_status": session.get("status"),
            "filters_applied": {
                "node_severity": resolved_node_severity,
                "node_file_types": sorted(resolved_node_file_types),
                "node_anchor_levels": sorted(resolved_node_anchor_levels),
                "node_kind_prefixes": sorted(resolved_node_kind_prefixes),
                "critical_only": bool(critical_only),
                "markdown_excerpt_max_chars": max(0, resolved_excerpt_max_chars),
            },
        }
        if include_baseline_gaps:
            payload["baseline_gap_summary"] = self._build_baseline_gap_summary(
                changed_md_files=changed_md_files,
                focus_path=focus_path,
                max_items=max_gap_items,
            )
        next_status = "dirty" if len(changed_files) > 0 else "open"
        if str(session.get("status")) != "committed":
            self._store.update_session(session_id, status=next_status)
            payload["session_status"] = next_status
        self._store.append_session_event(
            session_id,
            "session_diff",
            {
                "changed_count": len(changed_files),
                "impacted_nodes_count": len(filtered_nodes),
                "impacted_nodes_total_count": len(nodes),
                "changed_md_count": len(changed_md_files),
                "changed_code_count": len(changed_code_files),
                "code_diff_level": resolved_level_literal,
                "session_status_after": payload.get("session_status"),
            },
        )
        return payload

    def _build_baseline_gap_summary(
        self,
        *,
        changed_md_files: list[str],
        focus_path: str | None,
        max_items: int | None,
    ) -> dict[str, object]:
        full_report = run_full(self._config)
        diagnostics = full_report.get("diagnostics", [])
        if not isinstance(diagnostics, list):
            diagnostics = []
        normalized_focus = str(focus_path or "").strip().strip("/")
        scope_paths = set(changed_md_files)
        if normalized_focus:
            filtered = [
                item
                for item in diagnostics
                if isinstance(item, dict)
                and str(item.get("file_path", "")).strip().startswith(normalized_focus)
            ]
        elif scope_paths:
            filtered = [
                item
                for item in diagnostics
                if isinstance(item, dict) and str(item.get("file_path", "")) in scope_paths
            ]
        else:
            filtered = [item for item in diagnostics if isinstance(item, dict)]

        resolved_max_items = (
            int(max_items)
            if max_items is not None
            else int(getattr(self._config.session, "baseline_gap_max_items", 20))
        )
        if resolved_max_items < 1:
            resolved_max_items = 1

        top_uncovered_pairs: list[str] = []
        seen_pairs: set[str] = set()
        for item in filtered:
            code = str(item.get("code", ""))
            if code not in {"IWP107", "IWP108"}:
                continue
            file_path = str(item.get("file_path", "")).strip()
            message = str(item.get("message", ""))
            node_id = self._extract_node_id_from_message(message)
            if not file_path or not node_id:
                continue
            pair = f"{file_path}::{node_id}"
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            top_uncovered_pairs.append(pair)
            if len(top_uncovered_pairs) >= resolved_max_items:
                break

        total_errors = len(
            [item for item in filtered if str(item.get("severity", "error")).lower() == "error"]
        )
        total_warnings = max(0, len(filtered) - total_errors)
        return {
            "scope": {
                "focus_path": normalized_focus or None,
                "changed_md_files": changed_md_files,
            },
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "top_uncovered_pairs": top_uncovered_pairs,
        }

    @staticmethod
    def _extract_node_id_from_message(message: str) -> str:
        matched = re.search(r"(n\\.[a-zA-Z0-9]+)", message)
        if matched is None:
            return ""
        return matched.group(1)

    def run_gate_suite(self) -> dict[str, Any]:
        compile_node_context(config=self._config)
        compiled = verify_compiled_context(config=self._config)
        lint_report = run_full(self._config)
        lint_exit_code = 1 if int(lint_report["summary"].get("error_count", 0)) > 0 else 0
        checked_at = datetime.now(timezone.utc).isoformat()
        status = "OK"
        blocked_by: list[str] = []
        if not bool(compiled.get("ok", False)):
            status = "FAIL"
            blocked_by.append("compiled")
        if lint_exit_code != 0:
            status = "FAIL"
            blocked_by.append("lint")
        elif int(lint_report["summary"].get("warning_count", 0)) > 0 and status != "FAIL":
            status = "PASS_WITH_WARNINGS"
        return {
            "status": status,
            "blocked_by": blocked_by,
            "compiled": compiled,
            "compiled_ok": bool(compiled.get("ok", False)),
            "compiled_checked_at": checked_at,
            "lint_report": lint_report,
            "lint_exit_code": lint_exit_code,
        }

    def gate(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        if str(session.get("status")) == "committed":
            raise RuntimeError(f"session already committed: {session_id}")
        gate = self.run_gate_suite()
        gate_status = str(gate.get("status", "FAIL"))
        blocked_by = [str(item) for item in gate.get("blocked_by", [])]
        next_status = "verified" if gate_status != "FAIL" else "blocked"
        self._store.update_session(session_id, status=next_status)
        self._store.append_session_event(
            session_id,
            "session_gate",
            {
                "gate_status": gate_status,
                "blocked_by": blocked_by,
                "compiled_ok": bool(gate.get("compiled", {}).get("ok", False)),
                "lint_errors": gate.get("lint_report", {}).get("summary", {}).get("error_count", 0),
            },
        )
        return {
            "session_id": session_id,
            "status": next_status,
            "gate_status": gate_status,
            "blocked_by": blocked_by,
            "compiled": gate.get("compiled", {}),
            "lint_report": gate.get("lint_report", {}),
        }

    def commit(
        self,
        session_id: str,
        *,
        enforce_gate: bool = True,
        allow_stale_sidecar: bool = False,
        message: str | None = None,
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
        session = self._require_session(session_id)
        session_status = str(session.get("status"))
        if session_status == "committed":
            return {
                "session_id": session_id,
                "status": "committed",
                "gate_status": "OK",
                "baseline_id_before": session.get("baseline_id_before"),
                "baseline_id_after": session.get("baseline_id_after"),
                "message": "session already committed",
            }
        pre_commit_intent = self.diff(
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
        )
        gate_status = "SKIPPED"
        blocked_by: list[str] = []
        gate_payload: dict[str, Any] | None = None
        sidecar_freshness = verify_code_sidecar_freshness_context(config=self._config)
        sidecar_fresh = bool(sidecar_freshness.get("fresh", False))
        sidecar_compiled_at = sidecar_freshness.get("compiled_at")
        sidecar_compiled_from_baseline = sidecar_freshness.get("compiled_from_baseline_id")
        sidecar_stale_reasons = sidecar_freshness.get("stale_reasons", [])
        if enforce_gate:
            gate_payload = self.gate(session_id)
            gate_status = str(gate_payload["gate_status"])
            blocked_by = [str(item) for item in gate_payload.get("blocked_by", [])]
            if gate_status == "FAIL":
                self._store.append_session_event(
                    session_id,
                    "session_commit_blocked",
                    {
                        "blocked_by": blocked_by,
                        "lint_errors": gate_payload.get("lint_report", {})
                        .get("summary", {})
                        .get("error_count", 0),
                        "compiled_ok": bool(gate_payload.get("compiled", {}).get("ok", False)),
                    },
                )
                return {
                    "session_id": session_id,
                    "status": "blocked",
                    "gate_status": gate_status,
                    "blocked_by": blocked_by,
                    "baseline_id_before": session.get("baseline_id_before"),
                    "baseline_id_after": None,
                    "sidecar_fresh": sidecar_fresh,
                    "compiled_at": sidecar_compiled_at,
                    "compiled_from_baseline_id": sidecar_compiled_from_baseline,
                    "sidecar_stale_reasons": sidecar_stale_reasons,
                    "evidence": self._build_commit_evidence(
                        session_id=session_id,
                        intent_diff=pre_commit_intent,
                        gate_payload=gate_payload,
                        commit_payload=None,
                        sidecar_freshness=sidecar_freshness,
                    )
                    if include_evidence
                    else None,
                }
            session_status = "verified"
        if not allow_stale_sidecar and not sidecar_fresh:
            blocked_by_with_sidecar = list(blocked_by)
            if "code_sidecar" not in blocked_by_with_sidecar:
                blocked_by_with_sidecar.append("code_sidecar")
            self._store.append_session_event(
                session_id,
                "session_commit_blocked",
                {
                    "blocked_by": blocked_by_with_sidecar,
                    "reason": "stale_code_sidecar",
                    "sidecar_stale_reasons": sidecar_stale_reasons,
                },
            )
            return {
                "session_id": session_id,
                "status": "blocked",
                "gate_status": "FAIL",
                "blocked_by": blocked_by_with_sidecar,
                "baseline_id_before": session.get("baseline_id_before"),
                "baseline_id_after": None,
                "sidecar_fresh": False,
                "compiled_at": sidecar_compiled_at,
                "compiled_from_baseline_id": sidecar_compiled_from_baseline,
                "sidecar_stale_reasons": sidecar_stale_reasons,
                "evidence": self._build_commit_evidence(
                    session_id=session_id,
                    intent_diff=pre_commit_intent,
                    gate_payload=gate_payload,
                    commit_payload=None,
                    sidecar_freshness=sidecar_freshness,
                )
                if include_evidence
                else None,
            }
        if not enforce_gate and session_status not in {"verified", "open", "dirty", "blocked"}:
            raise RuntimeError(
                f"session state does not allow commit: {session_status}; run `iwp-build session diff` first"
            )
        files = collect_workspace_files(
            project_root=self._config.project_root,
            iwp_root=self._config.iwp_root,
            iwp_root_path=self._config.iwp_root_path,
            code_roots=self._config.code_roots,
            include_ext=self._config.snapshot_include_ext,
            code_exclude_globs=self._config.snapshot_exclude_globs,
            exclude_markdown_globs=self._config.schema_exclude_markdown_globs,
        )
        commit_message = (message or "").strip() or "session commit baseline checkpoint"
        baseline_after = self._store.create_snapshot(files)
        checkpoint_id = self._store.create_checkpoint(
            snapshot_id=baseline_after,
            source="session_commit",
            session_id=session_id,
            baseline_snapshot_id=(
                cast(int, session["baseline_id_before"])
                if session.get("baseline_id_before") is not None
                else None
            ),
            gate_status=("pass" if gate_status != "FAIL" else "fail"),
            message=commit_message,
            metadata={"status": "committed"},
        )
        self._store.update_session(
            session_id,
            status="committed",
            baseline_id_after=baseline_after,
            committed=True,
        )
        self._store.append_session_event(
            session_id,
            "session_committed",
            {
                "baseline_id_after": baseline_after,
                "file_count": len(files),
                "checkpoint_id": checkpoint_id,
                "message": commit_message,
            },
        )
        commit_payload = {
            "session_id": session_id,
            "status": "committed",
            "gate_status": gate_status,
            "baseline_id_before": session.get("baseline_id_before"),
            "baseline_id_after": baseline_after,
            "checkpoint_id": checkpoint_id,
            "checkpoint_message": commit_message,
            "file_count": len(files),
            "sidecar_fresh": sidecar_fresh,
            "compiled_at": sidecar_compiled_at,
            "compiled_from_baseline_id": sidecar_compiled_from_baseline,
            "sidecar_stale_reasons": sidecar_stale_reasons,
        }
        if include_evidence:
            commit_result = dict(commit_payload)
            commit_payload["evidence"] = self._build_commit_evidence(
                session_id=session_id,
                intent_diff=pre_commit_intent,
                gate_payload=gate_payload,
                commit_payload=commit_result,
                sidecar_freshness=sidecar_freshness,
            )
        return commit_payload

    def audit(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        events = self._store.get_session_events(session_id)
        return {"session": session, "events": events}

    def _require_session(self, session_id: str) -> dict[str, object]:
        session = self._store.get_session(session_id)
        if session is None:
            raise RuntimeError(f"session not found: {session_id}")
        return session

    def _latest_active_session(self) -> dict[str, object] | None:
        active_statuses = ("open", "dirty", "verified", "blocked")
        latest: dict[str, object] | None = None
        latest_created_at = ""
        for status in active_statuses:
            candidate = self._store.latest_session(status=status)
            if candidate is None:
                continue
            created_at = str(candidate.get("created_at", ""))
            if created_at >= latest_created_at:
                latest = candidate
                latest_created_at = created_at
        return latest

    def _compute_impacted_nodes(self, diff: DiffResult):
        schema_path = resolve_schema_source(self._config)
        nodes = parse_markdown_nodes(
            self._config.iwp_root_path,
            self._config.critical_node_patterns,
            schema_path,
            critical_granularity=self._config.critical_granularity,
            exclude_markdown_globs=self._config.schema_exclude_markdown_globs,
            node_registry_file=self._config.node_registry_file,
            node_id_min_length=self._config.node_id_min_length,
            page_only_enabled=self._config.page_only.enabled,
            authoring_tokens_enabled=self._config.authoring.tokens.enabled,
            node_generation_mode=self._config.authoring.node_generation_mode,
        )
        return impacted_nodes(nodes, diff)

    def _filter_impacted_nodes(
        self,
        *,
        nodes: list[MarkdownNode],
        node_severity: str,
        node_file_types: set[str],
        node_anchor_levels: set[str],
        node_kind_prefixes: set[str],
        critical_only: bool,
    ) -> list[MarkdownNode]:
        filtered: list[MarkdownNode] = []
        for node in nodes:
            if critical_only and not node.is_critical:
                continue
            if node_file_types and node.file_type_id not in node_file_types:
                continue
            if node_anchor_levels and node.anchor_level not in node_anchor_levels:
                continue
            if node_kind_prefixes and not any(
                node.computed_kind.startswith(prefix) for prefix in node_kind_prefixes
            ):
                continue
            severity = self._node_missing_severity(node)
            if node_severity != "all" and severity != node_severity:
                continue
            filtered.append(node)
        return filtered

    def _node_missing_severity(self, node: MarkdownNode) -> str:
        profile = first_matching_profile(node, self._config.coverage_profiles)
        if profile is None:
            return "error"
        severity = str(profile.missing_severity).strip().lower()
        return severity if severity in {"error", "warning"} else "error"

    def _serialize_impacted_node(
        self,
        node: MarkdownNode,
        *,
        excerpt_max_chars: int,
    ) -> dict[str, object]:
        payload = node.to_dict()
        payload["missing_severity"] = self._node_missing_severity(node)
        payload["block_text_excerpt"] = self._markdown_excerpt(
            source_path=node.source_path,
            line_start=node.line_start,
            line_end=node.line_end,
            max_chars=max(0, excerpt_max_chars),
        )
        return payload

    def _markdown_excerpt(
        self,
        *,
        source_path: str,
        line_start: int,
        line_end: int,
        max_chars: int,
    ) -> str:
        file_path = (self._config.iwp_root_path / source_path).resolve()
        if not file_path.exists():
            return ""
        lines = file_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return ""
        start_idx = max(0, line_start - 1)
        end_idx = min(len(lines), max(line_end, line_start))
        excerpt = "\n".join(lines[start_idx:end_idx]).strip()
        if max_chars <= 0 or len(excerpt) <= max_chars:
            return excerpt
        if max_chars <= 3:
            return excerpt[:max_chars]
        return f"{excerpt[: max_chars - 3]}..."

    @staticmethod
    def _normalize_filter_values(values: list[str] | None) -> set[str]:
        if not values:
            return set()
        return {str(item).strip() for item in values if str(item).strip()}

    def _compute_link_density_signals(
        self, changed_code_files: list[str]
    ) -> list[dict[str, object]]:
        if not changed_code_files:
            return []
        signals: list[dict[str, object]] = []
        threshold = float(getattr(self._config.session, "link_density_threshold", 0.25))
        for rel_path in changed_code_files:
            abs_path = (self._config.project_root / rel_path).resolve()
            if not abs_path.exists():
                continue
            text = abs_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            total_lines = max(1, len(lines))
            link_lines = sum(1 for line in lines if "@iwp.link" in line)
            density = link_lines / float(total_lines)
            if density >= threshold:
                signals.append(
                    {
                        "file_path": rel_path,
                        "link_line_count": link_lines,
                        "total_line_count": total_lines,
                        "link_density": round(density, 4),
                        "severity": "warning",
                        "message": "link density is unusually high in this changed file",
                    }
                )
        return signals

    def _build_commit_evidence(
        self,
        *,
        session_id: str,
        intent_diff: dict[str, Any],
        gate_payload: dict[str, Any] | None,
        commit_payload: dict[str, Any] | None,
        sidecar_freshness: dict[str, Any],
    ) -> dict[str, object]:
        gate_data = gate_payload or {}
        diagnostics = gate_data.get("lint_report", {}).get("diagnostics", [])
        return {
            "schema_version": "iwp.session.evidence.v1",
            "session_id": session_id,
            "intent_diff": intent_diff,
            "gate_result": {
                "gate_status": gate_data.get("gate_status", "SKIPPED"),
                "status": gate_data.get("status", "unknown"),
                "blocked_by": gate_data.get("blocked_by", []),
                "compiled": gate_data.get("compiled", {}),
                "sidecar_freshness": sidecar_freshness,
                "lint_summary": gate_data.get("lint_report", {}).get("summary", {}),
                "lint_diagnostics": diagnostics if isinstance(diagnostics, list) else [],
            },
            "commit_result": commit_payload
            or {
                "status": "blocked",
                "baseline_id_before": intent_diff.get("baseline_id_before"),
                "baseline_id_after": None,
            },
            "link_evidence": {
                "link_targets_suggested": intent_diff.get("link_targets_suggested", []),
                "link_density_signals": intent_diff.get("link_density_signals", []),
            },
        }

    @staticmethod
    def _next_step_commands() -> list[str]:
        return [
            "iwp-build session current --config <cfg>",
            "iwp-build session diff --config <cfg> --preset agent-default",
            "iwp-build session reconcile --config <cfg> --preset agent-default",
            "iwp-build session commit --config <cfg> --preset ci-strict",
        ]

    def _build_markdown_change_blocks(
        self,
        *,
        previous: dict[str, SnapshotFile],
        current: dict[str, SnapshotFile],
        changed_md_files: list[str],
        nodes: list[MarkdownNode],
    ) -> list[dict[str, object]]:
        by_source: dict[str, list[MarkdownNode]] = {}
        for node in nodes:
            by_source.setdefault(node.source_path, []).append(node)

        blocks: list[dict[str, object]] = []
        for source_path in changed_md_files:
            workspace_path = f"{self._config.iwp_root}/{source_path}"
            prev = previous.get(workspace_path)
            cur = current.get(workspace_path)
            old_lines = (prev.content or "").splitlines() if prev is not None else []
            new_lines = (cur.content or "").splitlines() if cur is not None else []
            file_nodes = by_source.get(source_path, [])
            ops: list[dict[str, object]] = []
            matcher = SequenceMatcher(None, old_lines, new_lines)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                if tag == "insert":
                    for idx in range(j1, j2):
                        line_no = idx + 1
                        ops.append(
                            {
                                "op": "+",
                                "line": line_no,
                                "node_id": self._resolve_node_id(file_nodes, line_no),
                                "text": new_lines[idx],
                            }
                        )
                    continue
                if tag == "delete":
                    for idx in range(i1, i2):
                        line_no = idx + 1
                        ops.append(
                            {
                                "op": "-",
                                "line": line_no,
                                "node_id": self._resolve_node_id(file_nodes, line_no),
                                "text": old_lines[idx],
                            }
                        )
                    continue
                overlap = min(i2 - i1, j2 - j1)
                for offset in range(overlap):
                    old_idx = i1 + offset
                    new_idx = j1 + offset
                    line_no = new_idx + 1
                    ops.append(
                        {
                            "op": "~",
                            "line": line_no,
                            "node_id": self._resolve_node_id(file_nodes, line_no),
                            "old_text": old_lines[old_idx],
                            "new_text": new_lines[new_idx],
                        }
                    )
                for old_idx in range(i1 + overlap, i2):
                    line_no = old_idx + 1
                    ops.append(
                        {
                            "op": "-",
                            "line": line_no,
                            "node_id": self._resolve_node_id(file_nodes, line_no),
                            "text": old_lines[old_idx],
                        }
                    )
                for new_idx in range(j1 + overlap, j2):
                    line_no = new_idx + 1
                    ops.append(
                        {
                            "op": "+",
                            "line": line_no,
                            "node_id": self._resolve_node_id(file_nodes, line_no),
                            "text": new_lines[new_idx],
                        }
                    )
            if ops:
                blocks.append({"file": source_path, "ops": ops})
        return blocks

    @staticmethod
    def _resolve_node_id(nodes: list[MarkdownNode], line_no: int) -> str:
        if not nodes:
            return "n/a"
        for node in nodes:
            if node.line_start <= line_no <= node.line_end:
                return node.node_id
        nearest = min(nodes, key=lambda item: abs(item.line_start - line_no))
        return nearest.node_id

    def _render_markdown_change_text(self, blocks: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for block in blocks:
            file_path = str(block.get("file", ""))
            lines.append(f'file:"{file_path}"')
            ops = block.get("ops", [])
            if not isinstance(ops, list):
                continue
            for op in ops:
                if not isinstance(op, dict):
                    continue
                op_kind = str(op.get("op", "+"))
                line_no = int(op.get("line", 0))
                node_id = str(op.get("node_id", "n/a"))
                if op_kind == "~":
                    old_text = str(op.get("old_text", "")).replace('"', '\\"')
                    new_text = str(op.get("new_text", "")).replace('"', '\\"')
                    lines.append(f'~[{line_no}]:{{{node_id}}} "{old_text}" => "{new_text}"')
                else:
                    text = str(op.get("text", "")).replace('"', '\\"')
                    lines.append(f'{op_kind}[{line_no}]:{{{node_id}}} "{text}"')
        return "\n".join(lines)
