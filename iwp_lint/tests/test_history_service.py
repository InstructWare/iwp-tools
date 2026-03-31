from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from iwp_lint.config import LintConfig, TrackingConfig, TrackingScopeConfig
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


def _tracking_ts() -> TrackingConfig:
    return TrackingConfig(
        protocol=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
        snapshot=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
    )


class HistoryServiceTests(unittest.TestCase):
    def test_history_checkpoint_creates_snapshot_and_checkpoint(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            payload = history.checkpoint(actor="test", message="dev loop savepoint")
            self.assertEqual(payload["status"], "ok")
            self.assertIsInstance(payload["checkpoint_id"], int)
            self.assertIsInstance(payload["snapshot_id"], int)
            self.assertEqual(payload["baseline_id_after"], payload["snapshot_id"])
            listed = history.list_checkpoints()
            self.assertGreaterEqual(len(listed["checkpoints"]), 1)
            self.assertEqual(
                str(listed["checkpoints"][0]["source"]),
                "history_checkpoint",
            )
            self.assertIsInstance(listed["checkpoints"][0].get("git_commit_oid"), str)
            self.assertTrue(str(listed["checkpoints"][0]["git_commit_oid"]))

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
                tracking=_tracking_ts(),
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
                tracking=_tracking_ts(),
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

    def test_history_restore_blocks_open_session_without_force(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="for restore")
            session_service = SessionService(config)
            started = session_service.start()
            blocked = history.restore(to_checkpoint_id=int(checkpoint["checkpoint_id"]))
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["blocked_reason"], "open_session")
            self.assertEqual(blocked["active_session_id"], str(started["session_id"]))
            forced = history.restore(to_checkpoint_id=int(checkpoint["checkpoint_id"]), force=True)
            self.assertEqual(forced["status"], "applied")

    def test_history_restore_prefers_dulwich_snapshot_over_sqlite_row_content(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="dulwich source of truth")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            snapshot_id = int(checkpoint["snapshot_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite3.connect(db_path) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE snapshot_files
                        SET content = ?, digest = ?, size = ?
                        WHERE snapshot_id = ? AND path = ?
                        """,
                        (
                            "# Architecture\n\n## Layout Tree\n- Tampered\n",
                            "tampered-digest",
                            41,
                            snapshot_id,
                            "InstructWare.iw/architecture.md",
                        ),
                    )
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Dirty\n")
            restored = history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertEqual(restored["status"], "applied")
            restored_content = md_file.read_text(encoding="utf-8")
            self.assertIn("- Alpha", restored_content)
            self.assertNotIn("Tampered", restored_content)

    def test_history_checkpoint_does_not_advance_baseline_on_checkpoint_write_failure(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            first = history.checkpoint(message="initial baseline")
            baseline_before_failed_checkpoint = int(first["snapshot_id"])
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Beta\n")
            with patch.object(
                history._backend, "create_checkpoint", side_effect=RuntimeError("boom")
            ):
                with self.assertRaises(RuntimeError):
                    history.checkpoint(message="should fail")
            listed = history.list_checkpoints()
            self.assertEqual(
                int(listed["current_baseline_snapshot_id"]),
                baseline_before_failed_checkpoint,
            )
            self.assertEqual(len(listed["checkpoints"]), 1)

    def test_history_restore_falls_back_to_sqlite_when_git_commit_unavailable(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="fallback source")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite3.connect(db_path) as conn:
                with conn:
                    conn.execute(
                        "UPDATE checkpoints SET git_commit_oid = ? WHERE id = ?",
                        ("invalid-commit-oid", checkpoint_id),
                    )
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Dirty\n")
            restored = history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertEqual(restored["status"], "applied")
            restored_content = md_file.read_text(encoding="utf-8")
            self.assertIn("- Alpha", restored_content)
            self.assertNotIn("- Dirty", restored_content)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT event_type, payload_json
                    FROM history_events
                    WHERE event_type = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ("restore_git_fallback",),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row[0]), "restore_git_fallback")
            payload = json.loads(str(row[1] or "{}"))
            self.assertEqual(int(payload.get("checkpoint_id")), checkpoint_id)
            self.assertEqual(str(payload.get("git_commit_oid")), "invalid-commit-oid")

    def test_history_restore_rejects_snapshot_path_outside_workspace(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="path guard baseline")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            snapshot_id = int(checkpoint["snapshot_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite3.connect(db_path) as conn:
                with conn:
                    conn.execute(
                        "UPDATE snapshot_files SET path = ? WHERE snapshot_id = ? AND path = ?",
                        ("../escape.txt", snapshot_id, "InstructWare.iw/architecture.md"),
                    )
                    conn.execute(
                        "UPDATE checkpoints SET git_commit_oid = ? WHERE id = ?",
                        ("invalid-commit-oid", checkpoint_id),
                    )
            with self.assertRaises(RuntimeError) as raised:
                history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertIn("escapes workspace root", str(raised.exception))
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_history_checkpoint_blocks_when_lock_exists(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            lock_path = (root / ".iwp/cache/history.lock").resolve()
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text("occupied", encoding="utf-8")
            history = HistoryService(config)
            with (
                patch("iwp_lint.core.history_service.time.monotonic", side_effect=[0.0, 3.0]),
                patch("iwp_lint.core.history_service.time.sleep", return_value=None),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    history.checkpoint(message="should block")
            self.assertIn("another history operation is in progress", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
