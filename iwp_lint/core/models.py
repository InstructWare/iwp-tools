from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class MarkdownNode:
    node_id: str
    source_path: str
    line_start: int
    line_end: int
    title_path: str
    anchor_text: str
    section_key: str
    file_type_id: str
    computed_kind: str
    anchor_level: str = "default"
    is_critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LinkAnnotation:
    source_path: str
    node_id: str
    file_path: str
    line: int
    column: int
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoverageMetrics:
    total_nodes: int
    linked_nodes: int
    critical_nodes: int
    linked_critical_nodes: int
    tested_nodes: int
    node_linked_percent: float
    critical_linked_percent: float
    node_tested_percent: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
