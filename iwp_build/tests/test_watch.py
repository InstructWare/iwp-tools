from __future__ import annotations

import unittest
from typing import TypeAlias

from iwp_build.watch import (
    DebouncedSourceQueue,
    has_control_file_changes,
    resolve_markdown_changes,
)

Snapshot: TypeAlias = tuple[int, int] | None


class IwpBuildWatchTests(unittest.TestCase):
    def test_debounced_queue_waits_and_drains_sources(self) -> None:
        queue = DebouncedSourceQueue(debounce_seconds=0.5)
        queue.mark_changed({"views/pages/home.md"}, now=1.0)
        self.assertFalse(queue.ready(1.3))
        self.assertTrue(queue.ready(1.6))
        full, sources = queue.drain()
        self.assertFalse(full)
        self.assertEqual(sources, ["views/pages/home.md"])

    def test_debounced_queue_marks_full_rebuild(self) -> None:
        queue = DebouncedSourceQueue(debounce_seconds=0.2)
        queue.mark_changed({"views/pages/home.md"}, now=2.0)
        queue.mark_full_rebuild(now=2.1)
        self.assertTrue(queue.ready(2.4))
        full, sources = queue.drain()
        self.assertTrue(full)
        self.assertEqual(sources, ["views/pages/home.md"])

    def test_resolve_markdown_changes_detects_deleted_and_modified(self) -> None:
        previous = {
            "views/pages/home.md": (100, 20),
            "views/pages/about.md": (100, 20),
        }
        current = {
            "views/pages/home.md": (101, 22),
            "views/pages/new.md": (101, 8),
        }
        changed, deleted = resolve_markdown_changes(previous, current)
        self.assertEqual(changed, {"views/pages/home.md", "views/pages/new.md"})
        self.assertEqual(deleted, {"views/pages/about.md"})

    def test_control_file_changes_detects_any_delta(self) -> None:
        prev: dict[str, Snapshot] = {"/tmp/schema.json": (100, 10), "/tmp/.iwp-lint.yaml": None}
        cur: dict[str, Snapshot] = {"/tmp/schema.json": (100, 10), "/tmp/.iwp-lint.yaml": (200, 20)}
        self.assertTrue(has_control_file_changes(prev, cur))


if __name__ == "__main__":
    unittest.main()
