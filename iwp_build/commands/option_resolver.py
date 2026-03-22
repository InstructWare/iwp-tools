from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BuildOptions:
    mode: str
    json_path: str | None
    normalize_links: bool
    build_code_sidecar: bool


@dataclass(frozen=True)
class VerifyOptions:
    with_tests: bool
    protocol_only: bool
    min_severity: str
    quiet_warnings: bool


@dataclass(frozen=True)
class SessionOptions:
    action: str
    session_id: str | None
    json_path: str | None
    code_diff_level: str | None
    code_diff_context_lines: int | None
    code_diff_max_chars: int | None
    node_severity: str | None
    node_file_type_ids: list[str] | None
    node_anchor_levels: list[str] | None
    node_kind_prefixes: list[str] | None
    critical_only: bool
    markdown_excerpt_max_chars: int | None
    normalize_links: bool
    evidence_json_path: str | None
    output_format: str
    debug_raw: bool
    auto_start_session: bool
    include_baseline_gaps: bool
    focus_path: str | None
    max_gap_items: int | None
    max_diagnostics: int | None
    min_severity: str
    quiet_warnings: bool
    suggest_fixes: bool
    allow_stale_sidecar: bool
    warning_top_n: int | None
    if_missing: bool
    auto_build_sidecar: bool


def get_preset_command_options(
    *,
    config: Any,
    preset_name: str | None,
    command_key: str,
) -> dict[str, Any]:
    if not preset_name:
        return {}
    all_presets = getattr(config, "execution_presets", {})
    if not isinstance(all_presets, dict) or not all_presets:
        raise RuntimeError("no execution_presets found in config")
    preset = all_presets.get(preset_name)
    if not isinstance(preset, dict):
        available = ", ".join(sorted(str(key) for key in all_presets.keys()))
        raise RuntimeError(f"unknown preset `{preset_name}`; available: {available}")
    options = preset.get(command_key, {})
    if not isinstance(options, dict):
        return {}
    return options


def pick_value(current: Any, preset_value: Any, *, fallback: Any) -> Any:
    if current is not None:
        return current
    if preset_value is not None:
        return preset_value
    return fallback


def pick_list(current: list[str] | None, *preset_values: Any) -> list[str] | None:
    if current is not None:
        return current
    for preset_value in preset_values:
        if preset_value is None:
            continue
        if isinstance(preset_value, list):
            return [str(item) for item in preset_value]
        if isinstance(preset_value, str):
            return [preset_value]
    return None


def resolve_build_options(args: argparse.Namespace, *, config: Any) -> BuildOptions:
    preset_opts = get_preset_command_options(
        config=config,
        preset_name=getattr(args, "preset", None),
        command_key="build",
    )
    mode = pick_value(getattr(args, "mode", None), preset_opts.get("mode"), fallback="auto")
    json_path = pick_value(getattr(args, "json", None), preset_opts.get("json"), fallback=None)
    normalize_links = bool(getattr(args, "normalize_links", False)) or bool(
        preset_opts.get("normalize_links", False)
    )
    no_code_sidecar = bool(getattr(args, "no_code_sidecar", False)) or bool(
        preset_opts.get("no_code_sidecar", False)
    )
    return BuildOptions(
        mode=str(mode),
        json_path=str(json_path) if isinstance(json_path, str) else None,
        normalize_links=normalize_links,
        build_code_sidecar=not no_code_sidecar,
    )


def resolve_verify_options(args: argparse.Namespace, *, config: Any) -> VerifyOptions:
    preset_opts = get_preset_command_options(
        config=config,
        preset_name=getattr(args, "preset", None),
        command_key="verify",
    )
    min_severity = pick_value(
        getattr(args, "min_severity", None),
        preset_opts.get("min_severity"),
        fallback="warning",
    )
    with_tests = (
        bool(getattr(args, "with_tests", False))
        or bool(getattr(args, "run_tests", False))
        or bool(preset_opts.get("with_tests", False))
        or bool(preset_opts.get("run_tests", False))
    )
    protocol_only = bool(getattr(args, "protocol_only", False)) or bool(
        preset_opts.get("protocol_only", False)
    )
    quiet_warnings = bool(getattr(args, "quiet_warnings", False)) or bool(
        preset_opts.get("quiet_warnings", False)
    )
    return VerifyOptions(
        with_tests=with_tests,
        protocol_only=protocol_only,
        min_severity=str(min_severity),
        quiet_warnings=quiet_warnings,
    )


