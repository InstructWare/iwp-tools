from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from difflib import get_close_matches
from typing import Any

from .config import load_config
from .core.engine import print_console_report, run_diff, run_full, run_schema, write_json_report
from .core.link_normalizer import normalize_links
from .core.node_catalog import (
    build_code_sidecar_context,
    build_node_catalog,
    compile_node_context,
    export_node_catalog,
    query_node_catalog,
    verify_compiled_context,
)


class _LintArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        enriched = message
        suggestion = _command_hint_from_message(message)
        if suggestion:
            enriched = f"{message}\n[iwp-lint] {suggestion}"
        super().error(enriched)


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = _LintArgumentParser(
        prog="iwp-lint", description="IWP node coverage linter"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = _LintArgumentParser(add_help=False)
    common.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    common.add_argument("--json", help="Write report JSON to path", default=None)

    check_cmd = sub.add_parser(
        "check",
        parents=[common],
        help="Alias of full lint check (same behavior as `full`)",
    )
    check_cmd.set_defaults(command="check")

    full_cmd = sub.add_parser("full", parents=[common], help="Run full coverage check")
    full_cmd.add_argument(
        "--min-severity",
        choices=["warning", "error"],
        default="warning",
        help="Minimum severity to print in diagnostics output",
    )
    full_cmd.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Hide warning diagnostics while keeping summary counts",
    )
    full_cmd.set_defaults(command="full")

    diff_cmd = sub.add_parser("diff", parents=[common], help="Run diff-based coverage check")
    diff_cmd.add_argument(
        "--min-severity",
        choices=["warning", "error"],
        default="warning",
        help="Minimum severity to print in diagnostics output",
    )
    diff_cmd.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Hide warning diagnostics while keeping summary counts",
    )
    diff_cmd.set_defaults(command="diff")

    schema_cmd = sub.add_parser("schema", parents=[common], help="Run markdown schema validation")
    schema_cmd.add_argument("--mode", choices=["compat", "strict"], default=None)
    schema_cmd.add_argument(
        "--min-severity",
        choices=["warning", "error"],
        default="warning",
        help="Minimum severity to print in diagnostics output",
    )
    schema_cmd.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Hide warning diagnostics while keeping summary counts",
    )
    schema_cmd.set_defaults(command="schema")

    nodes_cmd = sub.add_parser("nodes", help="Build or query node catalog")
    nodes_sub = nodes_cmd.add_subparsers(dest="nodes_action", required=True)

    nodes_build = nodes_sub.add_parser(
        "build", parents=[common], help="Build node catalog from markdown"
    )
    nodes_build.set_defaults(command="nodes", nodes_action="build")

    nodes_query = nodes_sub.add_parser(
        "query", parents=[common], help="Query node IDs from built catalog"
    )
    nodes_query.add_argument(
        "--source", default=None, help="Filter by markdown relative path, e.g. views/pages/home.md"
    )
    nodes_query.add_argument("--text", default=None, help="Anchor text to fuzzy-match")
    nodes_query.add_argument(
        "--line", type=int, default=None, help="Filter by line range in markdown file"
    )
    nodes_query.add_argument("--limit", type=int, default=5, help="Max result count")
    nodes_query.add_argument(
        "--top1-only",
        action="store_true",
        help="Return only the best-matched node",
    )
    nodes_query.add_argument(
        "--format",
        choices=["default", "link"],
        default="default",
        help="Output format for query results",
    )
    nodes_query.add_argument(
        "--exact-text",
        action="store_true",
        help="Use exact normalized text match instead of fuzzy match",
    )
    nodes_query.set_defaults(command="nodes", nodes_action="query")

    nodes_export = nodes_sub.add_parser(
        "export",
        parents=[common],
        help="Export node catalog entries, optionally filtered by source markdown files",
    )
    nodes_export.add_argument(
        "--source",
        action="append",
        default=[],
        help="Markdown relative path filter. Repeatable, e.g. --source views/pages/home.md",
    )
    nodes_export.set_defaults(command="nodes", nodes_action="export")

    nodes_compile = nodes_sub.add_parser(
        "compile",
        parents=[common],
        help="Compile agent-facing .iwc context artifacts",
    )
    nodes_compile.add_argument(
        "--source",
        action="append",
        default=[],
        help="Markdown relative path filter. Repeatable, e.g. --source views/pages/home.md",
    )
    nodes_compile.set_defaults(command="nodes", nodes_action="compile")

    nodes_verify = nodes_sub.add_parser(
        "verify-compiled",
        parents=[common],
        help="Verify .iwc artifacts are present and up to date",
    )
    nodes_verify.add_argument(
        "--source",
        action="append",
        default=[],
        help="Markdown relative path filter. Repeatable, e.g. --source views/pages/home.md",
    )
    nodes_verify.set_defaults(command="nodes", nodes_action="verify-compiled")

    links_cmd = sub.add_parser("links", help="Normalize @iwp.link annotations")
    links_sub = links_cmd.add_subparsers(dest="links_action", required=True)
    links_normalize = links_sub.add_parser("normalize", parents=[common], help="Normalize link comments")
    links_normalize.add_argument(
        "--write",
        action="store_true",
        help="Apply normalized links to files. Default is check mode.",
    )
    links_normalize.set_defaults(command="links", links_action="normalize")
    links_sidecar = links_sub.add_parser(
        "sidecar",
        parents=[common],
        help="Build code sidecar files with inline IWP node context blocks",
    )
    links_sidecar.set_defaults(command="links", links_action="sidecar")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)

    try:
        if args.command == "nodes":
            if args.nodes_action == "build":
                result = build_node_catalog(config)
            elif args.nodes_action == "export":
                result = export_node_catalog(config, source_paths=args.source)
            elif args.nodes_action == "compile":
                result = compile_node_context(config, source_paths=args.source)
            elif args.nodes_action == "verify-compiled":
                result = verify_compiled_context(config, source_paths=args.source)
            else:
                if args.text is None and args.line is None:
                    raise RuntimeError("nodes query requires at least one of --text or --line")
                result = query_node_catalog(
                    config=config,
                    source_path=args.source,
                    text=args.text,
                    line=args.line,
                    limit=args.limit,
                    exact_text=args.exact_text,
                )
                if args.top1_only and isinstance(result.get("results"), list):
                    results_list = result.get("results", [])
                    if isinstance(results_list, list):
                        result["results"] = results_list[:1]
                        result["returned"] = len(results_list[:1])
            _print_nodes_result(
                args.nodes_action,
                result,
                output_format=getattr(args, "format", "default"),
            )
            _write_json_blob(args.json, result)
            if args.nodes_action == "verify-compiled" and not bool(result.get("ok", False)):
                return 1
            return 0
        if args.command == "links":
            if args.links_action == "normalize":
                result = normalize_links(config=config, write=bool(args.write))
                _print_links_result(result)
                _write_json_blob(args.json, result)
                if not bool(args.write) and int(result.get("changed_count", 0)) > 0:
                    return 1
                return 0
            if args.links_action == "sidecar":
                result = build_code_sidecar_context(config=config)
                _print_code_sidecar_result(result)
                _write_json_blob(args.json, result)
                return 0
            raise RuntimeError(f"unknown links action: {args.links_action}")
        if args.command in {"full", "check"}:
            report = run_full(config)
        elif args.command == "diff":
            report = run_diff(config, None, None)
        else:
            report = run_schema(config, args.mode)
    except RuntimeError as exc:
        print(f"[iwp-lint] {exc}", file=sys.stderr)
        return 2

    print_console_report(
        report,
        min_severity=getattr(args, "min_severity", "warning"),
        quiet_warnings=bool(getattr(args, "quiet_warnings", False)),
    )
    _print_remediation_hints(report)
    write_json_report(args.json, report)
    return 1 if report["summary"]["error_count"] > 0 else 0


