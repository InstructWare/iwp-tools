from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
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


def collect_workspace_files(
    project_root: Path,
    iwp_root: str,
    iwp_root_path: Path,
    code_roots: list[str],
    include_ext: list[str],
    exclude_markdown_globs: list[str] | None = None,
) -> list[SnapshotFile]:
    files: list[SnapshotFile] = []

    for rel in list_markdown_rel_paths(iwp_root_path, exclude_markdown_globs):
        abs_path = iwp_root_path / rel
        project_rel = f"{iwp_root}/{rel}"
        files.append(_to_snapshot_file(abs_path, project_rel, "markdown"))

    for code_path in discover_code_files(project_root, code_roots, include_ext):
        rel = code_path.relative_to(project_root).as_posix()
        files.append(_to_snapshot_file(code_path, rel, "code"))

    dedup: dict[str, SnapshotFile] = {}
    for item in files:
        dedup[item.path] = item
    return sorted(dedup.values(), key=lambda item: item.path)


def compute_changed_lines(old_text: str, new_text: str) -> set[int]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines)
    changed: set[int] = set()

    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            anchor = min(max(j1 + 1, 1), max(len(new_lines), 1))
            changed.add(anchor)
            continue
        start = j1 + 1
        end = max(j2, start)
        for line_no in range(start, end + 1):
            changed.add(line_no)
    return changed


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
        content=content if kind == "markdown" else None,
    )
