from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SectionSpec:
    key: str
    required: bool

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SectionSpec:
        return cls(
            key=str(raw["key"]),
            required=bool(raw.get("required", False)),
        )


@dataclass(frozen=True)
class FileTypeSchema:
    id: str
    path_patterns: list[str]
    sections: list[SectionSpec]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FileTypeSchema:
        return cls(
            id=str(raw["id"]),
            path_patterns=[str(item) for item in raw.get("path_patterns", [])],
            sections=[SectionSpec.from_dict(item) for item in raw.get("sections", [])],
        )


@dataclass(frozen=True)
class SchemaProfile:
    schema_name: str
    schema_version: str
    mode_default: str
    supported_modes: list[str]
    h1_required_exactly_one: bool
    h2_unknown_policy: dict[str, str]
    kind_rule_format: str
    section_i18n: dict[str, dict[str, list[str]]]
    file_type_schemas: list[FileTypeSchema]
