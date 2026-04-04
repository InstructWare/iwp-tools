from __future__ import annotations

from collections.abc import Mapping


def safe_len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def as_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def collect_remediation_hints(diagnostics: list[object]) -> list[str]:
    codes: set[str] = set()
    for item in diagnostics:
        if isinstance(item, Mapping):
            codes.add(str(item.get("code", "")))
    hints: list[str] = []
    if "IWP105" in codes:
        hints.append("run: uv run iwp-lint links normalize --config .iwp-lint.yaml --write")
    if "IWP107" in codes:
        hints.append("add/update/remove @iwp.link for uncovered markdown nodes")
    if "IWP109" in codes:
        hints.append("review thresholds and tiny-diff settings in .iwp-lint.yaml")
    if not hints:
        hints.append("run: uv run iwp-lint full --config .iwp-lint.yaml --json out/iwp-report.json")
    return hints


def build_next_actions(
    *,
    compiled: Mapping[str, object] | None = None,
    diagnostics: list[object] | None = None,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if isinstance(compiled, Mapping) and not bool(compiled.get("ok", False)):
        actions.append(
            {
                "kind": "rebuild",
                "reason": "compiled_artifacts_invalid",
                "command": "uv run iwp-build build --config .iwp-lint.yaml --mode diff",
            }
        )
    for hint in collect_remediation_hints(diagnostics or []):
        command = hint.replace("run: ", "") if hint.startswith("run: ") else ""
        actions.append(
            {
                "kind": "lint_fix",
                "reason": hint,
                "command": command,
            }
        )
    if not actions:
        actions.append(
            {
                "kind": "verify",
                "reason": "run quality checks",
                "command": "uv run iwp-build verify --config .iwp-lint.yaml --min-severity error",
            }
        )
    return actions


def build_diagnostics_top(
    diagnostics: list[object] | None,
    *,
    max_items: int,
) -> list[dict[str, object]]:
    if not isinstance(diagnostics, list):
        return []
    top: list[dict[str, object]] = []
    for item in diagnostics:
        if not isinstance(item, Mapping):
            continue
        top.append(
            {
                "code": str(item.get("code", "")),
                "severity": str(item.get("severity", "error")),
                "file_path": str(item.get("file_path", "")),
                "line": safe_int(item.get("line", 0)),
                "message": str(item.get("message", "")),
            }
        )
        if len(top) >= max(0, max_items):
            break
    return top
