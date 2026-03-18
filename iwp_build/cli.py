from __future__ import annotations

import argparse
import json
import sys
import unittest
from collections.abc import Mapping

from iwp_build.watch import run_watch
from iwp_lint.api import (
    compile_context,
    run_quality_gate,
    snapshot_action,
    verify_compiled,
)
from iwp_lint.config import load_config
from iwp_lint.core.engine import print_console_report, run_diff, run_full


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iwp-build", description="IWP incremental build orchestrator"
    )
    parser.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser(
        "build", help="Compile .iwc and compute incremental implementation gap"
    )
    build_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    build_cmd.add_argument(
        "--mode",
        choices=["auto", "diff", "full"],
        default="auto",
        help="Build mode: auto tries diff first and falls back to full on first run",
    )
    build_cmd.add_argument("--json", help="Write build summary JSON to path", default=None)
    build_cmd.add_argument(
        "--diff-json",
        help="Write compact diff payload for agent consumption",
        default=None,
    )
    build_cmd.set_defaults(command="build")

    verify_cmd = sub.add_parser("verify", help="Run compiled check + lint gate (+ tests)")
    verify_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    verify_cmd.add_argument("--run-tests", action="store_true", help="Run regression tests")
    verify_cmd.set_defaults(command="verify")

    watch_cmd = sub.add_parser(
        "watch", help="Watch markdown changes and rebuild .iwc incrementally"
    )
    watch_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    watch_cmd.add_argument(
        "--debounce-ms",
        type=int,
        default=600,
        help="Debounce window for change batching",
    )
    watch_cmd.add_argument(
        "--poll-ms",
        type=int,
        default=250,
        help="Polling interval for file change detection",
    )
    watch_cmd.add_argument(
        "--verify",
        action="store_true",
        help="Verify compiled artifacts after each build",
    )
    watch_cmd.add_argument(
        "--run-tests",
        action="store_true",
        help="Run regression tests after each successful build",
    )
    watch_cmd.add_argument(
        "--once",
        action="store_true",
        help="Run one compile cycle and exit",
    )
    watch_cmd.set_defaults(command="watch")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "build":
        return _run_build(
            config=config,
            mode=args.mode,
            json_path=args.json,
            diff_json_path=args.diff_json,
        )
    if args.command == "verify":
        return _run_verify(config, args.run_tests)
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
    raise RuntimeError(f"unknown command: {args.command}")


