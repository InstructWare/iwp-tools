from __future__ import annotations

from pathlib import PurePosixPath

from .schema_models import FileTypeSchema


def match_file_type(rel_path: str, schemas: list[FileTypeSchema]) -> FileTypeSchema | None:
    p = PurePosixPath(rel_path)
    for schema in schemas:
        for pattern in schema.path_patterns:
            if p.match(pattern):
                return schema
    return None


def resolve_section_keys(
    actual_title: str,
    section_i18n: dict[str, dict[str, list[str]]],
) -> list[str]:
    matched_by_key: dict[str, int] = {}
    for section_key, locales in section_i18n.items():
        titles = [item for names in locales.values() for item in names]
        matched_lengths = [
            len(expected_title)
            for expected_title in titles
            if _title_match(expected_title, actual_title)
        ]
        if matched_lengths:
            matched_by_key[section_key] = max(matched_lengths)
    if not matched_by_key:
        return []
    best_len = max(matched_by_key.values())
    return [
        section_key for section_key, title_len in matched_by_key.items() if title_len == best_len
    ]


def _title_match(expected_title: str, actual_title: str) -> bool:
    actual = actual_title.strip()
    return (
        actual == expected_title
        or actual.startswith(f"{expected_title}:")
        or actual.startswith(f"{expected_title} ")
    )
