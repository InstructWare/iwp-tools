from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_STATES = {"pending", "running", "done", "failed"}


@dataclass(frozen=True)
class DiffTask:
    task_id: str
    status: str
    created_at: str
    updated_at: str
    changed_files: list[str]
    changed_md_files: list[str]
    changed_code_files: list[str]
    impacted_nodes: list[dict[str, Any]]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DiffTask:
        return cls(
            task_id=str(payload["task_id"]),
            status=str(payload["status"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            changed_files=[str(x) for x in payload.get("changed_files", [])],
            changed_md_files=[str(x) for x in payload.get("changed_md_files", [])],
            changed_code_files=[str(x) for x in payload.get("changed_code_files", [])],
            impacted_nodes=[
                item for item in payload.get("impacted_nodes", []) if isinstance(item, dict)
            ],
            notes=str(payload.get("notes", "")),
        )


def create_diff_task(
    task_dir: Path,
    changed_files: set[str],
    changed_md_files: set[str],
    changed_code_files: set[str],
    impacted_nodes: list[dict[str, Any]],
    notes: str = "",
) -> DiffTask:
    task_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    task_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    task = DiffTask(
        task_id=task_id,
        status="pending",
        created_at=now,
        updated_at=now,
        changed_files=sorted(changed_files),
        changed_md_files=sorted(changed_md_files),
        changed_code_files=sorted(changed_code_files),
        impacted_nodes=impacted_nodes,
        notes=notes,
    )
    _write_task(task_dir, task)
    return task


def list_tasks(task_dir: Path) -> list[DiffTask]:
    if not task_dir.exists():
        return []
    tasks: list[DiffTask] = []
    for path in sorted(task_dir.glob("diff-task-*.json"), reverse=True):
        raw = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(DiffTask.from_dict(raw))
    return tasks


def load_task(task_dir: Path, task_id: str) -> DiffTask:
    path = _task_path(task_dir, task_id)
    if not path.exists():
        raise RuntimeError(f"task not found: {task_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return DiffTask.from_dict(raw)


def update_task_status(task_dir: Path, task_id: str, status: str, notes: str = "") -> DiffTask:
    if status not in TASK_STATES:
        raise RuntimeError(f"invalid task status: {status}")
    task = load_task(task_dir, task_id)
    now = datetime.now(timezone.utc).isoformat()
    updated = DiffTask(
        task_id=task.task_id,
        status=status,
        created_at=task.created_at,
        updated_at=now,
        changed_files=task.changed_files,
        changed_md_files=task.changed_md_files,
        changed_code_files=task.changed_code_files,
        impacted_nodes=task.impacted_nodes,
        notes=notes or task.notes,
    )
    _write_task(task_dir, updated)
    return updated


def _write_task(task_dir: Path, task: DiffTask) -> None:
    path = _task_path(task_dir, task.task_id)
    path.write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _task_path(task_dir: Path, task_id: str) -> Path:
    return task_dir / f"diff-task-{task_id}.json"
