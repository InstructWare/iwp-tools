from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .commands.build_args import add_build_parser
from .commands.history_args import add_history_parser
from .commands.option_resolver import (
    HistoryOptions,
    SessionOptions,
    resolve_build_options,
    resolve_history_options,
    resolve_session_options,
    resolve_verify_options,
)
from .commands.session_args import add_session_parser
from .commands.verify_args import add_verify_parser
from .commands.watch_args import add_watch_parser
from .output import render_iwp_diff_text, render_iwp_reconcile_text, safe_len, write_json
from .services.build import run_build
from .services.verify import run_verify
from .services.watch import run_watch

try:
    from iwp_lint.api import (
        compile_context,
        history_checkpoint,
        history_list,
        history_prune,
        history_restore,
        normalize_annotations,
        resolve_session_id,
        session_audit,
        session_commit,
        session_current,
        session_diff,
        session_reconcile,
        session_start,
        verify_compiled,
    )
    from iwp_lint.config import load_config
except ImportError:
    from ..iwp_lint.api import (
        compile_context,
        history_checkpoint,
        history_list,
        history_prune,
        history_restore,
        normalize_annotations,
        resolve_session_id,
        session_audit,
        session_commit,
        session_current,
        session_diff,
        session_reconcile,
        session_start,
        verify_compiled,
    )
    from ..iwp_lint.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iwp-build", description="IWP incremental build orchestrator"
    )
    parser.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    add_build_parser(sub)
    add_verify_parser(sub)
    add_watch_parser(sub)
    add_session_parser(sub)
    add_history_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        config = load_config(args.config)

        if args.command == "build":
            options = resolve_build_options(args, config=config)
            return _run_build(
                config=config,
                mode=options.mode,
                json_path=options.json_path,
                normalize_links=options.normalize_links,
                build_code_sidecar=options.build_code_sidecar,
            )
        if args.command == "verify":
            options = resolve_verify_options(args, config=config)
            return _run_verify(
                config,
                with_tests=options.with_tests,
                protocol_only=options.protocol_only,
                min_severity=options.min_severity,
                quiet_warnings=options.quiet_warnings,
            )
        if args.command == "watch":
            return run_watch(
                config=config,
                config_file=args.config,
                debounce_ms=args.debounce_ms,
                poll_ms=args.poll_ms,
                verify=args.verify,
                run_tests=args.run_tests,
                once=args.once,
                compile_fn=compile_context,
                verify_fn=verify_compiled,
            )
        if args.command == "session":
            options = resolve_session_options(args, config=config)
            return _run_session(config=config, options=options)
        if args.command == "history":
            options = resolve_history_options(args, config=config)
            return _run_history(config=config, options=options)
        raise RuntimeError(f"unknown command: {args.command}")
    except RuntimeError as exc:
        print(f"[iwp-build] error: {exc}")
        return 1


def _run_build(
    config,
    mode: str,
    json_path: str | None,
    normalize_links: bool = False,
    build_code_sidecar: bool = True,
) -> int:
    return run_build(
        config=config,
        mode=mode,
        json_path=json_path,
        normalize_links=normalize_links,
        emit_code_sidecar=build_code_sidecar,
    )


def _run_verify(
    config,
    *,
    with_tests: bool,
    protocol_only: bool = False,
    min_severity: str = "warning",
    quiet_warnings: bool = False,
) -> int:
    return run_verify(
        config=config,
        with_tests=with_tests,
        protocol_only=protocol_only,
        min_severity=min_severity,
        quiet_warnings=quiet_warnings,
    )


@dataclass(frozen=True)
class SessionActionContext:
    config: Any
    options: SessionOptions


@dataclass(frozen=True)
class SessionActionResult:
    payload: dict[str, object]
    exit_code: int | None = None
    skip_post_processing: bool = False


SessionHandler = Callable[[SessionActionContext], SessionActionResult]


@dataclass(frozen=True)
class HistoryActionContext:
    config: Any
    options: HistoryOptions


