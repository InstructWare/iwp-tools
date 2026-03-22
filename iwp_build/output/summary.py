from __future__ import annotations

from collections.abc import Mapping

from .policy import collect_remediation_hints, filter_diagnostics
from .utils import safe_int


def print_compiled_failure_summary(compiled: Mapping[str, object], limit: int = 3) -> None:
    _print_path_bucket("missing", compiled.get("missing_files"), limit)
    _print_path_bucket("stale", compiled.get("stale_files"), limit)
    _print_path_bucket("invalid", compiled.get("invalid_files"), limit)


def print_lint_failure_summary(
    lint_report: Mapping[str, object],
    limit: int = 5,
    *,
    min_severity: str = "warning",
    quiet_warnings: bool = False,
) -> None:
    diagnostics = lint_report.get("diagnostics")
    if not isinstance(diagnostics, list):
        return
    filtered = filter_diagnostics(
        diagnostics, min_severity=min_severity, quiet_warnings=quiet_warnings
    )
    print(f"[iwp-build] verify lint failed diagnostics={len(filtered)}")
    for item in filtered[:limit]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", ""))
        file_path = str(item.get("file_path", ""))
        line = int(item.get("line", 0))
        message = str(item.get("message", ""))
        severity = str(item.get("severity", "error")).lower()
        tag = "[E]" if severity == "error" else "[W]"
        print(f"[iwp-build] {tag}[{code}] {file_path}:{line} {message}")
    hints = collect_remediation_hints(diagnostics)
    for hint in hints:
        print(f"[iwp-build] hint {hint}")


def print_repair_plan_hint(gap_report: Mapping[str, object]) -> None:
    repair = gap_report.get("repair_summary")
    if not isinstance(repair, Mapping):
        return
    critical_missing = safe_int(repair.get("critical_missing_total", 0))
    missing_total = safe_int(repair.get("missing_total", 0))
    print("[iwp-build] repair " f"critical_missing={critical_missing} missing_total={missing_total}")
    next_actions = repair.get("next_actions")
    if not isinstance(next_actions, list):
        return
    for action in next_actions[:3]:
        print(f"[iwp-build] repair-next {action}")


def _print_path_bucket(label: str, values: object, limit: int) -> None:
    if not isinstance(values, list) or not values:
        return
    sample = ", ".join(str(item) for item in values[:limit])
    print(f"[iwp-build] verify compiled {label}={len(values)} sample=[{sample}]")
