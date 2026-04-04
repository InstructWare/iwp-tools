from __future__ import annotations

from pathlib import Path
from typing import Any


def build_code_path_hints(*, config: Any, changed_md_files: object) -> list[str]:
    if not isinstance(changed_md_files, list):
        return []
    hints: list[str] = []
    for source in changed_md_files:
        if not isinstance(source, str) or not source.endswith(".md"):
            continue
        source_no_ext = source[:-3]
        parts = source_no_ext.split("/")
        stem = parts[-1]
        stem_pascal = "".join(piece.capitalize() for piece in stem.replace("-", "_").split("_"))
        md_like_vue = f"{source_no_ext}.vue"
        parent = "/".join(parts[:-1])
        candidates: list[str] = []
        for code_root in getattr(config, "code_roots", ["."]):
            base_root = str(code_root).rstrip("/")
            if base_root == ".":
                base_root = ""
            prefixes = [base_root, f"{base_root}/src" if base_root else "src"]
            for prefix in prefixes:
                prefix = prefix.strip("/")
                if prefix:
                    candidates.append(f"{prefix}/{md_like_vue}")
                    candidates.append(f"{prefix}/{parent}/{stem_pascal}.vue")
                    candidates.append(f"{prefix}/{parent}/{stem_pascal}Page.vue")
                else:
                    candidates.append(md_like_vue)
                    candidates.append(f"{parent}/{stem_pascal}.vue")
                    candidates.append(f"{parent}/{stem_pascal}Page.vue")
        seen: set[str] = set()
        for rel in candidates:
            clean = rel.replace("//", "/").lstrip("./")
            if not clean or clean in seen:
                continue
            seen.add(clean)
            abs_path = (Path(config.project_root) / clean).resolve()
            if abs_path.exists() and abs_path.is_file():
                hints.append(clean)
    deduped: list[str] = []
    used: set[str] = set()
    for item in hints:
        if item in used:
            continue
        used.add(item)
        deduped.append(item)
    return deduped


def build_suggested_code_paths(
    *,
    lint_report: object,
    code_path_hints: list[str],
    max_items: int,
) -> list[str]:
    suggested: list[str] = []
    if isinstance(lint_report, dict):
        repair_summary = lint_report.get("repair_summary", {})
        if isinstance(repair_summary, dict):
            by_file = repair_summary.get("by_file", [])
            if isinstance(by_file, list):
                for item in by_file:
                    if not isinstance(item, dict):
                        continue
                    targets = item.get("suggested_targets", [])
                    if not isinstance(targets, list):
                        continue
                    for target in targets:
                        if isinstance(target, str) and target.strip():
                            suggested.append(target.strip())
    suggested.extend(code_path_hints)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in suggested:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= max(1, max_items):
            break
    return deduped