def _print_nodes_result(
    action: str, result: Mapping[str, Any], output_format: str = "default"
) -> None:
    if action == "build":
        print(
            "[iwp-lint] nodes build "
            f"entries={result.get('entry_count', 0)} path={result.get('catalog_path', '')}"
        )
        return
    if action == "export":
        print(
            "[iwp-lint] nodes export "
            f"entries={result.get('entry_count', 0)} path={result.get('catalog_path', '')}"
        )
        return
    if action == "compile":
        print(
            "[iwp-lint] nodes compile "
            f"compiled={result.get('compiled_count', 0)} "
            f"json={_safe_len(result.get('compiled_json_files'))} "
            f"md={_safe_len(result.get('compiled_md_files'))} "
            f"dir={result.get('compiled_dir', '')}"
        )
        return
    if action == "verify-compiled":
        print(
            "[iwp-lint] nodes verify-compiled "
            f"checked={result.get('checked_sources', 0)} ok={result.get('ok', False)}"
        )
        missing = result.get("missing_files", [])
        stale = result.get("stale_files", [])
        invalid = result.get("invalid_files", [])
        missing_json = result.get("missing_json_files", [])
        missing_md = result.get("missing_md_files", [])
        if isinstance(missing, list) and missing:
            print(f"missing={len(missing)}")
        if isinstance(missing_json, list) and missing_json:
            print(f"missing_json={len(missing_json)}")
        if isinstance(missing_md, list) and missing_md:
            print(f"missing_md={len(missing_md)}")
        if isinstance(stale, list) and stale:
            print(f"stale={len(stale)}")
        if isinstance(invalid, list) and invalid:
            print(f"invalid={len(invalid)}")
        return
    if output_format == "link":
        first = _first_result(result)
        if first is None:
            raise RuntimeError("nodes query returned no result; cannot render --format link")
        source_path = str(first.get("source_path", ""))
        node_id = str(first.get("node_id", ""))
        print(f"@iwp.link {source_path}::{node_id}")
        return
    print(
        "[iwp-lint] nodes query "
        f"candidates={result.get('total_candidates', 0)} returned={result.get('returned', 0)}"
    )
    results = result.get("results", [])
    if not isinstance(results, list):
        return
    for item in results:
        if not isinstance(item, dict):
            continue
        print(
            f"{item.get('source_path')}:{item.get('line_start')}:{item.get('line_end')} "
            f"{item.get('node_id')} score={item.get('score')}"
        )


