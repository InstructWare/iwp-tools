from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from ..config import LintConfig
from ..vcs.snapshot_store import SnapshotFile, SnapshotStore, collect_workspace_files


class HistoryBackend(Protocol):
    @property
    def db_path(self) -> Path: ...

    def collect_current_files(self) -> list[SnapshotFile]: ...

    def list_checkpoints(self, *, limit: int | None = None) -> list[dict[str, object]]: ...

    def latest_snapshot_id(self) -> int | None: ...

    def history_stats(self) -> dict[str, Any]: ...

    def create_snapshot(
        self, files: list[SnapshotFile], *, set_as_baseline: bool = True
    ) -> int: ...

    def create_git_checkpoint(
        self,
        *,
        files: list[SnapshotFile],
        source: str,
        actor: str | None = None,
        message: str | None = None,
    ) -> str | None: ...

    def create_checkpoint(
        self,
        *,
        snapshot_id: int,
        source: str,
        session_id: str | None = None,
        baseline_snapshot_id: int | None = None,
        gate_status: str = "unknown",
        git_commit_oid: str | None = None,
        message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> int: ...

    def append_history_event(
        self, event_type: str, payload: dict[str, object] | None = None
    ) -> None: ...

    def get_checkpoint(self, checkpoint_id: int) -> dict[str, object] | None: ...

    def latest_session(self, *, status: str | None = None) -> dict[str, object] | None: ...

    def load_snapshot(self, snapshot_id: int) -> dict[str, SnapshotFile]: ...

    def load_snapshot_for_checkpoint(
        self,
        checkpoint: dict[str, object],
        *,
        strict_dulwich_restore: bool = False,
        allow_sqlite_fallback: bool = True,
    ) -> dict[str, SnapshotFile]: ...

    def set_current_snapshot_id(self, snapshot_id: int | None) -> None: ...

    def snapshot_sizes_by_id(self) -> dict[int, int]: ...

    def delete_checkpoints_and_orphan_snapshots(self, checkpoint_ids: list[int]) -> list[int]: ...

    def gc(self, *, dry_run: bool = False) -> dict[str, int]: ...


class SnapshotStoreHistoryBackend:
    def __init__(self, config: LintConfig) -> None:
        self._config = config
        self._db_path = (config.project_root / config.snapshot_db_file).resolve()
        self._store = SnapshotStore(self._db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def collect_current_files(self) -> list[SnapshotFile]:
        return collect_workspace_files(
            project_root=self._config.project_root,
            iwp_root=self._config.iwp_root,
            iwp_root_path=self._config.iwp_root_path,
            code_roots=self._config.code_roots,
            include_ext=self._config.snapshot_include_ext,
            code_exclude_globs=self._config.snapshot_exclude_globs,
            exclude_markdown_globs=self._config.schema_exclude_markdown_globs,
            max_file_size_bytes=self._config.snapshot_max_file_size_bytes,
        )

    def list_checkpoints(self, *, limit: int | None = None) -> list[dict[str, object]]:
        return self._store.list_checkpoints(limit=limit)

    def latest_snapshot_id(self) -> int | None:
        return self._store.latest_snapshot_id()

    def history_stats(self) -> dict[str, Any]:
        return self._store.history_stats()

    def create_snapshot(self, files: list[SnapshotFile], *, set_as_baseline: bool = True) -> int:
        return self._store.create_snapshot(files, set_as_baseline=set_as_baseline)

    def create_git_checkpoint(
        self,
        *,
        files: list[SnapshotFile],
        source: str,
        actor: str | None = None,
        message: str | None = None,
    ) -> str | None:
        return None

    def create_checkpoint(
        self,
        *,
        snapshot_id: int,
        source: str,
        session_id: str | None = None,
        baseline_snapshot_id: int | None = None,
        gate_status: str = "unknown",
        git_commit_oid: str | None = None,
        message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> int:
        return self._store.create_checkpoint(
            snapshot_id=snapshot_id,
            source=source,
            session_id=session_id,
            baseline_snapshot_id=baseline_snapshot_id,
            gate_status=gate_status,
            git_commit_oid=git_commit_oid,
            message=message,
            metadata=metadata,
        )

    def append_history_event(
        self, event_type: str, payload: dict[str, object] | None = None
    ) -> None:
        self._store.append_history_event(event_type, payload)

    def get_checkpoint(self, checkpoint_id: int) -> dict[str, object] | None:
        return self._store.get_checkpoint(checkpoint_id)

    def latest_session(self, *, status: str | None = None) -> dict[str, object] | None:
        return self._store.latest_session(status=status)

    def load_snapshot(self, snapshot_id: int) -> dict[str, SnapshotFile]:
        return self._store.load_snapshot(snapshot_id)

    def load_snapshot_for_checkpoint(
        self,
        checkpoint: dict[str, object],
        *,
        strict_dulwich_restore: bool = False,
        allow_sqlite_fallback: bool = True,
    ) -> dict[str, SnapshotFile]:
        if strict_dulwich_restore:
            checkpoint_id = checkpoint.get("checkpoint_id")
            raise RuntimeError(
                "strict dulwich restore requires history.backend=dulwich and git_commit_oid-backed "
                f"checkpoints; current backend cannot serve checkpoint {checkpoint_id}"
            )
        if not allow_sqlite_fallback:
            checkpoint_id = checkpoint.get("checkpoint_id")
            raise RuntimeError(
                "sqlite fallback is disabled, but current backend is snapshot-only; "
                f"cannot restore checkpoint {checkpoint_id}"
            )
        return self._store.load_snapshot(self._require_snapshot_id(checkpoint))

    def set_current_snapshot_id(self, snapshot_id: int | None) -> None:
        self._store.set_current_snapshot_id(snapshot_id)

    def snapshot_sizes_by_id(self) -> dict[int, int]:
        return self._store.snapshot_sizes_by_id()

    def delete_checkpoints_and_orphan_snapshots(self, checkpoint_ids: list[int]) -> list[int]:
        return self._store.delete_checkpoints_and_orphan_snapshots(checkpoint_ids)

    def gc(self, *, dry_run: bool = False) -> dict[str, int]:
        _ = dry_run
        return {
            "candidate_count": 0,
            "deleted_count": 0,
            "reclaimed_bytes": 0,
            "reachable_count": 0,
        }

    @staticmethod
    def _require_snapshot_id(checkpoint: dict[str, object]) -> int:
        value = checkpoint.get("snapshot_id")
        if isinstance(value, bool):
            raise RuntimeError("snapshot_id is not a valid integer value")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise RuntimeError("snapshot_id is not a valid integer value") from exc
        raise RuntimeError("snapshot_id is not a valid integer value")


class DulwichHistoryBackend(SnapshotStoreHistoryBackend):
    def __init__(self, config: LintConfig) -> None:
        super().__init__(config)
        self._repo_dir = (config.project_root / config.history.git_dir).resolve()
        self._repo_recovery_attempted = False

    def create_git_checkpoint(
        self,
        *,
        files: list[SnapshotFile],
        source: str,
        actor: str | None = None,
        message: str | None = None,
    ) -> str | None:
        try:
            from dulwich.objects import Commit
        except Exception as exc:
            raise RuntimeError("dulwich is not available; install dependencies and retry") from exc
        repo = self._open_or_init_repo()
        tree_id = self._write_tree(repo, files)
        ref_name = b"refs/iwp-history/checkpoints"
        try:
            parent_oid = repo.refs[ref_name]
        except KeyError:
            parent_oid = None
        now = int(time.time())
        actor_name = (actor or "iwp-history").strip() or "iwp-history"
        identity = f"{actor_name} <iwp@local>".encode()
        commit = Commit()
        commit.tree = tree_id
        commit.author = identity
        commit.committer = identity
        commit.author_time = now
        commit.commit_time = now
        commit.author_timezone = 0
        commit.commit_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = ((message or "").strip() or f"{source} checkpoint").encode("utf-8")
        commit.parents = [parent_oid] if parent_oid is not None else []
        repo.object_store.add_object(commit)
        repo.refs[ref_name] = commit.id
        return commit.id.decode("ascii")

    def load_snapshot_for_checkpoint(
        self,
        checkpoint: dict[str, object],
        *,
        strict_dulwich_restore: bool = False,
        allow_sqlite_fallback: bool = True,
    ) -> dict[str, SnapshotFile]:
        git_commit_oid_raw = checkpoint.get("git_commit_oid")
        git_commit_oid = str(git_commit_oid_raw).strip() if git_commit_oid_raw is not None else ""
        if not git_commit_oid:
            if strict_dulwich_restore:
                checkpoint_id = checkpoint.get("checkpoint_id")
                raise RuntimeError(
                    "strict dulwich restore blocked: missing git_commit_oid for checkpoint "
                    f"{checkpoint_id}. Create a new checkpoint via history/session commit first."
                )
            if not allow_sqlite_fallback:
                checkpoint_id = checkpoint.get("checkpoint_id")
                raise RuntimeError(
                    "sqlite fallback is disabled and git_commit_oid is missing for checkpoint "
                    f"{checkpoint_id}"
                )
            return super().load_snapshot_for_checkpoint(
                checkpoint,
                strict_dulwich_restore=False,
                allow_sqlite_fallback=allow_sqlite_fallback,
            )
        try:
            repo = self._open_or_init_repo()
            return self._read_snapshot_from_commit(repo, git_commit_oid)
        except Exception as exc:
            if not allow_sqlite_fallback:
                checkpoint_id = checkpoint.get("checkpoint_id")
                raise RuntimeError(
                    "restore failed while reading dulwich commit "
                    f"{git_commit_oid} for checkpoint {checkpoint_id}: {exc}"
                ) from exc
            checkpoint_id = checkpoint.get("checkpoint_id")
            self.append_history_event(
                "restore_git_fallback",
                {
                    "checkpoint_id": int(checkpoint_id)
                    if isinstance(checkpoint_id, int)
                    else str(checkpoint_id or ""),
                    "git_commit_oid": git_commit_oid,
                    "reason": str(exc),
                },
            )
            return super().load_snapshot_for_checkpoint(
                checkpoint,
                strict_dulwich_restore=False,
                allow_sqlite_fallback=allow_sqlite_fallback,
            )

    def _open_or_init_repo(self) -> Any:
        from dulwich.repo import Repo

        try:
            if self._repo_dir.exists():
                return Repo(str(self._repo_dir))
            return Repo.init_bare(str(self._repo_dir), mkdir=True)
        except Exception as exc:
            if self._repo_recovery_attempted:
                raise RuntimeError(
                    "history git repository is unavailable after a recovery attempt"
                ) from exc
            self._repo_recovery_attempted = True
            return self._recover_repo_after_corruption(exc)

    def _recover_repo_after_corruption(self, original_error: Exception) -> Any:
        from dulwich.repo import Repo

        now = datetime.now(timezone.utc)
        backup_path = self._repo_dir.with_name(
            f"{self._repo_dir.name}.corrupted.{now.strftime('%Y%m%d%H%M%S')}"
        )
        backup_path_text = ""
        backup_error = ""
        if self._repo_dir.exists():
            try:
                shutil.move(self._repo_dir.as_posix(), backup_path.as_posix())
                backup_path_text = backup_path.as_posix()
            except Exception as exc:
                backup_error = str(exc)
        if self._repo_dir.exists():
            raise RuntimeError(
                "history git repository appears corrupted, and automatic backup/reset failed"
                f": {self._repo_dir.as_posix()}; backup_error={backup_error or 'unknown'}"
            ) from original_error
        try:
            repo = Repo.init_bare(str(self._repo_dir), mkdir=True)
        except Exception as exc:
            raise RuntimeError(
                "history git repository appears corrupted, and automatic reinitialization failed"
            ) from exc
        self.append_history_event(
            "git_repo_corrupted_reinitialized",
            {
                "repo_dir": self._repo_dir.as_posix(),
                "backup_path": backup_path_text,
                "backup_error": backup_error,
                "reason": str(original_error),
            },
        )
        return repo

    def _write_tree(self, repo: Any, files: list[SnapshotFile]) -> Any:
        root: dict[str, object] = {}
        for item in files:
            if not item.path:
                continue
            current = root
            parts = [part for part in item.path.split("/") if part]
            if not parts:
                continue
            for part in parts[:-1]:
                next_node = current.get(part)
                if not isinstance(next_node, dict):
                    next_node = {}
                    current[part] = next_node
                current = next_node
            current[parts[-1]] = item
        return self._write_tree_node(repo, root)

    def _write_tree_node(self, repo: Any, node: dict[str, object]) -> Any:
        from dulwich.objects import Blob, Tree

        tree = Tree()
        for name in sorted(node.keys()):
            value = node[name]
            name_bytes = name.encode("utf-8")
            if isinstance(value, SnapshotFile):
                content = value.content or ""
                blob = Blob.from_string(content.encode("utf-8"))
                repo.object_store.add_object(blob)
                tree.add(name_bytes, 0o100644, blob.id)
                continue
            if isinstance(value, dict):
                subtree_id = self._write_tree_node(repo, value)
                tree.add(name_bytes, 0o040000, subtree_id)
        repo.object_store.add_object(tree)
        return tree.id

    def _read_snapshot_from_commit(self, repo: Any, commit_oid: str) -> dict[str, SnapshotFile]:
        commit_id = commit_oid.encode("ascii")
        commit = repo.object_store[commit_id]
        snapshot: dict[str, SnapshotFile] = {}
        self._read_tree_into_snapshot(
            repo=repo,
            tree_id=commit.tree,
            path_prefix="",
            out=snapshot,
        )
        return snapshot

    def _read_tree_into_snapshot(
        self,
        *,
        repo: Any,
        tree_id: Any,
        path_prefix: str,
        out: dict[str, SnapshotFile],
    ) -> None:
        tree = repo.object_store[tree_id]
        for entry in tree.items():
            name = entry.path.decode("utf-8")
            mode = int(entry.mode)
            object_id = entry.sha
            rel_path = f"{path_prefix}/{name}" if path_prefix else name
            if mode == 0o040000:
                self._read_tree_into_snapshot(
                    repo=repo,
                    tree_id=object_id,
                    path_prefix=rel_path,
                    out=out,
                )
                continue
            blob = repo.object_store[object_id]
            content_text = bytes(blob.data).decode("utf-8")
            digest = self._sha256_hex(content_text)
            out[rel_path] = SnapshotFile(
                path=rel_path,
                kind="markdown" if rel_path.endswith(".md") else "code",
                mtime_ns=0,
                size=len(content_text.encode("utf-8")),
                digest=digest,
                content=content_text,
            )

    def gc(self, *, dry_run: bool = False) -> dict[str, int]:
        if not self._repo_dir.exists():
            return super().gc(dry_run=dry_run)
        repo = self._open_or_init_repo()
        reachable = self._collect_reachable_object_oids(repo)
        candidate_count = 0
        deleted_count = 0
        estimated_reclaimed_bytes = 0
        for object_path, object_hex, object_size in self._iter_loose_object_files():
            if object_hex in reachable:
                continue
            candidate_count += 1
            estimated_reclaimed_bytes += object_size
            if dry_run:
                continue
            try:
                object_path.unlink()
                deleted_count += 1
            except FileNotFoundError:
                continue
        return {
            "candidate_count": candidate_count,
            "deleted_count": deleted_count if not dry_run else 0,
            "reclaimed_bytes": estimated_reclaimed_bytes,
            "reachable_count": len(reachable),
        }

    def _collect_reachable_object_oids(self, repo: Any) -> set[str]:
        from dulwich.objects import Commit, Tag, Tree

        reachable: set[str] = set()
        stack: list[str] = []
        for oid in self._store.list_referenced_git_commit_oids():
            normalized = self._normalize_oid_hex(oid)
            if normalized:
                stack.append(normalized)
        for value in repo.refs.as_dict().values():
            normalized = self._normalize_oid_hex(value)
            if normalized:
                stack.append(normalized)
        while stack:
            oid_hex = stack.pop()
            if oid_hex in reachable:
                continue
            reachable.add(oid_hex)
            try:
                obj = repo.object_store[oid_hex.encode("ascii")]
            except Exception:
                continue
            if isinstance(obj, Commit):
                tree_hex = self._normalize_oid_hex(obj.tree)
                if tree_hex and tree_hex not in reachable:
                    stack.append(tree_hex)
                for parent in obj.parents:
                    parent_hex = self._normalize_oid_hex(parent)
                    if parent_hex and parent_hex not in reachable:
                        stack.append(parent_hex)
                continue
            if isinstance(obj, Tree):
                for entry in obj.items():
                    child_hex = self._normalize_oid_hex(entry.sha)
                    if child_hex and child_hex not in reachable:
                        stack.append(child_hex)
                continue
            if isinstance(obj, Tag):
                tagged = getattr(obj, "object", None)
                if isinstance(tagged, tuple) and len(tagged) >= 2:
                    target_hex = self._normalize_oid_hex(tagged[1])
                    if target_hex and target_hex not in reachable:
                        stack.append(target_hex)
        return reachable

    def _iter_loose_object_files(self) -> list[tuple[Path, str, int]]:
        objects_dir = self._repo_dir / "objects"
        if not objects_dir.exists():
            return []
        candidates: list[tuple[Path, str, int]] = []
        for prefix_dir in objects_dir.iterdir():
            prefix = prefix_dir.name
            if (
                not prefix_dir.is_dir()
                or len(prefix) != 2
                or any(ch not in "0123456789abcdefABCDEF" for ch in prefix)
            ):
                continue
            for suffix_file in prefix_dir.iterdir():
                suffix = suffix_file.name
                if (
                    not suffix_file.is_file()
                    or len(suffix) != 38
                    or any(ch not in "0123456789abcdefABCDEF" for ch in suffix)
                ):
                    continue
                object_hex = f"{prefix}{suffix}".lower()
                try:
                    object_size = int(suffix_file.stat().st_size)
                except FileNotFoundError:
                    continue
                candidates.append((suffix_file, object_hex, object_size))
        return candidates

    @staticmethod
    def _normalize_oid_hex(value: object) -> str:
        if isinstance(value, bytes):
            if len(value) == 20:
                return value.hex().lower()
            try:
                text = value.decode("ascii").strip()
            except UnicodeDecodeError:
                return ""
            if len(text) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in text):
                return text.lower()
            return ""
        text = str(value).strip()
        if len(text) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return text.lower()
        return ""

    @staticmethod
    def _sha256_hex(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HistoryService:
    def __init__(
        self,
        config: LintConfig,
        *,
        backend: HistoryBackend | None = None,
    ) -> None:
        self._config = config
        if backend is not None:
            self._backend = backend
        elif config.history.backend == "dulwich":
            self._backend = DulwichHistoryBackend(config)
        else:
            self._backend = SnapshotStoreHistoryBackend(config)
        self._db_path = self._backend.db_path
        self._lock_path = (config.project_root / config.cache_dir / "history.lock").resolve()
        self._restore_txn_path = (
            config.project_root / config.cache_dir / "restore_transaction.v1.json"
        ).resolve()

    def list_checkpoints(
        self, *, limit: int | None = None, include_stats: bool = True
    ) -> dict[str, Any]:
        checkpoints = self._backend.list_checkpoints(limit=limit)
        pending_restore_txn = self._read_pending_restore_txn()
        payload: dict[str, Any] = {
            "snapshot_db_path": self._db_path.as_posix(),
            "current_baseline_snapshot_id": self._backend.latest_snapshot_id(),
            "checkpoints": checkpoints,
            "pending_restore_txn": pending_restore_txn,
        }
        if include_stats:
            payload["stats"] = self._backend.history_stats()
        return payload

    def checkpoint(
        self,
        *,
        actor: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        with self._history_operation_lock("checkpoint"):
            files = self._collect_current_files()
            baseline_before = self._backend.latest_snapshot_id()
            snapshot_id = self._backend.create_snapshot(files, set_as_baseline=False)
            checkpoint_message = (message or "").strip() or "history checkpoint"
            git_commit_oid = self._backend.create_git_checkpoint(
                files=files,
                source="history_checkpoint",
                actor=actor,
                message=checkpoint_message,
            )
            checkpoint_id = self._backend.create_checkpoint(
                snapshot_id=snapshot_id,
                source="history_checkpoint",
                baseline_snapshot_id=baseline_before,
                gate_status="skipped",
                git_commit_oid=git_commit_oid,
                message=checkpoint_message,
                metadata={"actor": actor or ""},
            )
            self._backend.set_current_snapshot_id(snapshot_id)
            self._backend.append_history_event(
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint_id,
                    "snapshot_id": snapshot_id,
                    "baseline_before": baseline_before,
                    "baseline_after": snapshot_id,
                    "file_count": len(files),
                    "git_commit_oid": git_commit_oid or "",
                    "actor": actor or "",
                    "message": checkpoint_message,
                },
            )
            return {
                "status": "ok",
                "checkpoint_id": checkpoint_id,
                "snapshot_id": snapshot_id,
                "baseline_id_before": baseline_before,
                "baseline_id_after": snapshot_id,
                "file_count": len(files),
                "message": checkpoint_message,
            }

    def restore(
        self,
        *,
        to_checkpoint_id: int,
        dry_run: bool = False,
        force: bool = False,
        actor: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        with self._history_operation_lock("restore"):
            self._recover_pending_restore_if_needed()
            checkpoint = self._backend.get_checkpoint(to_checkpoint_id)
            if checkpoint is None:
                raise RuntimeError(f"checkpoint not found: {to_checkpoint_id}")
            active_session = self._latest_active_session()
            if active_session is not None and not force:
                active_session_id = str(active_session.get("session_id", "")).strip()
                self._backend.append_history_event(
                    "restore_blocked",
                    {
                        "target_checkpoint_id": to_checkpoint_id,
                        "force": bool(force),
                        "blocked_reason": "open_session",
                        "active_session_id": active_session_id,
                        "actor": actor or "",
                        "message": message or "",
                    },
                )
                return {
                    "status": "blocked",
                    "blocked_reason": "open_session",
                    "active_session_id": active_session_id or None,
                    "next_required_actions": [
                        "iwp-build session current --config <cfg>",
                        "finish current session and start a new one after restore, or re-run with --force",
                    ],
                }
            current_files = self._collect_current_files()
            plan, target_snapshot = self._build_restore_plan(
                target_checkpoint_id=to_checkpoint_id,
                current_workspace_files=current_files,
            )
            blocked = bool(
                self._config.history.safety.block_restore_on_dirty
                and bool(plan["is_workspace_dirty"])
                and not force
            )
            if blocked:
                self._backend.append_history_event(
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
                self._backend.append_history_event(
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
                before_snapshot_id = self._backend.create_snapshot(
                    current_files,
                    set_as_baseline=False,
                )
                before_checkpoint_message = (
                    message or ""
                ).strip() or "restore before apply safety checkpoint"
                before_git_commit_oid = self._backend.create_git_checkpoint(
                    files=current_files,
                    source="restore_before_apply",
                    actor=actor,
                    message=before_checkpoint_message,
                )
                before_checkpoint_id = self._backend.create_checkpoint(
                    snapshot_id=before_snapshot_id,
                    source="restore_before_apply",
                    baseline_snapshot_id=plan["current_baseline_snapshot_id"],
                    gate_status="unknown",
                    git_commit_oid=before_git_commit_oid,
                    message=before_checkpoint_message,
                    metadata={"actor": actor or ""},
                )

            txn_payload = self._write_restore_transaction(
                target_checkpoint_id=to_checkpoint_id,
                target_snapshot_id=self._require_int(
                    plan["target_snapshot_id"], field="snapshot_id"
                ),
                before_checkpoint_id=before_checkpoint_id,
                before_snapshot_id=before_snapshot_id,
                actor=actor,
                message=message,
            )
            applied_ok = False
            try:
                self._apply_restore_plan(
                    project_root=self._config.project_root,
                    target_snapshot=target_snapshot,
                    to_delete=plan["to_delete"],
                )
                self._backend.set_current_snapshot_id(plan["target_snapshot_id"])
                self._backend.append_history_event(
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
                applied_ok = True
            except Exception as exc:
                self._backend.append_history_event(
                    "restore_apply_failed",
                    {
                        "target_checkpoint_id": to_checkpoint_id,
                        "target_snapshot_id": plan["target_snapshot_id"],
                        "before_snapshot_id": before_snapshot_id,
                        "before_checkpoint_id": before_checkpoint_id,
                        "reason": str(exc),
                    },
                )
                raise
            finally:
                if applied_ok and self._restore_txn_path.exists():
                    self._clear_restore_transaction(txn_payload)
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

    def _latest_active_session(self) -> dict[str, object] | None:
        active_statuses = ("open", "dirty", "verified", "blocked")
        latest: dict[str, object] | None = None
        latest_created_at = ""
        for status in active_statuses:
            candidate = self._backend.latest_session(status=status)
            if candidate is None:
                continue
            created_at = str(candidate.get("created_at", ""))
            if created_at >= latest_created_at:
                latest = candidate
                latest_created_at = created_at
        return latest

    def prune(
        self,
        *,
        max_snapshots: int | None = None,
        max_days: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        with self._history_operation_lock("prune"):
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
            rows = self._backend.list_checkpoints()
            if not rows:
                gc_stats = self._backend.gc(dry_run=False)
                return {
                    "status": "ok",
                    "removed_checkpoint_ids": [],
                    "removed_snapshot_ids": [],
                    "kept_checkpoint_ids": [],
                    "gc": gc_stats,
                }
            latest_checkpoint_id = self._require_int(
                rows[0]["checkpoint_id"], field="checkpoint_id"
            )
            latest_restore_before_id = next(
                (
                    self._require_int(item["checkpoint_id"], field="checkpoint_id")
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
            snapshot_sizes = self._backend.snapshot_sizes_by_id()
            kept_snapshots = 0
            for item in rows:
                checkpoint_id = self._require_int(item["checkpoint_id"], field="checkpoint_id")
                snapshot_id = self._require_int(item["snapshot_id"], field="snapshot_id")
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
                gc_stats = self._backend.gc(dry_run=False)
                return {
                    "status": "ok",
                    "removed_checkpoint_ids": [],
                    "removed_snapshot_ids": [],
                    "kept_checkpoint_ids": sorted(keep_ids, reverse=True),
                    "gc": gc_stats,
                }
            removed_snapshot_ids = self._backend.delete_checkpoints_and_orphan_snapshots(drop_ids)
            gc_stats = self._backend.gc(dry_run=False)
            payload = {
                "status": "ok",
                "removed_checkpoint_ids": sorted(drop_ids, reverse=True),
                "removed_snapshot_ids": sorted(removed_snapshot_ids),
                "kept_checkpoint_ids": sorted(keep_ids, reverse=True),
                "gc": gc_stats,
            }
            self._backend.append_history_event(
                "prune_done",
                {
                    "removed_checkpoint_count": len(drop_ids),
                    "removed_snapshot_count": len(removed_snapshot_ids),
                    "gc": gc_stats,
                },
            )
            return payload

    @contextmanager
    def _history_operation_lock(self, action: str):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        timeout_seconds = 2.0
        wait_interval_seconds = 0.05
        lock_fd = os.open(
            self._lock_path.as_posix(),
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        lock_id = uuid.uuid4().hex
        while True:
            try:
                self._acquire_file_lock(lock_fd)
                os.ftruncate(lock_fd, 0)
                os.write(lock_fd, f"{os.getpid()}:{lock_id}".encode())
                break
            except BlockingIOError as exc:
                if time.monotonic() - started >= timeout_seconds:
                    raise RuntimeError(
                        f"history {action} is blocked: another history operation is in progress"
                    ) from exc
                time.sleep(wait_interval_seconds)
        try:
            yield
        finally:
            try:
                self._release_file_lock(lock_fd)
            finally:
                os.close(lock_fd)

    @staticmethod
    def _acquire_file_lock(lock_fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            if os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"0")
            os.lseek(lock_fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                raise BlockingIOError("history lock is busy") from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as exc:
                raise BlockingIOError("history lock is busy") from exc

    @staticmethod
    def _release_file_lock(lock_fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(lock_fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                return
            return
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _collect_current_files(self) -> list[SnapshotFile]:
        return self._backend.collect_current_files()

    def _build_restore_plan(
        self,
        *,
        target_checkpoint_id: int,
        current_workspace_files: list[SnapshotFile],
    ) -> tuple[dict[str, Any], dict[str, SnapshotFile]]:
        checkpoint = self._backend.get_checkpoint(target_checkpoint_id)
        if checkpoint is None:
            raise RuntimeError(f"checkpoint not found: {target_checkpoint_id}")
        target_snapshot_id = self._require_int(checkpoint["snapshot_id"], field="snapshot_id")
        current_baseline_id = self._backend.latest_snapshot_id()
        baseline_snapshot = (
            self._backend.load_snapshot(current_baseline_id)
            if current_baseline_id is not None
            else {}
        )
        workspace_map = {item.path: item for item in current_workspace_files}
        dirty_files = sorted(set(self._compute_changed_paths(baseline_snapshot, workspace_map)))
        target_snapshot = self._backend.load_snapshot_for_checkpoint(
            checkpoint,
            strict_dulwich_restore=bool(
                getattr(self._config.history.safety, "strict_dulwich_restore", False)
            ),
            allow_sqlite_fallback=bool(
                getattr(self._config.history.safety, "allow_sqlite_fallback", True)
            ),
        )
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
        return (
            {
                "target_checkpoint_id": target_checkpoint_id,
                "target_snapshot_id": target_snapshot_id,
                "current_baseline_snapshot_id": current_baseline_id,
                "dirty_files": dirty_files,
                "is_workspace_dirty": len(dirty_files) > 0,
                "to_write": sorted(to_write, key=lambda item: str(item["path"])),
                "to_delete": sorted(to_delete),
            },
            target_snapshot,
        )

    @staticmethod
    def _require_int(value: object, *, field: str) -> int:
        if isinstance(value, bool):
            raise RuntimeError(f"{field} is not a valid integer value")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise RuntimeError(f"{field} is not a valid integer value") from exc
        raise RuntimeError(f"{field} is not a valid integer value")

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
        safe_delete_paths: list[Path] = []
        for rel_path in to_delete:
            abs_path = self._resolve_workspace_path_or_raise(
                project_root=project_root, rel_path=rel_path
            )
            safe_delete_paths.append(abs_path)
        safe_write_paths: list[tuple[Path, str]] = []
        for rel_path, item in target_snapshot.items():
            abs_path = self._resolve_workspace_path_or_raise(
                project_root=project_root, rel_path=rel_path
            )
            safe_write_paths.append((abs_path, item.content or ""))
        for abs_path in safe_delete_paths:
            if abs_path.exists() and abs_path.is_file():
                abs_path.unlink()
        for abs_path, content in safe_write_paths:
            self._write_text_atomic(abs_path, content)

    def _write_restore_transaction(
        self,
        *,
        target_checkpoint_id: int,
        target_snapshot_id: int,
        before_checkpoint_id: int | None,
        before_snapshot_id: int | None,
        actor: str | None,
        message: str | None,
    ) -> dict[str, object]:
        self._restore_txn_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": 1,
            "state": "applying",
            "target_checkpoint_id": target_checkpoint_id,
            "target_snapshot_id": target_snapshot_id,
            "before_checkpoint_id": before_checkpoint_id,
            "before_snapshot_id": before_snapshot_id,
            "actor": actor or "",
            "message": message or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = self._restore_txn_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path.as_posix(), self._restore_txn_path.as_posix())
        return payload

    def _clear_restore_transaction(self, txn_payload: dict[str, object]) -> None:
        if not self._restore_txn_path.exists():
            return
        self._restore_txn_path.unlink()
        self._backend.append_history_event(
            "restore_txn_cleared",
            {
                "target_checkpoint_id": txn_payload.get("target_checkpoint_id"),
                "target_snapshot_id": txn_payload.get("target_snapshot_id"),
            },
        )

    def _recover_pending_restore_if_needed(self) -> None:
        payload = self._read_pending_restore_txn()
        if payload is None:
            return
        if not isinstance(payload, dict):
            raise RuntimeError("restore transaction payload is invalid")
        if str(payload.get("state", "")).strip() != "applying":
            self._restore_txn_path.unlink()
            return
        before_checkpoint_id_raw = payload.get("before_checkpoint_id")
        if before_checkpoint_id_raw is None:
            raise RuntimeError(
                "detected incomplete restore transaction, but missing before_checkpoint_id for recovery"
            )
        before_checkpoint_id = self._require_int(
            before_checkpoint_id_raw, field="before_checkpoint_id"
        )
        checkpoint = self._backend.get_checkpoint(before_checkpoint_id)
        if checkpoint is None:
            raise RuntimeError(
                "detected incomplete restore transaction, but recovery checkpoint is unavailable"
            )
        recovery_snapshot = self._backend.load_snapshot_for_checkpoint(
            checkpoint,
            strict_dulwich_restore=False,
            allow_sqlite_fallback=True,
        )
        current_files = self._collect_current_files()
        workspace_map = {item.path: item for item in current_files}
        to_delete = sorted(path for path in workspace_map.keys() if path not in recovery_snapshot)
        self._apply_restore_plan(
            project_root=self._config.project_root,
            target_snapshot=recovery_snapshot,
            to_delete=to_delete,
        )
        before_snapshot_id_raw = payload.get("before_snapshot_id")
        if before_snapshot_id_raw is not None:
            self._backend.set_current_snapshot_id(
                self._require_int(before_snapshot_id_raw, field="before_snapshot_id")
            )
        self._backend.append_history_event(
            "restore_recovered",
            {
                "target_checkpoint_id": payload.get("target_checkpoint_id"),
                "target_snapshot_id": payload.get("target_snapshot_id"),
                "before_checkpoint_id": before_checkpoint_id,
                "before_snapshot_id": before_snapshot_id_raw,
            },
        )
        self._clear_restore_transaction(payload)

    def _read_pending_restore_txn(self) -> dict[str, object] | None:
        if not self._restore_txn_path.exists():
            return None
        try:
            payload = json.loads(self._restore_txn_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("restore transaction payload cannot be parsed") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("restore transaction payload is invalid")
        return payload

    @staticmethod
    def _resolve_workspace_path_or_raise(*, project_root: Path, rel_path: str) -> Path:
        resolved = (project_root / rel_path).resolve()
        try:
            resolved.relative_to(project_root.resolve())
        except ValueError as exc:
            raise RuntimeError(f"restore path escapes workspace root: {rel_path}") from exc
        return resolved

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
