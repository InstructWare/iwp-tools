from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    from iwp_lint.config import LintConfig, is_builtin_schema_source
    from iwp_lint.schema.schema_validator import list_markdown_rel_paths
except ImportError:
    from ...iwp_lint.config import LintConfig, is_builtin_schema_source
    from ...iwp_lint.schema.schema_validator import list_markdown_rel_paths

CompileFn = Callable[[LintConfig, list[str] | None], dict[str, object]]
VerifyFn = Callable[[LintConfig, list[str] | None], dict[str, object]]


@dataclass
class DebouncedSourceQueue:
    debounce_seconds: float
    pending_sources: set[str] = field(default_factory=set)
    full_rebuild: bool = False
    _last_change_at: float | None = None

    def mark_changed(self, sources: set[str], now: float) -> None:
        if sources:
            self.pending_sources.update(sources)
            self._last_change_at = now

    def mark_full_rebuild(self, now: float) -> None:
        self.full_rebuild = True
        self._last_change_at = now

    def ready(self, now: float) -> bool:
        if self._last_change_at is None:
            return False
        return (now - self._last_change_at) >= self.debounce_seconds

    def drain(self) -> tuple[bool, list[str]]:
        full_rebuild = self.full_rebuild
        sources = sorted(self.pending_sources)
        self.full_rebuild = False
        self.pending_sources.clear()
        self._last_change_at = None
        return full_rebuild, sources


def run_watch(
    config: LintConfig,
    config_file: str | None,
    debounce_ms: int,
    poll_ms: int,
    verify: bool,
    run_tests: bool,
    once: bool,
    compile_fn: CompileFn,
    verify_fn: VerifyFn,
) -> int:
    queue = DebouncedSourceQueue(debounce_seconds=max(debounce_ms, 0) / 1000.0)
    control_paths = _resolve_control_paths(config, config_file)
    prev_markdown = snapshot_markdown_files(config)
    prev_control = snapshot_control_files(control_paths)

    print("[iwp-build] watch start initial compile")
    compile_fn(config, None)
    if verify and not _run_verify_step(config, verify_fn, None):
        return 1
    if run_tests and not _run_regression_tests():
        return 1
    if once:
        print("[iwp-build] watch once done")
        return 0

    poll_seconds = max(poll_ms, 50) / 1000.0
    print("[iwp-build] watch running; press Ctrl+C to stop")

    while True:
        try:
            now = time.time()
            cur_markdown = snapshot_markdown_files(config)
            cur_control = snapshot_control_files(control_paths)

            changed_sources, deleted_sources = resolve_markdown_changes(prev_markdown, cur_markdown)
            touched_sources = changed_sources | deleted_sources
            if touched_sources:
                queue.mark_changed(touched_sources, now)
                print(
                    "[iwp-build] watch changed sources="
                    f"{len(touched_sources)} pending={len(queue.pending_sources)}"
                )

            if has_control_file_changes(prev_control, cur_control):
                queue.mark_full_rebuild(now)
                print("[iwp-build] watch control file changed; schedule full rebuild")

            prev_markdown = cur_markdown
            prev_control = cur_control

            if queue.ready(now):
                full_rebuild, sources = queue.drain()
                compile_sources = None if full_rebuild else sources
                result = compile_fn(config, compile_sources)
                print(
                    "[iwp-build] watch compiled "
                    f"full={full_rebuild} sources={len(sources)} "
                    f"compiled={result.get('compiled_count', 0)} removed={result.get('removed_count', 0)}"
                )
                if verify and not _run_verify_step(config, verify_fn, compile_sources):
                    return 1
                if run_tests and not _run_regression_tests():
                    return 1

            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("[iwp-build] watch stopped")
            return 0


def snapshot_markdown_files(config: LintConfig) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for source in list_markdown_rel_paths(
        config.iwp_root_path, config.schema_exclude_markdown_globs
    ):
        file_path = config.iwp_root_path / source
        stat = file_path.stat()
        snapshot[source] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def snapshot_control_files(paths: list[Path]) -> dict[str, tuple[int, int] | None]:
    state: dict[str, tuple[int, int] | None] = {}
    for item in paths:
        key = item.as_posix()
        if not item.exists():
            state[key] = None
            continue
        stat = item.stat()
        state[key] = (int(stat.st_mtime_ns), int(stat.st_size))
    return state


def resolve_markdown_changes(
    previous: dict[str, tuple[int, int]],
    current: dict[str, tuple[int, int]],
) -> tuple[set[str], set[str]]:
    changed: set[str] = set()
    deleted = set(previous.keys()) - set(current.keys())
    for source, state in current.items():
        if previous.get(source) != state:
            changed.add(source)
    return changed, deleted


def has_control_file_changes(
    previous: dict[str, tuple[int, int] | None],
    current: dict[str, tuple[int, int] | None],
) -> bool:
    return previous != current


def _resolve_control_paths(config: LintConfig, config_file: str | None) -> list[Path]:
    paths = [(config.project_root / config.node_registry_file).resolve()]
    if not is_builtin_schema_source(config.schema_file):
        paths.append((config.project_root / config.schema_file).resolve())
    if config_file:
        paths.append(Path(config_file).resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        key = item.as_posix()
        if key in seen:
            continue
        unique.append(item)
        seen.add(key)
    return unique


def _run_verify_step(
    config: LintConfig, verify_fn: VerifyFn, source_paths: list[str] | None
) -> bool:
    result = verify_fn(config, source_paths)
    ok = bool(result.get("ok", False))
    print(f"[iwp-build] watch verify checked={result.get('checked_sources', 0)} ok={ok}")
    return ok


def _run_regression_tests() -> bool:
    suite = unittest.defaultTestLoader.loadTestsFromName("iwp_lint.tests.test_regression")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()
