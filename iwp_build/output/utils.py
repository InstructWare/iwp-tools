from __future__ import annotations

import json


def safe_len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def write_json(path: str | None, payload: dict[str, object]) -> str | None:
    if not path:
        return None
    from pathlib import Path

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path.as_posix()
