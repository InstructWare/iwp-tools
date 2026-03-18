from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from iwp_build.cli import _run_build
from iwp_lint.config import LintConfig
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.vcs.snapshot_store import SnapshotStore


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_schema() -> dict:
    return {
        "schema_name": "test",
        "schema_version": "1.0.0",
        "modes": {"default": "compat", "supported": ["compat", "strict"]},
        "global_rules": {
            "h1_required_exactly_one": True,
            "h2_unknown_policy": {"compat": "warn", "strict": "error"},
        },
        "kind_rules": {"format": "{file_type_id}.{section_key}"},
        "section_i18n": {
            "layout_tree": {"en": ["Layout Tree"]},
        },
        "file_type_schemas": [
            {
                "id": "docs",
                "path_patterns": ["**/*.md", "*.md"],
                "sections": [{"key": "layout_tree", "required": True}],
            }
        ],
    }


def _workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = Path.cwd() / ".tmp_iwp_build_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


class IwpBuildCommitTests(unittest.TestCase):
    def test_build_does_not_advance_baseline_when_gap_errors_exist(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            md_file = iwp_root / "architecture.md"
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_file = root / "_ir/src/iwp_links.ts"
            initial_links = [f"// @iwp.link architecture.md::{node.node_id}" for node in nodes]
            _write(link_file, "\n".join(initial_links) + "\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )

            first_exit = _run_build(config, mode="auto", json_path=None)
            self.assertEqual(first_exit, 0)
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            baseline_before = store.latest_snapshot_id()
            self.assertIsNotNone(baseline_before)

            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            second_exit = _run_build(config, mode="diff", json_path=None)
            self.assertEqual(second_exit, 1)

            baseline_after_failure = store.latest_snapshot_id()
            self.assertEqual(baseline_after_failure, baseline_before)

            new_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                f"// @iwp.link architecture.md::{next(node.node_id for node in new_nodes if node.anchor_text == 'Beta')}\n",
            )

            third_exit = _run_build(config, mode="diff", json_path=None)
            self.assertEqual(third_exit, 0)
            baseline_after_success = store.latest_snapshot_id()
            assert baseline_after_success is not None and baseline_before is not None
            self.assertGreater(baseline_after_success, baseline_before)


if __name__ == "__main__":
    unittest.main()
