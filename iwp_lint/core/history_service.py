from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import LintConfig
from ..vcs.snapshot_store import SnapshotFile, SnapshotStore, collect_workspace_files


class HistoryService:
    def __init__(self, config: LintConfig) -> None:
        self._config = config
        self._db_path = (config.project_root / config.snapshot_db_file).resolve()
        self._store = SnapshotStore(self._db_path)

    def list_checkpoints(
        self, *, limit: int | None = None, include_stats: bool = True
    ) -> dict[str, Any]:
        checkpoints = self._store.list_checkpoints(limit=limit)
        payload: dict[str, Any] = {
            "snapshot_db_path": self._db_path.as_posix(),
            "current_baseline_snapshot_id": self._store.latest_snapshot_id(),
            "checkpoints": checkpoints,
        }
        if include_stats:
            payload["stats"] = self._store.history_stats()
        return payload

    def restore(
        self,
        *,
        to_checkpoint_id: int,
        dry_run: bool = False,
        force: bool = False,
        actor: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        checkpoint = self._store.get_checkpoint(to_checkpoint_id)
        if checkpoint is None:
            raise RuntimeError(f"checkpoint not found: {to_checkpoint_id}")
        current_files = self._collect_current_files()
        plan = self._build_restore_plan(
            target_checkpoint_id=to_checkpoint_id,
            current_workspace_files=current_files,
        )
        blocked = bool(
            self._config.history.safety.block_restore_on_dirty
            and bool(plan["is_workspace_dirty"])
            and not force
        )
        if blocked:
            self._store.append_history_event(
                "restore_blocked",
                {
                    "target_checkpoint_id": to_checkpoint_id,
                    "force": bool(force),
                    "dirty_files_count": len(plan["dirty_files"]),
                    "actor": actor or "",
                    "message": message or "",
                },
            )
            return {
                "status": "blocked",
                "blocked_reason": "dirty_workspace",
                "plan": plan,
            }
        if dry_run:
            self._store.append_history_event(
                "restore_dry_run",
                {
                    "target_checkpoint_id": to_checkpoint_id,
                    "force": bool(force),
                    "actor": actor or "",
                    "message": message or "",
                },
            )
            return {
                "status": "dry_run",
                "blocked_reason": None,
                "plan": plan,
            }

        before_snapshot_id: int | None = None
        before_checkpoint_id: int | None = None
        if self._config.history.safety.auto_checkpoint_before_restore:
            before_snapshot_id = self._store.create_snapshot(
                current_files,
                set_as_baseline=False,
            )
            before_checkpoint_id = self._store.create_checkpoint(
                snapshot_id=before_snapshot_id,
                source="restore_before_apply",
                baseline_snapshot_id=plan["current_baseline_snapshot_id"],
                gate_status="unknown",
                message=message or "",
                metadata={"actor": actor or ""},
            )

        target_snapshot = self._store.load_snapshot(plan["target_snapshot_id"])
        self._apply_restore_plan(
            project_root=self._config.project_root,
            target_snapshot=target_snapshot,
            to_delete=plan["to_delete"],
        )
        self._store.set_current_snapshot_id(plan["target_snapshot_id"])
        self._store.append_history_event(
            "restore_applied",
            {
                "target_checkpoint_id": to_checkpoint_id,
                "target_snapshot_id": plan["target_snapshot_id"],
                "before_snapshot_id": before_snapshot_id,
                "before_checkpoint_id": before_checkpoint_id,
                "force": bool(force),
                "actor": actor or "",
                "message": message or "",
            },
        )
        return {
            "status": "applied",
            "plan": plan,
            "before_snapshot_id": before_snapshot_id,
            "before_checkpoint_id": before_checkpoint_id,
            "current_baseline_snapshot_id": plan["target_snapshot_id"],
            "next_required_actions": [
                "iwp-build verify --config <cfg>",
                "iwp-build session reconcile --config <cfg> --preset agent-default",
            ],
        }

    def prune(
        self,
        *,
        max_snapshots: int | None = None,
        max_days: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        resolved_max_snapshots = int(
            max_snapshots
            if max_snapshots is not None
            else self._config.history.retention.max_snapshots
        )
        resolved_max_days = int(
            max_days if max_days is not None else self._config.history.retention.max_days
        )
        resolved_max_bytes = int(
            max_bytes if max_bytes is not None else self._config.history.retention.max_bytes
        )
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, resolved_max_days))
        rows = self._store.list_checkpoints()
        if not rows:
            return {
                "status": "ok",
                "removed_checkpoint_ids": [],
                "removed_snapshot_ids": [],
                "kept_checkpoint_ids": [],
            }
        latest_checkpoint_id = int(rows[0]["checkpoint_id"])
        latest_restore_before_id = next(
            (
                int(item["checkpoint_id"])
                for item in rows
                if str(item.get("source", "")) == "restore_before_apply"
            ),
            None,
        )
        protected = {latest_checkpoint_id}
        if latest_restore_before_id is not None:
            protected.add(latest_restore_before_id)
        keep_ids: list[int] = []
        drop_ids: list[int] = []
        byte_acc = 0
        snapshot_sizes = self._store.snapshot_sizes_by_id()
        kept_snapshots = 0
        for item in rows:
            checkpoint_id = int(item["checkpoint_id"])
            snapshot_id = int(item["snapshot_id"])
            created_at_text = str(item["created_at"])
            try:
                created_at = datetime.fromisoformat(created_at_text)
            except ValueError:
                created_at = now
            size = int(snapshot_sizes.get(snapshot_id, 0))
            should_keep = checkpoint_id in protected
            if not should_keep and created_at >= cutoff:
                should_keep = True
            if should_keep:
                keep_ids.append(checkpoint_id)
                kept_snapshots += 1
                byte_acc += size
                continue
            if kept_snapshots < max(1, resolved_max_snapshots) and byte_acc + size <= max(
                1, resolved_max_bytes
            ):
                keep_ids.append(checkpoint_id)
                kept_snapshots += 1
                byte_acc += size
            else:
                drop_ids.append(checkpoint_id)
        if not drop_ids:
            return {
                "status": "ok",
                "removed_checkpoint_ids": [],
                "removed_snapshot_ids": [],
                "kept_checkpoint_ids": sorted(keep_ids, reverse=True),
            }
        removed_snapshot_ids = self._store.delete_checkpoints_and_orphan_snapshots(drop_ids)
        payload = {
            "status": "ok",
            "removed_checkpoint_ids": sorted(drop_ids, reverse=True),
            "removed_snapshot_ids": sorted(removed_snapshot_ids),
            "kept_checkpoint_ids": sorted(keep_ids, reverse=True),
        }
        self._store.append_history_event(
            "prune_done",
            {
                "removed_checkpoint_count": len(drop_ids),
                "removed_snapshot_count": len(removed_snapshot_ids),
            },
        )
        return payload

    def _collect_current_files(self) -> list[SnapshotFile]:
        return collect_workspace_files(
            project_root=self._config.project_root,
            iwp_root=self._config.iwp_root,
            iwp_root_path=self._config.iwp_root_path,
            code_roots=self._config.code_roots,
            include_ext=self._config.include_ext,
            code_exclude_globs=self._config.code_exclude_globs,
            exclude_markdown_globs=self._config.schema_exclude_markdown_globs,
        )

    def _build_restore_plan(
        self,
        *,
        target_checkpoint_id: int,
        current_workspace_files: list[SnapshotFile],
    ) -> dict[str, Any]:
        checkpoint = self._store.get_checkpoint(target_checkpoint_id)
        if checkpoint is None:
            raise RuntimeError(f"checkpoint not found: {target_checkpoint_id}")
        target_snapshot_id = int(checkpoint["snapshot_id"])
        current_baseline_id = self._store.latest_snapshot_id()
        baseline_snapshot = (
            self._store.load_snapshot(current_baseline_id)
            if current_baseline_id is not None
            else {}
        )
        workspace_map = {item.path: item for item in current_workspace_files}
        dirty_files = sorted(set(self._compute_changed_paths(baseline_snapshot, workspace_map)))
        target_snapshot = self._store.load_snapshot(target_snapshot_id)
        to_write: list[dict[str, Any]] = []
        to_delete: list[str] = []
        for path, target in target_snapshot.items():
            current_item = workspace_map.get(path)
            if current_item is None:
                to_write.append({"path": path, "change_kind": "added", "size": target.size})
                continue
            if current_item.digest != target.digest:
                to_write.append({"path": path, "change_kind": "modified", "size": target.size})
        for path in workspace_map.keys():
            if path not in target_snapshot:
                to_delete.append(path)
        return {
            "target_checkpoint_id": target_checkpoint_id,
            "target_snapshot_id": target_snapshot_id,
            "current_baseline_snapshot_id": current_baseline_id,
            "dirty_files": dirty_files,
            "is_workspace_dirty": len(dirty_files) > 0,
            "to_write": sorted(to_write, key=lambda item: str(item["path"])),
            "to_delete": sorted(to_delete),
        }

    @staticmethod
    def _compute_changed_paths(
        baseline: dict[str, SnapshotFile],
        workspace: dict[str, SnapshotFile],
    ) -> list[str]:
        changed: list[str] = []
        all_paths = set(baseline.keys()) | set(workspace.keys())
        for path in sorted(all_paths):
            before = baseline.get(path)
            after = workspace.get(path)
            if before is None or after is None:
                changed.append(path)
                continue
            if before.digest != after.digest:
                changed.append(path)
        return changed

    def _apply_restore_plan(
        self,
        *,
        project_root: Path,
        target_snapshot: dict[str, SnapshotFile],
        to_delete: list[str],
    ) -> None:
        for rel_path in to_delete:
            abs_path = (project_root / rel_path).resolve()
            if abs_path.exists() and abs_path.is_file():
                abs_path.unlink()
        for rel_path, item in target_snapshot.items():
            abs_path = (project_root / rel_path).resolve()
            self._write_text_atomic(abs_path, item.content or "")

    @staticmethod
    def _write_text_atomic(target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent.as_posix(),
            delete=False,
        ) as tmp:
            tmp.write(text)
            temp_name = tmp.name
        os.replace(temp_name, target.as_posix())
