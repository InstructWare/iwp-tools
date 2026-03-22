from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core.errors import Diagnostic
from ..parsers.markdown_outline import extract_outline
from .schema_loader import load_schema_profile
from .schema_models import FileTypeSchema, SchemaProfile
from .schema_semantics import match_file_type, resolve_section_keys

LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
TEXT_MARKER_RE = re.compile(r"^\[text\]\s*:?\s*(.*)$")


@dataclass(frozen=True)
class SchemaValidationResult:
    diagnostics: list[Diagnostic]
    checked_files: int
    matched_files: int


def validate_markdown_schema(
    iwp_root: Path,
    schema_path: Path | str,
    mode: str,
    target_rel_paths: set[str] | None = None,
    exclude_markdown_globs: list[str] | None = None,
) -> SchemaValidationResult:
    profile = load_schema_profile(schema_path)
    mode = _resolve_mode(mode, profile)

    diagnostics: list[Diagnostic] = []
    checked_files = 0
    matched_files = 0

    all_md_rel_paths = list_markdown_rel_paths(iwp_root, exclude_markdown_globs)
    if target_rel_paths is not None:
        all_md_rel_paths = [p for p in all_md_rel_paths if p in target_rel_paths]

    for rel in all_md_rel_paths:
        checked_files += 1
        matched_schema = match_file_type(rel, profile.file_type_schemas)
        if not matched_schema:
            diagnostics.append(
                Diagnostic(
                    code="IWP202",
                    message=f"No schema file type matched for markdown path: {rel}",
                    file_path=rel,
                    severity=_unknown_severity(profile, mode),
                )
            )
            continue
        matched_files += 1
        diagnostics.extend(_validate_one_file(iwp_root / rel, rel, matched_schema, profile, mode))

    return SchemaValidationResult(
        diagnostics=diagnostics,
        checked_files=checked_files,
        matched_files=matched_files,
    )


def list_markdown_rel_paths(
    iwp_root: Path, exclude_markdown_globs: list[str] | None = None
) -> list[str]:
    rel_paths = sorted(path.relative_to(iwp_root).as_posix() for path in iwp_root.rglob("*.md"))
    if not exclude_markdown_globs:
        return rel_paths
    return [p for p in rel_paths if not _is_excluded_path(p, exclude_markdown_globs)]