@dataclass(frozen=True)
class HistoryActionResult:
    payload: dict[str, object]
    exit_code: int | None = None


HistoryHandler = Callable[[HistoryActionContext], HistoryActionResult]


def _run_session(config: Any, options: SessionOptions) -> int:
    action = options.action
    handlers = _build_session_handlers()
    handler = handlers.get(action)
    if handler is None:
        raise RuntimeError(f"unknown session action: {action}")
    result = handler(SessionActionContext(config=config, options=options))
    if result.skip_post_processing:
        return result.exit_code if result.exit_code is not None else 0
    payload = result.payload
    written = None
    should_write_json = bool(options.json_path) or options.output_format in {"json", "both"}
    if should_write_json:
        write_target = options.json_path or f"out/session-{action}.json"
        written = write_json(write_target, payload)
    print(
        "[iwp-build] session "
        f"action={action} "
        f"id={payload.get('session_id', options.session_id)} "
        f"status={payload.get('status', payload.get('session_status', 'n/a'))}"
    )
    if action == "diff":
        if options.output_format in {"text", "both"}:
            max_text_lines = int(getattr(config.session, "max_text_lines", 200))
            print(render_iwp_diff_text(payload, max_lines=max_text_lines))
        print(
            "[iwp-build] session diff "
            f"changed={safe_len(payload.get('changed_files'))} "
            f"changed_md={safe_len(payload.get('changed_md_files'))} "
            f"changed_code={safe_len(payload.get('changed_code_files'))} "
            f"impacted_nodes={safe_len(payload.get('impacted_nodes'))}"
        )
    if action == "reconcile":
        if options.output_format in {"text", "both"}:
            max_hint_items = int(getattr(config.session, "max_hint_items", 20))
            print(render_iwp_reconcile_text(payload, max_hint_items=max_hint_items))
        intent_diff = payload.get("intent_diff")
        intent_diff_dict = intent_diff if isinstance(intent_diff, dict) else {}
        summary = payload.get("summary")
        summary_dict = summary if isinstance(summary, dict) else {}
        print(
            "[iwp-build] session reconcile "
            f"can_commit={bool(payload.get('can_commit', False))} "
            f"changed={safe_len(intent_diff_dict.get('changed_files'))} "
            f"impacted_nodes={summary_dict.get('impacted_nodes_count', 0)}"
        )
    if action == "current":
        has_open = bool(payload.get("has_open_session", False))
        print(f"[iwp-build] session current has_open_session={has_open}")
    if written:
        print(f"[iwp-build] session json path={written}")
    if action == "commit" and str(payload.get("status")) != "committed":
        return 1
    return result.exit_code if result.exit_code is not None else 0


def _run_history(config: Any, options: HistoryOptions) -> int:
    action = options.action
    handlers = _build_history_handlers()
    handler = handlers.get(action)
    if handler is None:
        raise RuntimeError(f"unknown history action: {action}")
    result = handler(HistoryActionContext(config=config, options=options))
    payload = result.payload
    written = None
    if options.json_path:
        written = write_json(options.json_path, payload)
    print(f"[iwp-build] history action={action} status={payload.get('status', 'ok')}")
    if action == "list":
        print(
            "[iwp-build] history list "
            f"count={safe_len(payload.get('checkpoints'))} "
            f"current_baseline_snapshot_id={payload.get('current_baseline_snapshot_id')}"
        )
    if action == "restore":
        plan = payload.get("plan")
        target_id = plan.get("target_checkpoint_id", "n/a") if isinstance(plan, dict) else "n/a"
        print(
            f"[iwp-build] history restore target={target_id} result={payload.get('status', 'n/a')}"
        )
    if action == "prune":
        print(
            "[iwp-build] history prune "
            f"removed={safe_len(payload.get('removed_checkpoint_ids'))} "
            f"kept={safe_len(payload.get('kept_checkpoint_ids'))}"
        )
    if action == "checkpoint":
        print(
            "[iwp-build] history checkpoint "
            f"id={payload.get('checkpoint_id', 'n/a')} "
            f"snapshot_id={payload.get('snapshot_id', 'n/a')}"
        )
    if written:
        print(f"[iwp-build] history json path={written}")
    if action == "restore" and str(payload.get("status")) == "blocked":
        return 1
    return result.exit_code if result.exit_code is not None else 0