def resolve_session_options(args: argparse.Namespace, *, config: Any) -> SessionOptions:
    action = str(args.session_action)
    preset_opts = get_preset_command_options(
        config=config,
        preset_name=getattr(args, "preset", None),
        command_key=f"session_{action}",
    )
    node_anchor_levels = pick_list(
        getattr(args, "node_anchor_levels", None),
        preset_opts.get("node_anchor_levels"),
        preset_opts.get("node_anchor_level"),
    )
    node_file_type_ids = pick_list(
        getattr(args, "node_file_type_ids", None),
        preset_opts.get("node_file_type_ids"),
        preset_opts.get("node_file_type_id"),
    )
    node_kind_prefixes = pick_list(
        getattr(args, "node_kind_prefixes", None),
        preset_opts.get("node_kind_prefixes"),
        preset_opts.get("node_kind_prefix"),
    )
    output_format = pick_value(
        getattr(args, "format", None),
        preset_opts.get("format"),
        fallback="text",
    )
    config_auto_start = bool(getattr(getattr(config, "session", None), "auto_start_on_missing", False))
    auto_start_session = (
        bool(getattr(args, "auto_start_session", False))
        or bool(preset_opts.get("auto_start_session", False))
        or config_auto_start
    )
    warning_top_n = pick_value(
        getattr(args, "warning_top_n", None),
        preset_opts.get("warning_top_n"),
        fallback=getattr(getattr(config, "session", None), "warning_summary_top_n", 2),
    )
    return SessionOptions(
        action=action,
        session_id=pick_value(
            getattr(args, "session_id", None),
            preset_opts.get("session_id"),
            fallback=None,
        ),
        json_path=pick_value(
            getattr(args, "json", None),
            preset_opts.get("json"),
            fallback=None,
        ),
        code_diff_level=pick_value(
            getattr(args, "code_diff_level", None),
            preset_opts.get("code_diff_level"),
            fallback=None,
        ),
        code_diff_context_lines=pick_value(
            getattr(args, "code_diff_context_lines", None),
            preset_opts.get("code_diff_context_lines"),
            fallback=None,
        ),
        code_diff_max_chars=pick_value(
            getattr(args, "code_diff_max_chars", None),
            preset_opts.get("code_diff_max_chars"),
            fallback=None,
        ),
        node_severity=pick_value(
            getattr(args, "node_severity", None),
            preset_opts.get("node_severity"),
            fallback=None,
        ),
        node_file_type_ids=node_file_type_ids,
        node_anchor_levels=node_anchor_levels,
        node_kind_prefixes=node_kind_prefixes,
        critical_only=bool(getattr(args, "critical_only", False))
        or bool(preset_opts.get("critical_only", False)),
        markdown_excerpt_max_chars=pick_value(
            getattr(args, "markdown_excerpt_max_chars", None),
            preset_opts.get("markdown_excerpt_max_chars"),
            fallback=None,
        ),
        normalize_links=bool(getattr(args, "normalize_links", False))
        or bool(preset_opts.get("normalize_links", False)),
        evidence_json_path=pick_value(
            getattr(args, "evidence_json", None),
            preset_opts.get("evidence_json"),
            fallback=None,
        ),
        output_format=str(output_format),
        debug_raw=bool(getattr(args, "debug_raw", False)) or bool(preset_opts.get("debug_raw", False)),
        auto_start_session=auto_start_session,
        include_baseline_gaps=bool(getattr(args, "include_baseline_gaps", False))
        or bool(preset_opts.get("include_baseline_gaps", False)),
        focus_path=pick_value(
            getattr(args, "focus_path", None),
            preset_opts.get("focus_path"),
            fallback=None,
        ),
        max_gap_items=pick_value(
            getattr(args, "max_gap_items", None),
            preset_opts.get("max_gap_items"),
            fallback=None,
        ),
        max_diagnostics=pick_value(
            getattr(args, "max_diagnostics", None),
            preset_opts.get("max_diagnostics"),
            fallback=None,
        ),
        min_severity=pick_value(
            getattr(args, "min_severity", None),
            preset_opts.get("min_severity"),
            fallback="warning",
        ),
        quiet_warnings=bool(getattr(args, "quiet_warnings", False))
        or bool(preset_opts.get("quiet_warnings", False)),
        suggest_fixes=bool(getattr(args, "suggest_fixes", False))
        or bool(preset_opts.get("suggest_fixes", False)),
        allow_stale_sidecar=bool(getattr(args, "allow_stale_sidecar", False))
        or bool(preset_opts.get("allow_stale_sidecar", False)),
        warning_top_n=warning_top_n,
        if_missing=bool(getattr(args, "if_missing", False)) or bool(preset_opts.get("if_missing", False)),
        auto_build_sidecar=bool(getattr(args, "auto_build_sidecar", False))
        or bool(preset_opts.get("auto_build_sidecar", False)),
    )
