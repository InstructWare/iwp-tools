from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from iwp_lint.config import LintConfig
from iwp_lint.core.history_service import HistoryService
from iwp_lint.core.session_service import SessionService
from iwp_lint.parsers.md_parser import parse_markdown_nodes


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
        "section_i18n": {"layout_tree": {"en": ["Layout Tree"]}},
        "file_type_schemas": [
            {
                "id": "docs",
                "path_patterns": ["**/*.md", "*.md"],
                "sections": [{"key": "layout_tree"}],
            }
        ],
    }


def _workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = Path.cwd() / ".tmp_iwp_lint_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


class HistoryServiceTests(unittest.TestCase):
    def test_history_restore_supports_jump_back_and_forward(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
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
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            session_service = SessionService(config)
            first = session_service.start()
            first_commit = session_service.commit(
                str(first["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )
            checkpoint_1 = int(first_commit["checkpoint_id"])

            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            second = session_service.start()
            second_commit = session_service.commit(
                str(second["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )
            checkpoint_2 = int(second_commit["checkpoint_id"])

            history = HistoryService(config)
            listed = history.list_checkpoints()
            self.assertGreaterEqual(len(listed["checkpoints"]), 2)

            dry_run = history.restore(to_checkpoint_id=checkpoint_1, dry_run=True, force=True)
            self.assertEqual(dry_run["status"], "dry_run")

            restored_first = history.restore(to_checkpoint_id=checkpoint_1, force=True)
            self.assertEqual(restored_first["status"], "applied")
            self.assertIn("- Alpha\n", md_file.read_text(encoding="utf-8"))
            self.assertNotIn("- Beta", md_file.read_text(encoding="utf-8"))

            restored_second = history.restore(to_checkpoint_id=checkpoint_2, force=True)
            self.assertEqual(restored_second["status"], "applied")
            self.assertIn("- Beta", md_file.read_text(encoding="utf-8"))

    def test_history_restore_blocks_dirty_workspace_without_force(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
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
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            session_service = SessionService(config)
            started = session_service.start()
            committed = session_service.commit(
                str(started["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )
            checkpoint_id = int(committed["checkpoint_id"])
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Dirty\n")
            history = HistoryService(config)
            blocked = history.restore(to_checkpoint_id=checkpoint_id)
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["blocked_reason"], "dirty_workspace")


if __name__ == "__main__":
    unittest.main()