def _build_session_handlers() -> dict[str, SessionHandler]:
    return {
        "start": _handle_session_start,
        "current": _handle_session_current,
        "diff": _handle_session_diff,
        "commit": _handle_session_commit,
        "audit": _handle_session_audit,
        "reconcile": _handle_session_reconcile,
        "normalize_links": _handle_session_normalize_links,
    }


def _build_history_handlers() -> dict[str, HistoryHandler]:
    return {
        "list": _handle_history_list,
        "restore": _handle_history_restore,
        "prune": _handle_history_prune,
        "checkpoint": _handle_history_checkpoint,
    }


def _handle_session_start(ctx: SessionActionContext) -> SessionActionResult:
    config = ctx.config
    options = ctx.options
    if options.if_missing:
        current = session_current(config=config)
        if bool(current.get("has_open_session", False)):
            session = current.get("session", {})
            if not isinstance(session, dict):
                raise RuntimeError("current session payload is invalid")
            payload = dict(session)
            payload["reused_current"] = True
            payload["status"] = "reused"
            return SessionActionResult(payload=payload)
        payload = session_start(config=config, metadata={"origin": "iwp-build session start"})
        payload["reused_current"] = False
        return SessionActionResult(payload=payload)
    payload = session_start(config=config, metadata={"origin": "iwp-build session start"})
    return SessionActionResult(payload=payload)


def _handle_session_current(ctx: SessionActionContext) -> SessionActionResult:
    payload = session_current(config=ctx.config)
    return SessionActionResult(payload=payload)


def _handle_session_diff(ctx: SessionActionContext) -> SessionActionResult:
    config = ctx.config
    options = ctx.options
    resolved_session_id, auto_started = resolve_session_id(
        config=config,
        session_id=options.session_id,
        action="diff",
        auto_start_session=options.auto_start_session,
        auto_start_origin="iwp-build session diff auto-start",
    )
    if auto_started:
        print(f"[iwp-build] auto-started session id={resolved_session_id} for action=diff")
    payload = session_diff(
        config=config,
        session_id=resolved_session_id,
        code_diff_level=options.code_diff_level,
        code_diff_context_lines=options.code_diff_context_lines,
        code_diff_max_chars=options.code_diff_max_chars,
        node_severity=options.node_severity,
        node_file_types=options.node_file_type_ids,
        node_anchor_levels=options.node_anchor_levels,
        node_kind_prefixes=options.node_kind_prefixes,
        critical_only=options.critical_only,
        markdown_excerpt_max_chars=options.markdown_excerpt_max_chars,
        include_baseline_gaps=options.include_baseline_gaps,
        focus_path=options.focus_path,
        max_gap_items=options.max_gap_items,
    )
    if options.debug_raw:
        payload["raw"] = dict(payload)
    return SessionActionResult(payload=payload)


def _handle_session_commit(ctx: SessionActionContext) -> SessionActionResult:
    config = ctx.config
    options = ctx.options
    resolved_session_id, _ = resolve_session_id(
        config=config,
        session_id=options.session_id,
        action="commit",
        auto_start_session=False,
    )
    payload = session_commit(
        config=config,
        session_id=resolved_session_id,
        allow_stale_sidecar=options.allow_stale_sidecar,
        message=options.commit_message,
        code_diff_level=options.code_diff_level,
        code_diff_context_lines=options.code_diff_context_lines,
        code_diff_max_chars=options.code_diff_max_chars,
        node_severity=options.node_severity,
        node_file_types=options.node_file_type_ids,
        node_anchor_levels=options.node_anchor_levels,
        node_kind_prefixes=options.node_kind_prefixes,
        critical_only=options.critical_only,
        markdown_excerpt_max_chars=options.markdown_excerpt_max_chars,
        include_evidence=bool(options.evidence_json_path),
    )
    if options.evidence_json_path and isinstance(payload.get("evidence"), dict):
        written_evidence = write_json(options.evidence_json_path, payload["evidence"])
        if written_evidence:
            print(f"[iwp-build] session evidence json path={written_evidence}")
    return SessionActionResult(payload=payload)


