from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..parsers.comment_scanner import discover_code_files
from ..schema.schema_validator import list_markdown_rel_paths


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    kind: str
    mtime_ns: int
    size: int
    digest: str
    content: str | None


class SnapshotStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def ensure(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS snapshot_files (
                        snapshot_id INTEGER NOT NULL,
                        path TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        size INTEGER NOT NULL,
                        digest TEXT NOT NULL,
                        content TEXT,
                        PRIMARY KEY (snapshot_id, path),
                        FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_snapshot_files_path ON snapshot_files(path)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS baseline_state (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        current_snapshot_id INTEGER,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        baseline_id_before INTEGER,
                        baseline_id_after INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        committed_at TEXT,
                        metadata_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        session_id TEXT,
                        baseline_snapshot_id INTEGER,
                        gate_status TEXT NOT NULL,
                        git_commit_oid TEXT,
                        message TEXT,
                        metadata_json TEXT,
                        FOREIGN KEY (snapshot_id) REFERENCES snapshots(id),
                        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_checkpoints_snapshot_id ON checkpoints(snapshot_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at ON checkpoints(created_at)"
                )
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()
                    if len(row) > 1
                }
                if "git_commit_oid" not in columns:
                    conn.execute("ALTER TABLE checkpoints ADD COLUMN git_commit_oid TEXT")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        payload_json TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_history_events_created_at ON history_events(created_at)"
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO baseline_state(id, current_snapshot_id, updated_at)
                    VALUES (1, NULL, ?)
                    """,
                    (datetime.now(timezone.utc).isoformat(),),
                )

    def create_snapshot(self, files: list[SnapshotFile], *, set_as_baseline: bool = True) -> int:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT INTO snapshots(created_at) VALUES (?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("failed to create snapshot row")
                snapshot_id = int(cur.lastrowid)
                conn.executemany(
                    """
                    INSERT INTO snapshot_files(
                        snapshot_id, path, kind, mtime_ns, size, digest, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            snapshot_id,
                            item.path,
                            item.kind,
                            item.mtime_ns,
                            item.size,
                            item.digest,
                            item.content,
                        )
                        for item in files
                    ],
                )
                if set_as_baseline:
                    conn.execute(
                        """
                        UPDATE baseline_state
                        SET current_snapshot_id = ?, updated_at = ?
                        WHERE id = 1
                        """,
                        (snapshot_id, datetime.now(timezone.utc).isoformat()),
                    )
            return snapshot_id

    def latest_snapshot_id(self) -> int | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT current_snapshot_id FROM baseline_state WHERE id = 1"
            ).fetchone()
        if not row or row[0] is None:
            with closing(sqlite3.connect(self.db_path)) as conn:
                fallback = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()
            if not fallback or fallback[0] is None:
                return None
            return int(fallback[0])
        return int(row[0])

    def set_current_snapshot_id(self, snapshot_id: int | None) -> None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE baseline_state
                    SET current_snapshot_id = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (snapshot_id, datetime.now(timezone.utc).isoformat()),
                )

    def latest_checkpoint(self) -> dict[str, object] | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT id, snapshot_id, created_at, source, session_id,
                       baseline_snapshot_id, gate_status, git_commit_oid, message, metadata_json
                FROM checkpoints
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": int(row[0]),
            "snapshot_id": int(row[1]),
            "created_at": str(row[2]),
            "source": str(row[3]),
            "session_id": str(row[4]) if row[4] is not None else None,
            "baseline_snapshot_id": int(row[5]) if row[5] is not None else None,
            "gate_status": str(row[6]),
            "git_commit_oid": str(row[7]) if row[7] is not None else None,
            "message": str(row[8] or ""),
            "metadata": json.loads(row[9]) if row[9] else {},
        }

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
        self.ensure()
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO checkpoints(
                        snapshot_id, created_at, source, session_id,
                        baseline_snapshot_id, gate_status, git_commit_oid, message, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        now,
                        source,
                        session_id,
                        baseline_snapshot_id,
                        gate_status,
                        git_commit_oid,
                        message or "",
                        metadata_json,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("failed to create checkpoint row")
                return int(cur.lastrowid)

    def get_checkpoint(self, checkpoint_id: int) -> dict[str, object] | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT id, snapshot_id, created_at, source, session_id,
                       baseline_snapshot_id, gate_status, git_commit_oid, message, metadata_json
                FROM checkpoints
                WHERE id = ?
                """,
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": int(row[0]),
            "snapshot_id": int(row[1]),
            "created_at": str(row[2]),
            "source": str(row[3]),
            "session_id": str(row[4]) if row[4] is not None else None,
            "baseline_snapshot_id": int(row[5]) if row[5] is not None else None,
            "gate_status": str(row[6]),
            "git_commit_oid": str(row[7]) if row[7] is not None else None,
            "message": str(row[8] or ""),
            "metadata": json.loads(row[9]) if row[9] else {},
        }

    def list_checkpoints(self, *, limit: int | None = None) -> list[dict[str, object]]:
        self.ensure()
        query = """
            SELECT id, snapshot_id, created_at, source, session_id,
                   baseline_snapshot_id, gate_status, git_commit_oid, message, metadata_json
            FROM checkpoints
            ORDER BY id DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params = (int(limit),)
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(query, params).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            items.append(
                {
                    "checkpoint_id": int(row[0]),
                    "snapshot_id": int(row[1]),
                    "created_at": str(row[2]),
                    "source": str(row[3]),
                    "session_id": str(row[4]) if row[4] is not None else None,
                    "baseline_snapshot_id": int(row[5]) if row[5] is not None else None,
                    "gate_status": str(row[6]),
                    "git_commit_oid": str(row[7]) if row[7] is not None else None,
                    "message": str(row[8] or ""),
                    "metadata": json.loads(row[9]) if row[9] else {},
                }
            )
        return items

    def list_referenced_git_commit_oids(self) -> list[str]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT git_commit_oid
                FROM checkpoints
                WHERE git_commit_oid IS NOT NULL AND TRIM(git_commit_oid) <> ''
                """
            ).fetchall()
        oids: list[str] = []
        for row in rows:
            value = str(row[0]).strip()
            if value:
                oids.append(value)
        return oids

    def latest_snapshot_info(self) -> dict[str, int | str] | None:
        snapshot_id = self.latest_snapshot_id()
        if snapshot_id is None:
            return None
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, created_at FROM snapshots WHERE id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "snapshot_id": int(row[0]),
            "created_at": str(row[1]),
        }

    def load_snapshot(self, snapshot_id: int) -> dict[str, SnapshotFile]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT path, kind, mtime_ns, size, digest, content
                FROM snapshot_files
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchall()
        return {
            str(path): SnapshotFile(
                path=str(path),
                kind=str(kind),
                mtime_ns=int(mtime_ns),
                size=int(size),
                digest=str(digest),
                content=str(content) if content is not None else None,
            )
            for (path, kind, mtime_ns, size, digest, content) in rows
        }

    def create_session(
        self,
        session_id: str,
        baseline_id_before: int | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.ensure()
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions(
                        session_id, status, baseline_id_before, baseline_id_after,
                        created_at, updated_at, committed_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, "open", baseline_id_before, None, now, now, None, metadata_json),
                )

    def get_session(self, session_id: str) -> dict[str, object] | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT session_id, status, baseline_id_before, baseline_id_after,
                       created_at, updated_at, committed_at, metadata_json
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row[0]),
            "status": str(row[1]),
            "baseline_id_before": int(row[2]) if row[2] is not None else None,
            "baseline_id_after": int(row[3]) if row[3] is not None else None,
            "created_at": str(row[4]),
            "updated_at": str(row[5]),
            "committed_at": str(row[6]) if row[6] is not None else None,
            "metadata": json.loads(row[7]) if row[7] else {},
        }

    def latest_session(self, *, status: str | None = None) -> dict[str, object] | None:
        self.ensure()
        query = """
            SELECT session_id, status, baseline_id_before, baseline_id_after,
                   created_at, updated_at, committed_at, metadata_json
            FROM sessions
        """
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC LIMIT 1"
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row[0]),
            "status": str(row[1]),
            "baseline_id_before": int(row[2]) if row[2] is not None else None,
            "baseline_id_after": int(row[3]) if row[3] is not None else None,
            "created_at": str(row[4]),
            "updated_at": str(row[5]),
            "committed_at": str(row[6]) if row[6] is not None else None,
            "metadata": json.loads(row[7]) if row[7] else {},
        }

    def update_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        baseline_id_after: int | None = None,
        committed: bool = False,
    ) -> None:
        self.ensure()
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                current = conn.execute(
                    "SELECT status, baseline_id_after FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError(f"session not found: {session_id}")
                next_status = status if status is not None else str(current[0])
                next_after = baseline_id_after if baseline_id_after is not None else current[1]
                committed_at = now if committed else None
                conn.execute(
                    """
                    UPDATE sessions
                    SET status = ?, baseline_id_after = ?, updated_at = ?, committed_at = COALESCE(?, committed_at)
                    WHERE session_id = ?
                    """,
                    (next_status, next_after, now, committed_at, session_id),
                )

    def append_session_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.ensure()
        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO session_events(session_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, event_type, payload_json, now),
                )

    def get_session_events(self, session_id: str) -> list[dict[str, object]]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, payload_json, created_at
                FROM session_events
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            events.append(
                {
                    "id": int(row[0]),
                    "event_type": str(row[1]),
                    "payload": json.loads(row[2]) if row[2] else {},
                    "created_at": str(row[3]),
                }
            )
        return events

    def append_history_event(
        self,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.ensure()
        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO history_events(event_type, payload_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (event_type, payload_json, now),
                )

    def history_stats(self) -> dict[str, int]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS checkpoint_count,
                    COUNT(DISTINCT snapshot_id) AS referenced_snapshots
                FROM checkpoints
                """
            ).fetchone()
            size_row = conn.execute(
                """
                SELECT COALESCE(SUM(size), 0)
                FROM snapshot_files
                WHERE snapshot_id IN (SELECT snapshot_id FROM checkpoints)
                """
            ).fetchone()
        return {
            "checkpoint_count": int(row[0] if row and row[0] is not None else 0),
            "referenced_snapshots": int(row[1] if row and row[1] is not None else 0),
            "referenced_bytes": int(size_row[0] if size_row and size_row[0] is not None else 0),
        }

    def snapshot_sizes_by_id(self) -> dict[int, int]:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT snapshot_id, COALESCE(SUM(size), 0)
                FROM snapshot_files
                GROUP BY snapshot_id
                """
            ).fetchall()
        return {int(snapshot_id): int(total) for snapshot_id, total in rows}

    def delete_checkpoints_and_orphan_snapshots(self, checkpoint_ids: list[int]) -> list[int]:
        self.ensure()
        removed_snapshots: list[int] = []
        placeholders = ",".join("?" for _ in checkpoint_ids)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                rows = conn.execute(
                    f"SELECT snapshot_id FROM checkpoints WHERE id IN ({placeholders})",
                    tuple(checkpoint_ids),
                ).fetchall()
                snapshot_ids = {int(row[0]) for row in rows}
                conn.execute(
                    f"DELETE FROM checkpoints WHERE id IN ({placeholders})",
                    tuple(checkpoint_ids),
                )
                current_snapshot_id_row = conn.execute(
                    "SELECT current_snapshot_id FROM baseline_state WHERE id = 1"
                ).fetchone()
                current_snapshot_id = (
                    int(current_snapshot_id_row[0])
                    if current_snapshot_id_row and current_snapshot_id_row[0] is not None
                    else None
                )
                for snapshot_id in sorted(snapshot_ids):
                    if current_snapshot_id is not None and snapshot_id == current_snapshot_id:
                        continue
                    in_checkpoint = conn.execute(
                        "SELECT 1 FROM checkpoints WHERE snapshot_id = ? LIMIT 1",
                        (snapshot_id,),
                    ).fetchone()
                    in_session = conn.execute(
                        """
                        SELECT 1 FROM sessions
                        WHERE baseline_id_before = ? OR baseline_id_after = ?
                        LIMIT 1
                        """,
                        (snapshot_id, snapshot_id),
                    ).fetchone()
                    if in_checkpoint is not None or in_session is not None:
                        continue
                    conn.execute("DELETE FROM snapshot_files WHERE snapshot_id = ?", (snapshot_id,))
                    conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
                    removed_snapshots.append(snapshot_id)
        return removed_snapshots


def collect_workspace_files(
    project_root: Path,
    iwp_root: str,
    iwp_root_path: Path,
    code_roots: list[str],
    include_ext: list[str],
    code_exclude_globs: list[str] | None = None,
    exclude_markdown_globs: list[str] | None = None,
    max_file_size_bytes: int | None = None,
) -> list[SnapshotFile]:
    files: list[SnapshotFile] = []

    for rel in list_markdown_rel_paths(iwp_root_path, exclude_markdown_globs):
        abs_path = iwp_root_path / rel
        project_rel = f"{iwp_root}/{rel}"
        files.append(
            _to_snapshot_file(
                abs_path,
                project_rel,
                "markdown",
                max_file_size_bytes=max_file_size_bytes,
            )
        )

    for code_path in discover_code_files(
        project_root,
        code_roots,
        include_ext,
        code_exclude_globs,
    ):
        rel = code_path.relative_to(project_root).as_posix()
        files.append(
            _to_snapshot_file(
                code_path,
                rel,
                "code",
                max_file_size_bytes=max_file_size_bytes,
            )
        )

    dedup: dict[str, SnapshotFile] = {}
    for item in files:
        dedup[item.path] = item
    return sorted(dedup.values(), key=lambda item: item.path)


def _to_snapshot_file(
    abs_path: Path,
    rel_path: str,
    kind: str,
    *,
    max_file_size_bytes: int | None = None,
) -> SnapshotFile:
    stat = abs_path.stat()
    if max_file_size_bytes is not None and int(stat.st_size) > int(max_file_size_bytes):
        raise RuntimeError(
            "snapshot file exceeds configured max size: "
            f"{rel_path} ({int(stat.st_size)} bytes > {int(max_file_size_bytes)} bytes). "
            "Adjust `tracking.snapshot.max_file_size_kb` or refine `tracking.snapshot.exclude_globs`."
        )
    content = abs_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return SnapshotFile(
        path=rel_path,
        kind=kind,
        mtime_ns=int(stat.st_mtime_ns),
        size=int(stat.st_size),
        digest=digest,
        content=content,
    )
