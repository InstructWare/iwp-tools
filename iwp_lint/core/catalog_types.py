from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class NodeCatalogEntry:
    source_path: str
    node_id: str
    anchor_text: str
    title_path: str
    section_key: str
    file_type_id: str
    computed_kind: str
    anchor_level: str
    line_start: int
    line_end: int
    is_critical: bool
    source_line_start: int
    source_line_end: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NodeCatalogEntry:
        line_start = int(raw["line_start"])
        line_end = int(raw["line_end"])
        return cls(
            source_path=str(raw["source_path"]),
            node_id=str(raw["node_id"]),
            anchor_text=str(raw["anchor_text"]),
            title_path=str(raw["title_path"]),
            section_key=str(raw["section_key"]),
            file_type_id=str(raw["file_type_id"]),
            computed_kind=str(raw["computed_kind"]),
            anchor_level=str(raw.get("anchor_level", "default")),
            line_start=line_start,
            line_end=line_end,
            is_critical=bool(raw.get("is_critical", False)),
            source_line_start=int(raw.get("source_line_start", line_start)),
            source_line_end=int(raw.get("source_line_end", line_end)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
