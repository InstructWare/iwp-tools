from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from .schema_models import AuthoringAliasSpec, FileTypeSchema, SchemaProfile


@dataclass(frozen=True)
class SemanticContext:
    section_i18n: dict[str, dict[str, list[str]]]
    page_only_enabled: bool
    authoring_tokens_enabled: bool
    aliases: list[AuthoringAliasSpec]
    aliases_by_title: dict[tuple[str, str], AuthoringAliasSpec]
    aliases_by_section: dict[tuple[str, str], AuthoringAliasSpec]
    alias_labels_by_source_file_type: dict[str, list[str]]


@dataclass(frozen=True)
class ParsedIwpControl:
    mode: str
    file_type_id: str | None = None
    section_key: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class SemanticResolution:
    file_type_id: str
    section_key: str
    trace_required: bool
    trace_source: str


class SemanticResolver(Protocol):
    def resolve_heading(
        self,
        *,
        title: str,
        file_type_id: str,
        allowed_section_keys: set[str],
        allow_unknown_sections: bool,
        control: ParsedIwpControl | None,
    ) -> SemanticResolution: ...

    def resolve_list_item(
        self,
        *,
        file_type_id: str,
        current_section_key: str,
        control: ParsedIwpControl | None,
        inherited_trace_required: bool,
    ) -> SemanticResolution: ...


CONTROL_TOKEN_RE = re.compile(
    r"^(?P<body>.*?)(?:\s+)(?P<token>@no-iwp|@iwp(?:\((?P<args>[^)]*)\))?)\s*$"
)
CONTROL_TOKEN_ONLY_RE = re.compile(r"^(?P<token>@no-iwp|@iwp(?:\((?P<args>[^)]*)\))?)\s*$")


class DefaultSemanticResolver:
    def __init__(self, semantic_context: SemanticContext) -> None:
        self.semantic_context = semantic_context

    def resolve_heading(
        self,
        *,
        title: str,
        file_type_id: str,
        allowed_section_keys: set[str],
        allow_unknown_sections: bool,
        control: ParsedIwpControl | None,
    ) -> SemanticResolution:
        explicit = _resolve_control_semantic_context(
            control=control,
            fallback_file_type_id=file_type_id,
            fallback_section_key="document",
        )
        if explicit is not None:
            resolved_file_type_id, resolved_section_key = explicit
        else:
            page_only_section = resolve_page_only_h2(
                title=title,
                file_type_id=file_type_id,
                semantic_context=self.semantic_context,
            )
            if page_only_section is not None:
                resolved_file_type_id, resolved_section_key = page_only_section
            else:
                resolved = resolve_section_keys(title, self.semantic_context)
                if len(resolved) == 1 and (
                    not allowed_section_keys or resolved[0] in allowed_section_keys
                ):
                    resolved_section_key = resolved[0]
                elif allow_unknown_sections:
                    resolved_section_key = _slugify(title)
                else:
                    resolved_section_key = "unknown_section"
                resolved_file_type_id, resolved_section_key = resolve_section_semantic_context(
                    file_type_id=file_type_id,
                    section_key=resolved_section_key,
                    semantic_context=self.semantic_context,
                )
        return SemanticResolution(
            file_type_id=resolved_file_type_id,
            section_key=resolved_section_key,
            trace_required=_trace_required_from_control(control=control, inherited=False),
            trace_source=_trace_source_from_control(control=control, inherited=False),
        )

    def resolve_list_item(
        self,
        *,
        file_type_id: str,
        current_section_key: str,
        control: ParsedIwpControl | None,
        inherited_trace_required: bool,
    ) -> SemanticResolution:
        explicit = _resolve_control_semantic_context(
            control=control,
            fallback_file_type_id=file_type_id,
            fallback_section_key=current_section_key,
        )
        if explicit is not None:
            resolved_file_type_id, resolved_section_key = explicit
        else:
            resolved_file_type_id, resolved_section_key = resolve_section_semantic_context(
                file_type_id=file_type_id,
                section_key=current_section_key,
                semantic_context=self.semantic_context,
            )
        return SemanticResolution(
            file_type_id=resolved_file_type_id,
            section_key=resolved_section_key,
            trace_required=_trace_required_from_control(
                control=control, inherited=inherited_trace_required
            ),
            trace_source=_trace_source_from_control(
                control=control, inherited=inherited_trace_required
            ),
        )


