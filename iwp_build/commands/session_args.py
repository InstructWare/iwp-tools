from __future__ import annotations

import argparse

from .common_args import add_session_node_filter_args


def add_session_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    session_cmd = subparsers.add_parser("session", help="Manage snapshot sessions")
    session_sub = session_cmd.add_subparsers(dest="session_action", required=True)

    session_start_cmd = session_sub.add_parser("start", help="Start a new session")
    session_start_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_start_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_start_cmd.add_argument(
        "--if-missing",
        action="store_true",
        help="Reuse current open session when present; otherwise start a new session",
    )
    session_start_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_start_cmd.set_defaults(command="session", session_action="start")

    session_current_cmd = session_sub.add_parser("current", help="Show current open session")
    session_current_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_current_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_current_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_current_cmd.set_defaults(command="session", session_action="current")

    session_diff_cmd = session_sub.add_parser("diff", help="Compute diff against session baseline")
    session_diff_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_diff_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_diff_cmd.add_argument(
        "--session-id",
        default=None,
        help="Session id; if omitted, falls back to current open session",
    )
    session_diff_cmd.add_argument(
        "--code-diff-level",
        choices=["summary", "hunk"],
        default=None,
        help="Code diff detail level (default from config)",
    )
    session_diff_cmd.add_argument(
        "--code-diff-context-lines",
        type=int,
        default=None,
        help="Context lines per code diff hunk (hunk mode only)",
    )
    session_diff_cmd.add_argument(
        "--code-diff-max-chars",
        type=int,
        default=None,
        help="Maximum total chars for code diff hunks (hunk mode only)",
    )
    add_session_node_filter_args(session_diff_cmd)
    session_diff_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_diff_cmd.add_argument(
        "--format",
        choices=["text", "json", "both"],
        default=None,
        help="Output format for session diff result",
    )
    session_diff_cmd.add_argument(
        "--auto-start-session",
        action="store_true",
        help="Auto-start a session when no open session exists for diff",
    )
    session_diff_cmd.add_argument(
        "--debug-raw",
        action="store_true",
        help="Include raw verbose payload fields for debugging",
    )
    session_diff_cmd.add_argument(
        "--include-baseline-gaps",
        action="store_true",
        help="Include baseline uncovered-node gap summary in session diff output",
    )
    session_diff_cmd.add_argument(
        "--focus-path",
        default=None,
        help="Filter baseline gap summary by markdown source path prefix",
    )
    session_diff_cmd.add_argument(
        "--max-gap-items",
        type=int,
        default=None,
        help="Maximum uncovered source_path::node_id pairs to include in baseline gap summary",
    )
    session_diff_cmd.set_defaults(command="session", session_action="diff")

    session_commit_cmd = session_sub.add_parser(
        "commit",
        help="Run gate and atomically advance baseline (the only baseline writer)",
    )
    session_commit_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_commit_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_commit_cmd.add_argument(
        "--session-id",
        default=None,
        help="Session id; if omitted, falls back to current open session",
    )
    session_commit_cmd.add_argument(
        "--evidence-json",
        default=None,
        help="Write structured pre-commit evidence JSON to path",
    )
    session_commit_cmd.add_argument(
        "--allow-stale-sidecar",
        action="store_true",
        help="Allow commit even when code sidecar freshness check fails",
    )
    session_commit_cmd.add_argument(
        "--code-diff-level",
        choices=["summary", "hunk"],
        default=None,
        help="Code diff detail level used to freeze pre-commit evidence",
    )
    session_commit_cmd.add_argument(
        "--code-diff-context-lines",
        type=int,
        default=None,
        help="Context lines per code diff hunk (hunk mode only)",
    )
    session_commit_cmd.add_argument(
        "--code-diff-max-chars",
        type=int,
        default=None,
        help="Maximum total chars for code diff hunks (hunk mode only)",
    )
    add_session_node_filter_args(session_commit_cmd)
    session_commit_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_commit_cmd.set_defaults(command="session", session_action="commit")

    session_reconcile_cmd = session_sub.add_parser(
        "reconcile",
        help="Run diff + optional normalize + gate and return commit readiness",
    )
    session_reconcile_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_reconcile_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_reconcile_cmd.add_argument(
        "--session-id",
        default=None,
        help="Session id; if omitted, falls back to current open session",
    )
    session_reconcile_cmd.add_argument(
        "--normalize-links",
        action="store_true",
        help="Normalize @iwp.link annotations before gate checks",
    )
    session_reconcile_cmd.add_argument(
        "--code-diff-level",
        choices=["summary", "hunk"],
        default=None,
        help="Code diff detail level (default from config)",
    )
    session_reconcile_cmd.add_argument(
        "--code-diff-context-lines",
        type=int,
        default=None,
        help="Context lines per code diff hunk (hunk mode only)",
    )
    session_reconcile_cmd.add_argument(
        "--code-diff-max-chars",
        type=int,
        default=None,
        help="Maximum total chars for code diff hunks (hunk mode only)",
    )
    add_session_node_filter_args(session_reconcile_cmd)
    session_reconcile_cmd.add_argument("--json", help="Write reconcile payload JSON to path", default=None)
    session_reconcile_cmd.add_argument(
        "--format",
        choices=["text", "json", "both"],
        default=None,
        help="Output format for reconcile result",
    )
    session_reconcile_cmd.add_argument(
        "--auto-start-session",
        action="store_true",
        help="Auto-start a session when no open session exists for reconcile",
    )
    session_reconcile_cmd.add_argument(
        "--debug-raw",
        action="store_true",
        help="Include raw verbose payload fields for debugging",
    )
    session_reconcile_cmd.add_argument(
        "--max-diagnostics",
        type=int,
        default=None,
        help="Maximum blocking diagnostics items to include in reconcile payload",
    )
    session_reconcile_cmd.add_argument(
        "--min-severity",
        choices=["warning", "error"],
        default=None,
        help="Minimum severity to include in reconcile diagnostics output",
    )
    session_reconcile_cmd.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Hide warning-level diagnostics in reconcile diagnostics output",
    )
    session_reconcile_cmd.add_argument(
        "--suggest-fixes",
        action="store_true",
        help="Enable heuristic fix suggestions in reconcile payload",
    )
    session_reconcile_cmd.add_argument(
        "--warning-top-n",
        type=int,
        default=None,
        help="Top N warning diagnostics to summarize (default from config, usually 2)",
    )
    session_reconcile_cmd.add_argument(
        "--auto-build-sidecar",
        action="store_true",
        help="Auto-refresh code sidecar when stale before final reconcile decision",
    )
    session_reconcile_cmd.set_defaults(command="session", session_action="reconcile")

    session_normalize_cmd = session_sub.add_parser(
        "normalize-links",
        help="Normalize @iwp.link annotations within iwp-build session flow",
    )
    session_normalize_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_normalize_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_normalize_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_normalize_cmd.set_defaults(command="session", session_action="normalize_links")

    session_audit_cmd = session_sub.add_parser("audit", help="Read session audit events")
    session_audit_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    session_audit_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    session_audit_cmd.add_argument("--session-id", required=True, help="Session id created by session start")
    session_audit_cmd.add_argument("--json", help="Write session payload JSON to path", default=None)
    session_audit_cmd.set_defaults(command="session", session_action="audit")