def _handle_session_audit(ctx: SessionActionContext) -> SessionActionResult:
    options = ctx.options
    if not options.session_id:
        raise RuntimeError("--session-id is required for session audit")
    payload = session_audit(config=ctx.config, session_id=options.session_id)
    return SessionActionResult(payload=payload)


def _handle_session_reconcile(ctx: SessionActionContext) -> SessionActionResult:
    config = ctx.config
    options = ctx.options
    exit_code, payload = session_reconcile(
        config=config,
        session_id=options.session_id,
        normalize_links=options.normalize_links,
        code_diff_level=options.code_diff_level,
        code_diff_context_lines=options.code_diff_context_lines,
        code_diff_max_chars=options.code_diff_max_chars,
        node_severity=options.node_severity,
        node_file_types=options.node_file_type_ids,
        node_anchor_levels=options.node_anchor_levels,
        node_kind_prefixes=options.node_kind_prefixes,
        critical_only=options.critical_only,
        markdown_excerpt_max_chars=options.markdown_excerpt_max_chars,
        debug_raw=options.debug_raw,
        auto_start_session=options.auto_start_session,
        max_diagnostics=options.max_diagnostics,
        min_severity=options.min_severity,
        quiet_warnings=options.quiet_warnings,
        suggest_fixes=options.suggest_fixes,
        warning_top_n=options.warning_top_n,
        auto_build_sidecar=options.auto_build_sidecar,
    )
    auto_started = payload.get("auto_started_session")
    if isinstance(auto_started, dict):
        started_id = str(auto_started.get("session_id", "")).strip()
        if started_id:
            print(f"[iwp-build] auto-started session id={started_id} for action=reconcile")
    return SessionActionResult(payload=payload, exit_code=exit_code)


def _handle_session_normalize_links(ctx: SessionActionContext) -> SessionActionResult:
    normalize = normalize_annotations(config=ctx.config, write=True)
    payload = {
        "action": "normalize_links",
        "status": "ok",
        "normalize": normalize,
    }
    return SessionActionResult(payload=payload)


def _handle_history_list(ctx: HistoryActionContext) -> HistoryActionResult:
    options = ctx.options
    payload = history_list(
        config=ctx.config,
        limit=options.limit,
        include_stats=True,
    )
    return HistoryActionResult(payload=payload)


def _handle_history_restore(ctx: HistoryActionContext) -> HistoryActionResult:
    options = ctx.options
    if options.to_checkpoint_id is None:
        raise RuntimeError("--to is required for history restore")
    payload = history_restore(
        config=ctx.config,
        to_checkpoint_id=int(options.to_checkpoint_id),
        dry_run=options.dry_run,
        force=options.force,
        actor="iwp-build",
    )
    return HistoryActionResult(payload=payload)


def _handle_history_prune(ctx: HistoryActionContext) -> HistoryActionResult:
    options = ctx.options
    payload = history_prune(
        config=ctx.config,
        max_snapshots=options.max_snapshots,
        max_days=options.max_days,
        max_bytes=options.max_bytes,
    )
    return HistoryActionResult(payload=payload)


def _handle_history_checkpoint(ctx: HistoryActionContext) -> HistoryActionResult:
    options = ctx.options
    payload = history_checkpoint(
        config=ctx.config,
        actor="iwp-build",
        message=options.message,
    )
    return HistoryActionResult(payload=payload)


if __name__ == "__main__":
    raise SystemExit(main())