def build_semantic_context(
    profile: SchemaProfile, *, page_only_enabled: bool, authoring_tokens_enabled: bool = True
) -> SemanticContext:
    aliases_by_title: dict[tuple[str, str], AuthoringAliasSpec] = {}
    aliases_by_section: dict[tuple[str, str], AuthoringAliasSpec] = {}
    alias_labels_by_source_file_type: dict[str, list[str]] = {}
    for alias in profile.authoring_aliases:
        key = (alias.source_file_type_id, alias.source_section_key)
        aliases_by_section[key] = alias
        labels = alias_labels_by_source_file_type.setdefault(alias.source_file_type_id, [])
        for label in alias.labels:
            if label not in labels:
                labels.append(label)
        for title in alias.title_aliases:
            aliases_by_title[(alias.source_file_type_id, _normalize_title(title))] = alias
    return SemanticContext(
        section_i18n=profile.section_i18n,
        page_only_enabled=page_only_enabled,
        authoring_tokens_enabled=authoring_tokens_enabled,
        aliases=profile.authoring_aliases,
        aliases_by_title=aliases_by_title,
        aliases_by_section=aliases_by_section,
        alias_labels_by_source_file_type=alias_labels_by_source_file_type,
    )


def build_semantic_resolver(semantic_context: SemanticContext) -> SemanticResolver:
    return DefaultSemanticResolver(semantic_context)


def match_file_type(rel_path: str, schemas: list[FileTypeSchema]) -> FileTypeSchema | None:
    p = PurePosixPath(rel_path)
    for schema in schemas:
        for pattern in schema.path_patterns:
            if p.match(pattern):
                return schema
    return None


def resolve_section_keys(
    actual_title: str,
    semantic_context: SemanticContext | dict[str, dict[str, list[str]]],
) -> list[str]:
    section_i18n = (
        semantic_context.section_i18n
        if isinstance(semantic_context, SemanticContext)
        else semantic_context
    )
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


def resolve_page_only_h2(
    *,
    title: str,
    file_type_id: str,
    semantic_context: SemanticContext,
) -> tuple[str, str] | None:
    if not semantic_context.page_only_enabled:
        return None
    alias = semantic_context.aliases_by_title.get((file_type_id, _normalize_title(title)))
    if alias is None:
        return None
    return alias.target_file_type_id, alias.target_section_key


def resolve_section_semantic_context(
    *,
    file_type_id: str,
    section_key: str,
    semantic_context: SemanticContext,
) -> tuple[str, str]:
    if not semantic_context.page_only_enabled:
        return file_type_id, section_key
    alias = semantic_context.aliases_by_section.get((file_type_id, section_key))
    if alias is not None:
        return alias.target_file_type_id, alias.target_section_key
    return file_type_id, section_key


def allowed_alias_labels(file_type_id: str, semantic_context: SemanticContext) -> list[str]:
    return semantic_context.alias_labels_by_source_file_type.get(file_type_id, [])


def _normalize_title(title: str) -> str:
    normalized = title.strip().casefold()
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("_", "")
    return normalized


def parse_iwp_control_token(raw_text: str, *, enabled: bool) -> tuple[str, ParsedIwpControl | None]:
    text = raw_text.strip()
    if not enabled:
        return text, None
    match = CONTROL_TOKEN_RE.match(text)
    if not match:
        token_only_match = CONTROL_TOKEN_ONLY_RE.match(text)
        if token_only_match:
            match = token_only_match
            body = ""
        else:
            return text, None
    else:
        body = match.group("body").rstrip()
    token = str(match.group("token"))
    if token == "@no-iwp":
        return body, ParsedIwpControl(mode="no_iwp")
    args_raw = match.group("args") or ""
    parsed_args = _parse_iwp_args(args_raw)
    return body, ParsedIwpControl(
        mode="iwp",
        file_type_id=parsed_args.get("file"),
        section_key=parsed_args.get("section"),
        kind=parsed_args.get("kind"),
    )


def _parse_iwp_args(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        out[normalized_key] = normalized_value
    return out


def _resolve_control_semantic_context(
    *,
    control: ParsedIwpControl | None,
    fallback_file_type_id: str,
    fallback_section_key: str,
) -> tuple[str, str] | None:
    if control is None or control.mode != "iwp":
        return None
    if control.kind:
        if "." not in control.kind:
            return (fallback_file_type_id, control.kind)
        file_type_id, section_key = control.kind.split(".", 1)
        if file_type_id.strip() and section_key.strip():
            return (file_type_id.strip(), section_key.strip())
    file_type_id = control.file_type_id or fallback_file_type_id
    section_key = control.section_key or fallback_section_key
    if not section_key:
        return None
    return (file_type_id, section_key)


def _trace_required_from_control(*, control: ParsedIwpControl | None, inherited: bool) -> bool:
    if control is None:
        return inherited
    if control.mode == "iwp":
        return True
    if control.mode == "no_iwp":
        return False
    return inherited


def _trace_source_from_control(*, control: ParsedIwpControl | None, inherited: bool) -> str:
    if control is None:
        return "section_token" if inherited else "default_policy"
    if control.mode == "iwp":
        return "item_token"
    if control.mode == "no_iwp":
        return "item_token"
    return "section_token" if inherited else "default_policy"


def _slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"`+", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "node"