def _run_build(
    config,
    mode: str,
    json_path: str | None,
    diff_json_path: str | None = None,
) -> int:
    compile_result = compile_context(config)
    build_mode = mode
    needs_bootstrap_init = False
    intent = {
        "changed_files": [],
        "changed_md_files": [],
        "changed_code_files": [],
        "changed_count": 0,
        "impacted_nodes": [],
    }

    if mode in {"auto", "diff"}:
        try:
            intent = snapshot_action(config, "diff")
            gap_report = run_diff(config, None, None)
            build_mode = "diff"
        except RuntimeError as exc:
            if mode == "diff":
                raise
            if "baseline not found" not in str(exc):
                raise
            gap_report = run_full(config)
            build_mode = "bootstrap_full"
            needs_bootstrap_init = True
    else:
        gap_report = run_full(config)
        build_mode = "full"

    gap_error_count = int(gap_report["summary"]["error_count"])
    baseline_bootstrapped = needs_bootstrap_init and gap_error_count == 0
    summary = {
        "build_mode": build_mode,
        "baseline_bootstrapped": baseline_bootstrapped,
        "compiled_count": int(compile_result.get("compiled_count", 0)),
        "removed_count": int(compile_result.get("removed_count", 0)),
        "changed_count": int(intent.get("changed_count", 0)),
        "changed_md_count": _safe_len(intent.get("changed_md_files")),
        "impacted_nodes_count": _safe_len(intent.get("impacted_nodes")),
        "gap_error_count": gap_error_count,
        "gap_warning_count": int(gap_report["summary"]["warning_count"]),
    }
    print(
        "[iwp-build] build "
        f"mode={summary['build_mode']} "
        f"compiled={summary['compiled_count']} removed={summary['removed_count']} "
        f"changed={summary['changed_count']} impacted_nodes={summary['impacted_nodes_count']} "
        f"gap_errors={summary['gap_error_count']}"
    )
    print_console_report(gap_report)
    full_payload = {
        "summary": summary,
        "compile": compile_result,
        "intent_diff": intent,
        "gap_report": gap_report,
    }
    written_json_path = _write_json(
        json_path,
        full_payload,
    )
    if written_json_path is not None:
        print(
            "[iwp-build] build json "
            f"path={written_json_path} "
            f"changed_md={summary['changed_md_count']} "
            f"impacted_nodes={summary['impacted_nodes_count']} "
            f"gap_errors={summary['gap_error_count']}"
        )
    written_diff_json_path = _write_json(
        diff_json_path,
        _to_compact_diff_payload(summary=summary, intent=intent, gap_report=gap_report),
    )
    if written_diff_json_path is not None:
        print(
            "[iwp-build] build diff json "
            f"path={written_diff_json_path} "
            f"changed_md={summary['changed_md_count']} "
            f"impacted_nodes={summary['impacted_nodes_count']} "
            f"gap_errors={summary['gap_error_count']}"
        )

    if gap_error_count > 0:
        print("[iwp-build] build failed; keep previous baseline unchanged")
        return 1

    # Build completion is the manual checkpoint; only commit snapshot when gap checks pass.
    if needs_bootstrap_init:
        snapshot_action(config, "init")
    else:
        snapshot_action(config, "update")
    return 0


def _run_verify(config, run_tests: bool) -> int:
    compiled = verify_compiled(config)
    if not bool(compiled.get("ok", False)):
        print(f"[iwp-build] verify compiled checked={compiled.get('checked_sources', 0)} ok=False")
        return 1

    gate = run_quality_gate(config)
    print_console_report(gate["lint_report"])
    if gate["lint_exit_code"] != 0:
        return 1

    if run_tests:
        suite = unittest.defaultTestLoader.loadTestsFromName("iwp_lint.tests.test_regression")
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
        if not result.wasSuccessful():
            return 1

    print("[iwp-build] verify ok")
    return 0


def _safe_len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _to_compact_diff_payload(
    *,
    summary: dict[str, object],
    intent: dict[str, object],
    gap_report: dict[str, object],
) -> dict[str, object]:
    raw_gap_summary = gap_report.get("summary", {})
    gap_summary: Mapping[str, object]
    if isinstance(raw_gap_summary, Mapping):
        gap_summary = raw_gap_summary
    else:
        gap_summary = {}
    return {
        "summary": {
            "build_mode": summary.get("build_mode"),
            "changed_md_count": summary.get("changed_md_count"),
            "impacted_nodes_count": summary.get("impacted_nodes_count"),
            "gap_error_count": summary.get("gap_error_count"),
            "gap_warning_count": summary.get("gap_warning_count"),
        },
        "intent_diff": {
            "changed_md_files": intent.get("changed_md_files", []),
            "impacted_nodes": intent.get("impacted_nodes", []),
        },
        "gap_report": {
            "mode": gap_report.get("mode"),
            "summary": {
                "error_count": gap_summary.get("error_count", 0),
                "warning_count": gap_summary.get("warning_count", 0),
                "total_nodes_in_scope": gap_summary.get("total_nodes_in_scope", 0),
                "total_nodes_all": gap_summary.get("total_nodes_all", 0),
                "covered_nodes": gap_summary.get("covered_nodes", 0),
            },
            "diagnostics": gap_report.get("diagnostics", []),
            "nodes": gap_report.get("nodes", []),
        },
    }


def _write_json(path: str | None, payload: dict[str, object]) -> str | None:
    if not path:
        return None
    from pathlib import Path

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
