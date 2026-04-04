from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from iwp_lint.config import LintConfig, TrackingConfig, TrackingScopeConfig, load_config
from iwp_lint.core.history_service import HistoryService
from iwp_lint.core.session_service import SessionService
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.tests.helpers import sqlite_conn


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


def _tracking_ts(*, snapshot_max_file_size_kb: int = 5120) -> TrackingConfig:
    return TrackingConfig(
        protocol=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
        snapshot=TrackingScopeConfig(
            include_ext=[".ts"],
            exclude_globs=[],
            max_file_size_kb=snapshot_max_file_size_kb,
        ),
    )


@contextmanager
def _occupy_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path.as_posix(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"0")
            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(lock_fd, 0, os.SEEK_SET)
                msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


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
            listed_after_restore = history.list_checkpoints()
            restore_before_rows = [
                item
                for item in listed_after_restore["checkpoints"]
                if str(item.get("source", "")) == "restore_before_apply"
            ]
            self.assertTrue(restore_before_rows)
            self.assertTrue(str(restore_before_rows[0].get("git_commit_oid", "")).strip())

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
            with sqlite_conn(db_path) as conn:
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
            with sqlite_conn(db_path) as conn:
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
            with sqlite_conn(db_path) as conn:
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

    def test_history_restore_strict_mode_blocks_checkpoint_without_git_oid(self) -> None:
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
            config.history.safety.strict_dulwich_restore = True
            config.history.safety.allow_sqlite_fallback = True
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="strict check")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite_conn(db_path) as conn:
                with conn:
                    conn.execute(
                        "UPDATE checkpoints SET git_commit_oid = NULL WHERE id = ?",
                        (checkpoint_id,),
                    )
            with self.assertRaises(RuntimeError) as raised:
                history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertIn("missing git_commit_oid", str(raised.exception))

    def test_history_restore_non_strict_can_use_sqlite_fallback_when_enabled(self) -> None:
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
            config.history.safety.strict_dulwich_restore = False
            config.history.safety.allow_sqlite_fallback = True
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="fallback enabled")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite_conn(db_path) as conn:
                with conn:
                    conn.execute(
                        "UPDATE checkpoints SET git_commit_oid = NULL WHERE id = ?",
                        (checkpoint_id,),
                    )
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Dirty\n")
            restored = history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertEqual(restored["status"], "applied")
            self.assertIn("- Alpha", md_file.read_text(encoding="utf-8"))

    def test_history_restore_blocks_when_sqlite_fallback_disabled_and_oid_missing(self) -> None:
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
            config.history.safety.strict_dulwich_restore = False
            config.history.safety.allow_sqlite_fallback = False
            history = HistoryService(config)
            checkpoint = history.checkpoint(message="fallback disabled")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite_conn(db_path) as conn:
                with conn:
                    conn.execute(
                        "UPDATE checkpoints SET git_commit_oid = NULL WHERE id = ?",
                        (checkpoint_id,),
                    )
            with self.assertRaises(RuntimeError) as raised:
                history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertIn("sqlite fallback is disabled", str(raised.exception))

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
            with sqlite_conn(db_path) as conn:
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

    def test_history_restore_recovers_from_interrupted_apply_via_txn_marker(self) -> None:
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
            checkpoint = history.checkpoint(message="txn baseline")
            checkpoint_id = int(checkpoint["checkpoint_id"])
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Dirty\n")
            marker_path = (root / ".iwp/cache/restore_transaction.v1.json").resolve()

            original_writer = HistoryService._write_text_atomic
            call_counter = {"count": 0}

            def _flaky_write(target: Path, text: str) -> None:
                call_counter["count"] += 1
                if call_counter["count"] == 1:
                    raise RuntimeError("simulated interrupted restore apply")
                original_writer(target, text)

            with patch.object(HistoryService, "_write_text_atomic", side_effect=_flaky_write):
                with self.assertRaises(RuntimeError):
                    history.restore(to_checkpoint_id=checkpoint_id, force=True)

            self.assertTrue(marker_path.exists())
            listed_with_pending = history.list_checkpoints()
            pending_obj = listed_with_pending.get("pending_restore_txn")
            self.assertIsInstance(pending_obj, dict)
            pending = pending_obj if isinstance(pending_obj, dict) else {}
            self.assertEqual(str(pending.get("state")), "applying")
            pending_target = pending.get("target_checkpoint_id")
            self.assertIsInstance(pending_target, int)
            self.assertEqual(
                int(pending_target) if isinstance(pending_target, int) else -1, checkpoint_id
            )
            recovered = history.restore(to_checkpoint_id=checkpoint_id, force=True)
            self.assertEqual(recovered["status"], "applied")
            self.assertFalse(marker_path.exists())
            listed_after_recovery = history.list_checkpoints()
            self.assertIsNone(listed_after_recovery.get("pending_restore_txn"))
            restored_content = md_file.read_text(encoding="utf-8")
            self.assertIn("- Alpha", restored_content)
            self.assertNotIn("- Dirty", restored_content)
            db_path = (root / ".iwp/cache/snapshots.sqlite").resolve()
            with sqlite_conn(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT event_type
                    FROM history_events
                    WHERE event_type = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ("restore_recovered",),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row[0]), "restore_recovered")

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
            history = HistoryService(config)
            with _occupy_lock(lock_path):
                with (
                    patch("iwp_lint.core.history_service.time.monotonic", side_effect=[0.0, 3.0]),
                    patch("iwp_lint.core.history_service.time.sleep", return_value=None),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        history.checkpoint(message="should block")
            self.assertIn("another history operation is in progress", str(raised.exception))

    def test_history_checkpoint_rejects_oversized_snapshot_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            big_code_file = root / "_ir/src/big.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(big_code_file, "x" * 2048)
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(snapshot_max_file_size_kb=1),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            history = HistoryService(config)
            with self.assertRaises(RuntimeError) as raised:
                history.checkpoint(message="oversize should fail")
            message = str(raised.exception)
            self.assertIn("snapshot file exceeds configured max size", message)
            self.assertIn("_ir/src/big.ts", message)

    def test_tracking_snapshot_max_file_size_kb_requires_positive_integer(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            non_number_cfg = root / "non-number.yaml"
            negative_cfg = root / "negative.yaml"
            non_number_cfg.write_text(
                (
                    "tracking:\n"
                    "  protocol:\n"
                    "    include_ext: ['.ts']\n"
                    "    exclude_globs: []\n"
                    "  snapshot:\n"
                    "    include_ext: ['.ts']\n"
                    "    exclude_globs: []\n"
                    "    max_file_size_kb: abc\n"
                ),
                encoding="utf-8",
            )
            negative_cfg.write_text(
                (
                    "tracking:\n"
                    "  protocol:\n"
                    "    include_ext: ['.ts']\n"
                    "    exclude_globs: []\n"
                    "  snapshot:\n"
                    "    include_ext: ['.ts']\n"
                    "    exclude_globs: []\n"
                    "    max_file_size_kb: -1\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError) as non_number_error:
                load_config(str(non_number_cfg), cwd=root)
            self.assertIn(
                "tracking.snapshot.max_file_size_kb",
                str(non_number_error.exception),
            )
            with self.assertRaises(RuntimeError) as negative_error:
                load_config(str(negative_cfg), cwd=root)
            self.assertIn(
                "tracking.snapshot.max_file_size_kb",
                str(negative_error.exception),
            )

    def test_history_checkpoint_reinitializes_corrupted_git_repo_once(self) -> None:
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
            history.checkpoint(message="baseline")
            repo_dir = (root / ".iwp/cache/history.git").resolve()
            shutil.rmtree(repo_dir)
            repo_dir.write_text("corrupted repo marker", encoding="utf-8")
            payload = history.checkpoint(message="after corruption")
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(repo_dir.is_dir())
            backup_candidates = sorted(repo_dir.parent.glob("history.git.corrupted.*"))
            self.assertTrue(backup_candidates)
            with sqlite_conn(root / ".iwp/cache/snapshots.sqlite") as conn:
                row = conn.execute(
                    """
                    SELECT event_type
                    FROM history_events
                    WHERE event_type = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ("git_repo_corrupted_reinitialized",),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row[0]), "git_repo_corrupted_reinitialized")

    def test_history_prune_runs_safe_gc_and_removes_orphan_loose_objects(self) -> None:
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
            history.checkpoint(message="gc baseline")

            from dulwich.objects import Blob
            from dulwich.repo import Repo

            repo_dir = (root / ".iwp/cache/history.git").resolve()
            repo = Repo(str(repo_dir))
            orphan = Blob.from_string(b"orphan-loose-object")
            repo.object_store.add_object(orphan)
            orphan_oid = orphan.id.decode("ascii")
            orphan_path = repo_dir / "objects" / orphan_oid[:2] / orphan_oid[2:]
            self.assertTrue(orphan_path.exists())

            prune_payload = history.prune(max_snapshots=1, max_days=1, max_bytes=1)
            self.assertEqual(prune_payload["status"], "ok")
            gc_payload_obj = prune_payload.get("gc")
            self.assertIsInstance(gc_payload_obj, dict)
            gc_payload = gc_payload_obj if isinstance(gc_payload_obj, dict) else {}
            self.assertGreaterEqual(int(gc_payload.get("candidate_count", 0)), 1)
            self.assertGreaterEqual(int(gc_payload.get("deleted_count", 0)), 1)
            self.assertFalse(orphan_path.exists())


if __name__ == "__main__":
    unittest.main()
