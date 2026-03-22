from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import LintConfig
from ..core.models import MarkdownNode
from .snapshot_diff import compute_diff_against_snapshot
from .snapshot_store import SnapshotStore, collect_workspace_files


@dataclass
class DiffResult:
    changed_files: set[str] = field(default_factory=set)
    changed_lines_by_file: dict[str, set[int]] = field(default_factory=dict)


class DiffProvider:
    name = "filesystem_snapshot"

    def load(
        self,
        config: LintConfig,
        base: str,
        head: str,
        cwd: Path,
        strict: bool = True,
    ) -> DiffResult:
        _ = (base, head, cwd, strict)
        db_path = (config.project_root / config.snapshot_db_file).resolve()
        store = SnapshotStore(db_path)
        latest_id = store.latest_snapshot_id()
        if latest_id is None:
            raise RuntimeError(
                "filesystem snapshot baseline not found; run `iwp-build build` once to initialize baseline"
            )

        previous = store.load_snapshot(latest_id)
        current_files = collect_workspace_files(
            project_root=config.project_root,
            iwp_root=config.iwp_root,
            iwp_root_path=config.iwp_root_path,
            code_roots=config.code_roots,
            include_ext=config.include_ext,
            code_exclude_globs=config.code_exclude_globs,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
        )
        current = {item.path: item for item in current_files}
        result = DiffResult()
        changed_files, changed_lines_by_file = compute_diff_against_snapshot(previous, current)
        result.changed_files = changed_files
        result.changed_lines_by_file = changed_lines_by_file
        return result


def load_diff(
    config: LintConfig,
    base: str,
    head: str,
    cwd: Path,
    strict: bool = True,
    provider_name: str | None = None,
) -> DiffResult:
    if provider_name and provider_name != DiffProvider.name:
        raise RuntimeError(
            f"unknown diff provider: {provider_name}. supported: {DiffProvider.name}"
        )
    _ = config.diff_provider
    provider = DiffProvider()
    return provider.load(config=config, base=base, head=head, cwd=cwd, strict=strict)


def impacted_nodes(nodes: list[MarkdownNode], diff_result: DiffResult) -> list[MarkdownNode]:
    if not diff_result.changed_files:
        return []

    changed_lines_index = _build_changed_lines_index(diff_result.changed_lines_by_file)
    impacted: list[MarkdownNode] = []
    for node in nodes:
        changed_lines = _resolve_changed_lines(changed_lines_index, node.source_path)
        if not changed_lines:
            continue
        if any(node.line_start <= line <= node.line_end for line in changed_lines):
            impacted.append(node)
    return impacted


def _build_changed_lines_index(changed_lines_by_file: dict[str, set[int]]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for raw_path, lines in changed_lines_by_file.items():
        normalized = Path(raw_path).as_posix()
        index[normalized] = lines
        # Snapshot stores markdown paths as "<iwp_root>/<source_path>", while nodes use source_path.
        # Index both forms so diff resolution works for top-level and nested markdown files.
        if "/" in normalized:
            index.setdefault(normalized.split("/", 1)[1], lines)
    return index


def _resolve_changed_lines(index: dict[str, set[int]], source_path: str) -> set[int] | None:
    direct = index.get(source_path)
    if direct is not None:
        return direct

    suffix = f"/{source_path}"
    merged: set[int] = set()
    for path, lines in index.items():
        if path.endswith(suffix):
            merged.update(lines)
    return merged or None
