from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from .snapshot_store import SnapshotFile


@dataclass(frozen=True)
class CodeDiffOptions:
    level: Literal["summary", "hunk"] = "summary"
    context_lines: int = 3
    max_chars: int = 12000


def compute_diff_against_snapshot(
    previous: dict[str, SnapshotFile],
    current: dict[str, SnapshotFile],
) -> tuple[set[str], dict[str, set[int]]]:
    changed_files = set(previous.keys()) | set(current.keys())
    changed: set[str] = set()
    changed_lines_by_file: dict[str, set[int]] = {}
    for path in changed_files:
        prev = previous.get(path)
        cur = current.get(path)
        if prev is None or cur is None:
            changed.add(path)
            if path.endswith(".md"):
                changed_lines_by_file[path] = {1}
            continue
        if prev.digest == cur.digest and prev.size == cur.size:
            continue
        changed.add(path)
        if path.endswith(".md"):
            old_content = prev.content or ""
            new_content = cur.content or ""
            lines = compute_changed_lines(old_content, new_content)
            changed_lines_by_file[path] = lines or {1}
    return changed, changed_lines_by_file


def compute_code_change_details(
    previous: dict[str, SnapshotFile],
    current: dict[str, SnapshotFile],
    *,
    include_ext: list[str],
    options: CodeDiffOptions | None = None,
) -> list[dict[str, object]]:
    resolved = options or CodeDiffOptions()
    include_ext_set = set(include_ext)
    changed_paths = sorted(set(previous.keys()) | set(current.keys()))
    details: list[dict[str, object]] = []
    for path in changed_paths:
        prev = previous.get(path)
        cur = current.get(path)
        if not _is_code_path(path, prev, cur, include_ext_set):
            continue
        if (
            prev is not None
            and cur is not None
            and prev.digest == cur.digest
            and prev.size == cur.size
        ):
            continue
        change_kind: str
        if prev is None:
            change_kind = "added"
        elif cur is None:
            change_kind = "deleted"
        else:
            change_kind = "modified"
        old_text = prev.content or "" if prev is not None else ""
        new_text = cur.content or "" if cur is not None else ""
        changed_line_ranges = compute_changed_line_ranges(old_text, new_text)
        if not changed_line_ranges:
            changed_line_ranges = [[1, 1]]
        changed_line_count = sum((end - start + 1) for start, end in changed_line_ranges)
        detail: dict[str, object] = {
            "file_path": path,
            "change_kind": change_kind,
            "changed_line_count": changed_line_count,
            "changed_line_ranges": changed_line_ranges,
        }
        if resolved.level == "hunk":
            hunks, truncated = compute_code_hunks(
                old_text=old_text,
                new_text=new_text,
                context_lines=max(0, resolved.context_lines),
                max_chars=max(0, resolved.max_chars),
            )
            detail["hunks"] = hunks
            detail["hunks_truncated"] = truncated
        details.append(detail)
    return details


def compute_changed_lines(old_text: str, new_text: str) -> set[int]:
    ranges = compute_changed_line_ranges(old_text, new_text)
    changed: set[int] = set()
    for start, end in ranges:
        for line_no in range(start, end + 1):
            changed.add(line_no)
    return changed


def compute_changed_line_ranges(old_text: str, new_text: str) -> list[list[int]]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines)
    ranges: list[list[int]] = []
    last_start: int | None = None
    last_end: int | None = None

    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            anchor = min(max(j1 + 1, 1), max(len(new_lines), 1))
            start = anchor
            end = anchor
        else:
            start = j1 + 1
            end = max(j2, start)
        if last_start is None:
            last_start = start
            last_end = end
            continue
        if start <= (last_end or start) + 1:
            last_end = max(last_end or start, end)
            continue
        ranges.append([last_start, last_end or last_start])
        last_start = start
        last_end = end
    if last_start is not None:
        ranges.append([last_start, last_end or last_start])
    return ranges


def compute_code_hunks(
    *,
    old_text: str,
    new_text: str,
    context_lines: int,
    max_chars: int,
) -> tuple[list[dict[str, object]], bool]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines)
    groups = matcher.get_grouped_opcodes(context_lines)
    hunks: list[dict[str, object]] = []
    consumed_chars = 0
    truncated = False
    for group in groups:
        old_start = group[0][1] + 1
        old_end = group[-1][2]
        new_start = group[0][3] + 1
        new_end = group[-1][4]
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(f" {line}" for line in old_lines[i1:i2])
            elif tag == "delete":
                lines.extend(f"-{line}" for line in old_lines[i1:i2])
            elif tag == "insert":
                lines.extend(f"+{line}" for line in new_lines[j1:j2])
            else:
                lines.extend(f"-{line}" for line in old_lines[i1:i2])
                lines.extend(f"+{line}" for line in new_lines[j1:j2])
        text = "\n".join(lines)
        if max_chars > 0 and (consumed_chars + len(text)) > max_chars:
            truncated = True
            break
        hunks.append(
            {
                "old_start": old_start,
                "old_end": old_end,
                "new_start": new_start,
                "new_end": new_end,
                "text": text,
            }
        )
        consumed_chars += len(text)
    return hunks, truncated


def _is_code_path(
    path: str,
    previous: SnapshotFile | None,
    current: SnapshotFile | None,
    include_ext: set[str],
) -> bool:
    if Path(path).suffix in include_ext:
        return True
    if previous is not None and previous.kind == "code":
        return True
    if current is not None and current.kind == "code":
        return True
    return False
