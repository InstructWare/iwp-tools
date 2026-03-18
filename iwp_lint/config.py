from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SCHEMA_SOURCE = "builtin:iwp-schema.v1"


@dataclass
class LintThresholds:
    node_linked_min: float = 0.0
    critical_linked_min: float = 0.0
    node_tested_min: float = 0.0


@dataclass
class LintConfig:
    project_root: Path
    iwp_root: str = "InstructWare.iw"
    code_roots: list[str] = field(default_factory=lambda: ["."])
    include_ext: list[str] = field(
        default_factory=lambda: [".vue", ".py", ".ts", ".tsx", ".js", ".jsx"]
    )
    test_globs: list[str] = field(
        default_factory=lambda: ["**/tests/**", "**/*.spec.*", "**/*.test.*"]
    )
    allow_multi_link_per_symbol: bool = False
    critical_node_patterns: list[str] = field(
        default_factory=lambda: ["interaction hooks", "trigger", "execution flow"]
    )
    thresholds: LintThresholds = field(default_factory=LintThresholds)
    diff_base: str = ""
    diff_head: str = ""
    diff_strict: bool = True
    diff_provider: str = "filesystem_snapshot"
    schema_file: str = DEFAULT_SCHEMA_SOURCE
    schema_mode: str = "compat"
    schema_exclude_markdown_globs: list[str] = field(default_factory=lambda: ["README.md"])
    node_registry_file: str = ".iwp/node_registry.v1.json"
    node_catalog_file: str = ".iwp/node_catalog.v1.json"
    cache_dir: str = ".iwp/cache"
    snapshot_db_file: str = ".iwp/cache/snapshots.sqlite"
    node_index_db_file: str = ".iwp/cache/node_index.v1.sqlite"
    task_dir: str = ".iwp/tasks"
    compiled_dir: str = ".iwp/compiled"

    @property
    def iwp_root_path(self) -> Path:
        return (self.project_root / self.iwp_root).resolve()


def _load_yaml_or_json(config_path: Path) -> dict[str, Any]:
    if config_path.suffix.lower() == ".json":
        import json

        return json.loads(config_path.read_text(encoding="utf-8"))

    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise RuntimeError("YAML config requires PyYAML. Install with: pip install pyyaml") from exc

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return loaded or {}


def load_config(config_file: str | None, cwd: Path | None = None) -> LintConfig:
    process_cwd = (cwd or Path.cwd()).resolve()
    if not config_file:
        return LintConfig(project_root=process_cwd)

    config_path = Path(config_file).resolve()
    raw = _load_yaml_or_json(config_path)
    thresholds_raw = raw.get("thresholds", {})
    config_dir = config_path.parent
    root_raw = raw.get("project_root")
    if root_raw is None:
        project_root = config_dir
    else:
        configured_root = Path(str(root_raw))
        project_root = (
            (config_dir / configured_root).resolve()
            if not configured_root.is_absolute()
            else configured_root.resolve()
        )

    return LintConfig(
        project_root=project_root,
        iwp_root=raw.get("iwp_root", "InstructWare.iw"),
        code_roots=list(raw.get("code_roots", ["."])),
        include_ext=list(raw.get("include_ext", [".vue", ".py", ".ts", ".tsx", ".js", ".jsx"])),
        test_globs=list(raw.get("test_globs", ["**/tests/**", "**/*.spec.*", "**/*.test.*"])),
        allow_multi_link_per_symbol=bool(raw.get("allow_multi_link_per_symbol", False)),
        critical_node_patterns=list(
            raw.get(
                "critical_node_patterns",
                ["interaction hooks", "trigger", "execution flow"],
            )
        ),
        thresholds=LintThresholds(
            node_linked_min=float(thresholds_raw.get("node_linked_min", 0.0)),
            critical_linked_min=float(thresholds_raw.get("critical_linked_min", 0.0)),
            node_tested_min=float(thresholds_raw.get("node_tested_min", 0.0)),
        ),
        diff_base=str(raw.get("diff_defaults", {}).get("base", "")),
        diff_head=str(raw.get("diff_defaults", {}).get("head", "")),
        diff_strict=bool(raw.get("diff_defaults", {}).get("strict", True)),
        diff_provider=str(raw.get("diff_defaults", {}).get("provider", "filesystem_snapshot")),
        schema_file=str(raw.get("schema", {}).get("file", DEFAULT_SCHEMA_SOURCE)),
        schema_mode=str(raw.get("schema", {}).get("mode", "compat")),
        schema_exclude_markdown_globs=list(
            raw.get("schema", {}).get("exclude_markdown_globs", ["README.md"])
        ),
        node_registry_file=str(raw.get("node_registry_file", ".iwp/node_registry.v1.json")),
        node_catalog_file=str(raw.get("node_catalog_file", ".iwp/node_catalog.v1.json")),
        cache_dir=str(raw.get("cache", {}).get("dir", ".iwp/cache")),
        snapshot_db_file=str(
            raw.get("cache", {}).get("snapshot_db_file", ".iwp/cache/snapshots.sqlite")
        ),
        node_index_db_file=str(
            raw.get("cache", {}).get("node_index_db_file", ".iwp/cache/node_index.v1.sqlite")
        ),
        task_dir=str(raw.get("task", {}).get("dir", ".iwp/tasks")),
        compiled_dir=str(raw.get("compiled", {}).get("dir", ".iwp/compiled")),
    )


def is_builtin_schema_source(schema_file: str) -> bool:
    normalized = schema_file.strip().lower()
    return normalized == "builtin" or normalized.startswith("builtin:")


def resolve_schema_source(config: LintConfig) -> str | Path:
    schema_file = config.schema_file.strip()
    if is_builtin_schema_source(schema_file):
        return schema_file
    return (config.project_root / schema_file).resolve()