def _validate_one_file(
    file_path: Path,
    rel_path: str,
    file_type_schema: FileTypeSchema,
    profile: SchemaProfile,
    mode: str,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    outline = extract_outline(file_path)

    if profile.h1_required_exactly_one and outline.h1_count != 1:
        diagnostics.append(
            Diagnostic(
                code="IWP204",
                message=f"Expected exactly 1 H1, found {outline.h1_count}",
                file_path=rel_path,
                line=1,
            )
        )

    allowed_keys = {item.key for item in file_type_schema.sections}
    allow_unknown_sections = file_type_schema.allow_unknown_sections
    required_keys = {item.key for item in file_type_schema.sections if item.required}
    declared_i18n_keys = set(profile.section_i18n.keys())
    for section_key in sorted(allowed_keys):
        if section_key not in declared_i18n_keys:
            diagnostics.append(
                Diagnostic(
                    code="IWP204",
                    message=f"Missing section_i18n entry for section key: {section_key}",
                    file_path=rel_path,
                )
            )

    found_keys: set[str] = set()
    for section in outline.h2_sections:
        resolved = resolve_section_keys(section.title, profile.section_i18n)
        if len(resolved) > 1:
            diagnostics.append(
                Diagnostic(
                    code="IWP204",
                    message=(
                        "Ambiguous section title mapping: "
                        f"{section.title} -> {', '.join(sorted(resolved))}"
                    ),
                    file_path=rel_path,
                    line=section.line,
                )
            )
            continue
        if not resolved:
            if not allow_unknown_sections:
                allowed_section_hint = _allowed_section_hint(file_type_schema, profile)
                diagnostics.append(
                    Diagnostic(
                        code="IWP202",
                        message=(
                            f'Unknown section "{section.title}" for file type {file_type_schema.id}. '
                            f"{allowed_section_hint}"
                        ),
                        file_path=rel_path,
                        line=section.line,
                        severity=_unknown_severity(profile, mode),
                    )
                )
            continue

        section_key = resolved[0]
        if section_key not in allowed_keys:
            if not allow_unknown_sections:
                allowed_section_hint = _allowed_section_hint(file_type_schema, profile)
                diagnostics.append(
                    Diagnostic(
                        code="IWP202",
                        message=(
                            f'Illegal section "{section.title}" ({section_key}) for file type '
                            f"{file_type_schema.id}. {allowed_section_hint}"
                        ),
                        file_path=rel_path,
                        line=section.line,
                        severity=_unknown_severity(profile, mode),
                    )
                )
            continue
        found_keys.add(section_key)

    for required_key in sorted(required_keys):
        if required_key not in found_keys:
            diagnostics.append(
                Diagnostic(
                    code="IWP201",
                    message=f"Missing required section key: {required_key}",
                    file_path=rel_path,
                )
            )

    diagnostics.extend(_validate_text_marker_usage(file_path, rel_path, profile))
    return diagnostics


def _resolve_mode(requested_mode: str, profile: SchemaProfile) -> str:
    if requested_mode in profile.supported_modes:
        return requested_mode
    return profile.mode_default


def _unknown_severity(profile: SchemaProfile, mode: str) -> str:
    policy = profile.h2_unknown_policy.get(mode, "warn")
    return "error" if policy == "error" else "warning"


def _is_excluded_path(rel_path: str, patterns: list[str]) -> bool:
    posix = PurePosixPath(rel_path)
    for pattern in patterns:
        if posix.match(pattern) or fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _validate_text_marker_usage(
    file_path: Path, rel_path: str, profile: SchemaProfile
) -> list[Diagnostic]:
    if not profile.text_marker_enabled:
        return []
    token = profile.text_marker_token.strip()
    if token != "[text]":
        return []

    diagnostics: list[Diagnostic] = []
    allowed_sections = set(profile.text_marker_allowed_sections)
    lines = file_path.read_text(encoding="utf-8").splitlines()
    current_section_key = "document"
    for line_no, line in enumerate(lines, start=1):
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            resolved = resolve_section_keys(heading_match.group(1).strip(), profile.section_i18n)
            current_section_key = resolved[0] if len(resolved) == 1 else "unknown_section"
            continue
        list_match = LIST_ITEM_RE.match(line)
        if not list_match:
            continue
        item_text = list_match.group(1).strip()
        if not item_text.startswith("["):
            continue
        if not TEXT_MARKER_RE.match(item_text):
            diagnostics.append(
                Diagnostic(
                    code="IWP204",
                    message="Invalid marker syntax. Only `[text]` is supported.",
                    file_path=rel_path,
                    line=line_no,
                )
            )
            continue
        if allowed_sections and current_section_key not in allowed_sections:
            diagnostics.append(
                Diagnostic(
                    code="IWP204",
                    message=(
                        "`[text]` marker is not allowed in this section: "
                        f"{current_section_key}"
                    ),
                    file_path=rel_path,
                    line=line_no,
                )
            )
    return diagnostics


def _allowed_section_hint(file_type_schema: FileTypeSchema, profile: SchemaProfile) -> str:
    labels = [_section_label(item.key, profile) for item in file_type_schema.sections]
    if not labels:
        return "Allowed: (none)"
    return f"Allowed: {', '.join(labels)}"


def _section_label(section_key: str, profile: SchemaProfile) -> str:
    locales = profile.section_i18n.get(section_key, {})
    for locale in sorted(locales.keys()):
        titles = locales.get(locale) or []
        if titles:
            return str(titles[0])
    return section_key
