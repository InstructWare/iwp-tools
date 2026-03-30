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
    arg_keys: tuple[str, ...] = ()
    invalid_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class IwpControlParamIssue:
    code: str
    message: str


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
    parsed_args, invalid_args = _parse_iwp_args(args_raw)
    return body, ParsedIwpControl(
        mode="iwp",
        file_type_id=parsed_args.get("file"),
        section_key=parsed_args.get("section"),
        kind=parsed_args.get("kind"),
        arg_keys=tuple(sorted(parsed_args.keys())),
        invalid_args=invalid_args,
    )


def _parse_iwp_args(raw: str) -> tuple[dict[str, str], tuple[str, ...]]:
    out: dict[str, str] = {}
    invalid_items: list[str] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        if "=" not in item:
            invalid_items.append(item)
            continue
        key, value = item.split("=", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            invalid_items.append(item)
            continue
        out[normalized_key] = normalized_value
    return out, tuple(invalid_items)


def validate_iwp_control_params(
    control: ParsedIwpControl | None,
    profile: SchemaProfile,
    *,
    strict_annotation_params: bool,
) -> list[IwpControlParamIssue]:
    if not strict_annotation_params or control is None or control.mode != "iwp":
        return []
    issues: list[IwpControlParamIssue] = []
    if control.invalid_args:
        issues.append(
            IwpControlParamIssue(
                code="IWP301",
                message=f"Invalid annotation parameter syntax: {', '.join(control.invalid_args)}",
            )
        )
    allowed_keys = {"kind", "file", "section"}
    unknown_keys = [key for key in control.arg_keys if key not in allowed_keys]
    if unknown_keys:
        issues.append(
            IwpControlParamIssue(
                code="IWP301",
                message=f"Unsupported annotation parameter key(s): {', '.join(unknown_keys)}",
            )
        )
    has_file = bool(control.file_type_id)
    has_section = bool(control.section_key)
    if has_file != has_section:
        issues.append(
            IwpControlParamIssue(
                code="IWP302",
                message="`file` and `section` must be provided together.",
            )
        )
    kind_pair: tuple[str, str] | None = None
    if control.kind:
        if "." not in control.kind:
            issues.append(
                IwpControlParamIssue(
                    code="IWP304",
                    message="Invalid `kind` format. Expected `<file_type_id>.<section_key>`.",
                )
            )
        else:
            file_type_id, section_key = control.kind.rsplit(".", 1)
            normalized_file_type_id = file_type_id.strip()
            normalized_section_key = section_key.strip()
            if not normalized_file_type_id or not normalized_section_key:
                issues.append(
                    IwpControlParamIssue(
                        code="IWP304",
                        message="Invalid `kind` format. Expected non-empty file/section.",
                    )
                )
            else:
                kind_pair = (normalized_file_type_id, normalized_section_key)
                if not _is_valid_file_section_pair(
                    profile,
                    file_type_id=normalized_file_type_id,
                    section_key=normalized_section_key,
                ):
                    issues.append(
                        IwpControlParamIssue(
                            code="IWP301",
                            message=f"Unknown `kind` value: {control.kind}",
                        )
                    )
    file_section_pair: tuple[str, str] | None = None
    if has_file and has_section:
        file_section_pair = (str(control.file_type_id), str(control.section_key))
        if not _is_valid_file_section_pair(
            profile,
            file_type_id=file_section_pair[0],
            section_key=file_section_pair[1],
        ):
            issues.append(
                IwpControlParamIssue(
                    code="IWP302",
                    message=(
                        "Invalid `file`/`section` pair: "
                        f"file={file_section_pair[0]}, section={file_section_pair[1]}"
                    ),
                )
            )
    if kind_pair is not None and file_section_pair is not None and kind_pair != file_section_pair:
        issues.append(
            IwpControlParamIssue(
                code="IWP303",
                message=(
                    "Conflicting annotation parameters: "
                    f"kind={control.kind} vs file={file_section_pair[0]},section={file_section_pair[1]}"
                ),
            )
        )
    return _dedupe_issues(issues)


def _is_valid_file_section_pair(
    profile: SchemaProfile,
    *,
    file_type_id: str,
    section_key: str,
) -> bool:
    for schema in profile.file_type_schemas:
        if schema.id != file_type_id:
            continue
        return section_key in {item.key for item in schema.sections}
    return False


def _dedupe_issues(issues: list[IwpControlParamIssue]) -> list[IwpControlParamIssue]:
    deduped: list[IwpControlParamIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


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
        file_type_id, section_key = control.kind.rsplit(".", 1)
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
