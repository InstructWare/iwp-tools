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

    def create_snapshot(self, files: list[SnapshotFile]) -> int:
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
            return snapshot_id

    def latest_snapshot_id(self) -> int | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def latest_snapshot_info(self) -> dict[str, int | str] | None:
        self.ensure()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, created_at FROM snapshots ORDER BY id DESC LIMIT 1"
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


def collect_workspace_files(
    project_root: Path,
    iwp_root: str,
    iwp_root_path: Path,
    code_roots: list[str],
    include_ext: list[str],
    code_exclude_globs: list[str] | None = None,
    exclude_markdown_globs: list[str] | None = None,
) -> list[SnapshotFile]:
    files: list[SnapshotFile] = []

    for rel in list_markdown_rel_paths(iwp_root_path, exclude_markdown_globs):
        abs_path = iwp_root_path / rel
        project_rel = f"{iwp_root}/{rel}"
        files.append(_to_snapshot_file(abs_path, project_rel, "markdown"))

    for code_path in discover_code_files(
        project_root,
        code_roots,
        include_ext,
        code_exclude_globs,
    ):
        rel = code_path.relative_to(project_root).as_posix()
        files.append(_to_snapshot_file(code_path, rel, "code"))

    dedup: dict[str, SnapshotFile] = {}
    for item in files:
        dedup[item.path] = item
    return sorted(dedup.values(), key=lambda item: item.path)


def _to_snapshot_file(abs_path: Path, rel_path: str, kind: str) -> SnapshotFile:
    stat = abs_path.stat()
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
