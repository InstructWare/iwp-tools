from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from .schema_models import FileTypeSchema, SchemaProfile


def load_schema_profile(schema_source: Path | str) -> SchemaProfile:
    raw = json.loads(_read_schema_text(schema_source))
    file_types = [FileTypeSchema.from_dict(item) for item in raw.get("file_type_schemas", [])]
    modes = raw.get("modes", {})
    global_rules = raw.get("global_rules", {})
    kind_rules = raw.get("kind_rules", {})
    section_i18n_raw = raw.get("section_i18n", {})
    section_i18n = {
        str(key): {
            str(locale): [str(title) for title in titles] for locale, titles in locales.items()
        }
        for key, locales in section_i18n_raw.items()
    }
    return SchemaProfile(
        schema_name=str(raw.get("schema_name", "IWP Semantic Schema")),
        schema_version=str(raw.get("schema_version", "1.0.0")),
        mode_default=str(modes.get("default", "compat")),
        supported_modes=[str(item) for item in modes.get("supported", ["compat", "strict"])],
        h1_required_exactly_one=bool(global_rules.get("h1_required_exactly_one", True)),
        h2_unknown_policy={
            "compat": str(global_rules.get("h2_unknown_policy", {}).get("compat", "warn")),
            "strict": str(global_rules.get("h2_unknown_policy", {}).get("strict", "error")),
        },
        kind_rule_format=str(kind_rules.get("format", "{file_type_id}.{section_key}")),
        section_i18n=section_i18n,
        file_type_schemas=file_types,
    )


def _read_schema_text(schema_source: Path | str) -> str:
    if isinstance(schema_source, Path):
        return schema_source.read_text(encoding="utf-8")

    source = schema_source.strip()
    normalized = source.lower()
    if normalized == "builtin":
        return _read_builtin_schema_text("iwp-schema.v1")
    if normalized.startswith("builtin:"):
        alias = source.split(":", 1)[1].strip()
        return _read_builtin_schema_text(alias or "iwp-schema.v1")
    return Path(source).read_text(encoding="utf-8")


def _read_builtin_schema_text(alias: str) -> str:
    normalized = alias.lower()
    if normalized in {"iwp-schema.v1", "iwp-schema.v1.json", "default", "official"}:
        file_name = "iwp-schema.v1.json"
    else:
        raise RuntimeError(
            f"Unsupported builtin schema alias: {alias}. "
            "Use builtin:iwp-schema.v1 or a filesystem path."
        )
    resource = files("iwp_lint._bundled_schema").joinpath(file_name)
    return resource.read_text(encoding="utf-8")