def _first_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    items = result.get("results", [])
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    return first


def _write_json_blob(path: str | None, payload: Mapping[str, Any]) -> None:
    if not path:
        return
    from pathlib import Path

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _print_links_result(result: Mapping[str, Any]) -> None:
    print(
        "[iwp-lint] links normalize "
        f"mode={result.get('mode')} "
        f"checked={result.get('checked_files', 0)} "
        f"changed={result.get('changed_count', 0)} "
        f"removed_stale={result.get('removed_stale_links', 0)} "
        f"removed_dup={result.get('removed_duplicate_links', 0)} "
        f"multi_line_blocks_seen={result.get('multi_line_blocks_seen', 0)}"
    )


def _print_code_sidecar_result(result: Mapping[str, Any]) -> None:
    print(
        "[iwp-lint] links sidecar "
        f"files_scanned={result.get('files_scanned', 0)} "
        f"files_written={result.get('files_written', 0)} "
        f"links_found={result.get('links_found', 0)} "
        f"resolved={result.get('resolved_links', 0)} "
        f"unresolved={result.get('unresolved_links', 0)} "
        f"dir={result.get('code_sidecar_dir', '')}"
    )
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, list):
        return
    for item in diagnostics:
        if not isinstance(item, Mapping):
            continue
        print(
            f"[iwp-lint] [W][{item.get('code', 'IWP399')}] "
            f"{item.get('file_path', '')}:{item.get('line', 0)}:{item.get('column', 0)} "
            f"{item.get('message', '')}"
        )


def _command_hint_from_message(message: str) -> str | None:
    marker = "invalid choice: '"
    idx = message.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = message.find("'", start)
    if end <= start:
        return None
    wrong = message[start:end]
    known = ["full", "check", "diff", "schema", "nodes", "links"]
    if wrong == "check":
        return "Did you mean `iwp-lint check` (alias of `iwp-lint full`)?"
    matches = get_close_matches(wrong, known, n=1, cutoff=0.6)
    if not matches:
        return None
    guessed = matches[0]
    if guessed == "check":
        return "Did you mean `iwp-lint check` (alias of `iwp-lint full`)?"
    return f"Did you mean `iwp-lint {guessed}`?"


def _print_remediation_hints(report: Mapping[str, Any]) -> None:
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, list):
        return
    codes = {str(item.get("code", "")) for item in diagnostics if isinstance(item, Mapping)}
    if not codes:
        return
    hints: list[str] = []
    if "IWP105" in codes:
        hints.append("IWP105 fix: uv run iwp-lint links normalize --config .iwp-lint.yaml --write")
    if "IWP107" in codes:
        hints.append("IWP107 fix: add/update/remove colocated @iwp.link near behavior boundary")
    if "IWP109" in codes:
        hints.append("IWP109 fix: review thresholds and tiny_diff settings in .iwp-lint.yaml")
    for hint in hints:
        print(f"[iwp-lint] hint {hint}")


if __name__ == "__main__":
    sys.exit(main())
