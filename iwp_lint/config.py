from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .versioning import (
    DEFAULT_NODE_CATALOG_FILE,
    DEFAULT_NODE_INDEX_DB_FILE,
    DEFAULT_NODE_REGISTRY_FILE,
    DEFAULT_SCHEMA_SOURCE,
)

CRITICAL_GRANULARITIES = {"all", "title_only"}
DEFAULT_TRACKING_EXCLUDE_GLOBS = [
    "**/node_modules/**",
    "**/dist/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
]
DEFAULT_PROTOCOL_INCLUDE_EXT = [".vue", ".py", ".ts", ".tsx", ".js", ".jsx"]
DEFAULT_TRACKING_MAX_FILE_SIZE_KB = 5120
DEFAULT_SNAPSHOT_INCLUDE_EXT = [
    ".vue",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
]


@dataclass
class LintThresholds:
    node_linked_min: float = 0.0
    critical_linked_min: float = 0.0
    node_tested_min: float = 0.0


@dataclass
class ModeThresholds:
    full: LintThresholds = field(default_factory=LintThresholds)
    diff: LintThresholds = field(default_factory=LintThresholds)


@dataclass
class TinyDiffConfig:
    min_impacted_nodes: int = 3
    node_tested_min_count: int = 1
    degrade_to_warning: bool = True


@dataclass
class CoverageProfile:
    name: str
    file_type_ids: list[str] = field(default_factory=list)
    section_keys: list[str] = field(default_factory=list)
    computed_kind_prefixes: list[str] = field(default_factory=list)
    anchor_levels: list[str] = field(default_factory=list)
    node_linked_min: float = 0.0
    critical_linked_min: float = 0.0
    missing_severity: str = "error"


@dataclass
class CodeSidecarConfig:
    enabled: bool = True
    dir: str = ".iwp/compiled/code"
    replace_pure_link_line: bool = True
    max_diagnostics: int = 20
    include_node_anchor_text: bool = True
    include_node_block_text: bool = True


@dataclass
class SessionConfig:
    auto_start_on_missing: bool = False
    link_density_threshold: float = 0.25
    code_diff_level: str = "summary"
    code_diff_context_lines: int = 3
    code_diff_max_chars: int = 12000
    diff_node_severity: str = "all"
    markdown_excerpt_max_chars: int = 240
    max_text_lines: int = 200
    max_hint_items: int = 20
    max_diagnostics_items: int = 20
    baseline_gap_max_items: int = 20
    warning_summary_top_n: int = 2


@dataclass
class HistoryRetentionConfig:
    max_snapshots: int = 200
    max_days: int = 30
    max_bytes: int = 2147483648


@dataclass
class HistorySafetyConfig:
    block_restore_on_dirty: bool = True
    auto_checkpoint_before_restore: bool = True
    strict_dulwich_restore: bool = False
    allow_sqlite_fallback: bool = True


@dataclass
class HistoryConfig:
    enabled: bool = True
    backend: str = "dulwich"
    git_dir: str = ".iwp/cache/history.git"
    retention: HistoryRetentionConfig = field(default_factory=HistoryRetentionConfig)
    safety: HistorySafetyConfig = field(default_factory=HistorySafetyConfig)


@dataclass
class WorkflowConfig:
    mode: str = "aligned"


@dataclass
class PageOnlyConfig:
    enabled: bool = False


@dataclass
class AuthoringTokensConfig:
    enabled: bool = True
    scope: str = "global"


@dataclass
class AuthoringConfig:
    tokens: AuthoringTokensConfig = field(default_factory=AuthoringTokensConfig)
    node_generation_mode: str = "structural"
    kind_unknown_policy: str = "warn"
    strict_annotation_params: bool = True
    strict_scopes: list[str] = field(default_factory=list)


@dataclass
class TrackingScopeConfig:
    include_ext: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    max_file_size_kb: int = DEFAULT_TRACKING_MAX_FILE_SIZE_KB


@dataclass
class TrackingConfig:
    protocol: TrackingScopeConfig = field(default_factory=TrackingScopeConfig)
    snapshot: TrackingScopeConfig = field(default_factory=TrackingScopeConfig)


def _default_coverage_profiles() -> list[CoverageProfile]:
    return [
        CoverageProfile(
            name="logic_high",
            file_type_ids=["logic", "state", "middleware"],
            node_linked_min=80.0,
            critical_linked_min=100.0,
            missing_severity="error",
        ),
        CoverageProfile(
            name="views_interaction",
            computed_kind_prefixes=[
                "views.pages.interaction_hooks",
                "views.components.interaction_hooks",
            ],
            anchor_levels=["interaction"],
            node_linked_min=100.0,
            critical_linked_min=100.0,
            missing_severity="error",
        ),
        CoverageProfile(
            name="views_structure",
            computed_kind_prefixes=[
                "views.pages.layout_tree",
                "views.components.layout",
                "views.pages.display_rules",
            ],
            anchor_levels=["structure"],
            node_linked_min=85.0,
            critical_linked_min=100.0,
            missing_severity="error",
        ),
        CoverageProfile(
            name="views_text",
            file_type_ids=["views.pages", "views.components"],
            anchor_levels=["text"],
            node_linked_min=0.0,
            critical_linked_min=0.0,
            missing_severity="warning",
        ),
        CoverageProfile(
            name="styles_locales",
            file_type_ids=["styles", "locales"],
            node_linked_min=0.0,
            critical_linked_min=0.0,
            missing_severity="warning",
        ),
    ]


@dataclass
class LintConfig:
    project_root: Path
    iwp_root: str = "InstructWare.iw"
    code_roots: list[str] = field(default_factory=lambda: ["."])
    tracking: TrackingConfig = field(
        default_factory=lambda: TrackingConfig(
            protocol=TrackingScopeConfig(
                include_ext=list(DEFAULT_PROTOCOL_INCLUDE_EXT),
                exclude_globs=list(DEFAULT_TRACKING_EXCLUDE_GLOBS),
            ),
            snapshot=TrackingScopeConfig(
                include_ext=list(DEFAULT_SNAPSHOT_INCLUDE_EXT),
                exclude_globs=list(DEFAULT_TRACKING_EXCLUDE_GLOBS),
            ),
        )
    )
    test_globs: list[str] = field(
        default_factory=lambda: ["**/tests/**", "**/*.spec.*", "**/*.test.*"]
    )
    allow_multi_link_per_symbol: bool = False
    enable_profile_coverage: bool = True
    critical_granularity: str = "all"
    critical_node_patterns: list[str] = field(
        default_factory=lambda: ["interaction hooks", "trigger", "execution flow"]
    )
    thresholds: LintThresholds = field(default_factory=LintThresholds)
    thresholds_by_mode: ModeThresholds = field(default_factory=ModeThresholds)
    tiny_diff: TinyDiffConfig = field(default_factory=TinyDiffConfig)
    coverage_profiles: list[CoverageProfile] = field(default_factory=_default_coverage_profiles)
    diff_base: str = ""
    diff_head: str = ""
    diff_strict: bool = True
    diff_provider: str = "filesystem_snapshot"
    schema_file: str = DEFAULT_SCHEMA_SOURCE
    schema_mode: str = "compat"
    schema_exclude_markdown_globs: list[str] = field(default_factory=lambda: ["README.md"])
    page_only: PageOnlyConfig = field(default_factory=PageOnlyConfig)
    authoring: AuthoringConfig = field(default_factory=AuthoringConfig)
    node_registry_file: str = DEFAULT_NODE_REGISTRY_FILE
    node_id_min_length: int = 4
    node_catalog_file: str = DEFAULT_NODE_CATALOG_FILE
    cache_dir: str = ".iwp/cache"
    snapshot_db_file: str = ".iwp/cache/snapshots.sqlite"
    node_index_db_file: str = DEFAULT_NODE_INDEX_DB_FILE
    compiled_dir: str = ".iwp/compiled"
    code_sidecar: CodeSidecarConfig = field(default_factory=CodeSidecarConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    execution_presets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.tracking, dict):
            self.tracking = _load_tracking(self.tracking)

    @property
    def iwp_root_path(self) -> Path:
        return (self.project_root / self.iwp_root).resolve()

    @property
    def protocol_include_ext(self) -> list[str]:
        return list(self.tracking.protocol.include_ext)

    @property
    def protocol_exclude_globs(self) -> list[str]:
        return list(self.tracking.protocol.exclude_globs)

    @property
    def snapshot_include_ext(self) -> list[str]:
        return list(self.tracking.snapshot.include_ext)

    @property
    def snapshot_exclude_globs(self) -> list[str]:
        return list(self.tracking.snapshot.exclude_globs)

    @property
    def snapshot_max_file_size_bytes(self) -> int:
        max_size_kb = int(self.tracking.snapshot.max_file_size_kb)
        return max(1, max_size_kb) * 1024

    @property
    def include_ext(self) -> list[str]:
        return self.protocol_include_ext

    @property
    def code_exclude_globs(self) -> list[str]:
        return self.protocol_exclude_globs


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
    tracking = _load_tracking(raw.get("tracking"))
    thresholds_raw = raw.get("thresholds", {})
    base_thresholds = _load_thresholds(thresholds_raw, fallback=None)
    mode_thresholds_raw = raw.get("thresholds_by_mode", {})
    tiny_diff_raw = raw.get("tiny_diff", {})
    profiles_raw = raw.get("coverage_profiles", [])
    session_raw = raw.get("session", {})
    history_raw = raw.get("history", {})
    workflow_raw = raw.get("workflow", {})
    history_retention_raw = history_raw.get("retention", {})
    history_safety_raw = history_raw.get("safety", {})
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

    schema_raw = raw.get("schema", {})
    page_only_raw = schema_raw.get("page_only", {})
    authoring_raw = raw.get("authoring", {})
    tokens_raw = authoring_raw.get("tokens", {})
    return LintConfig(
        project_root=project_root,
        iwp_root=raw.get("iwp_root", "InstructWare.iw"),
        code_roots=list(raw.get("code_roots", ["."])),
        tracking=tracking,
        test_globs=list(raw.get("test_globs", ["**/tests/**", "**/*.spec.*", "**/*.test.*"])),
        allow_multi_link_per_symbol=bool(raw.get("allow_multi_link_per_symbol", False)),
        enable_profile_coverage=bool(raw.get("enable_profile_coverage", True)),
        critical_granularity=_load_critical_granularity(raw.get("critical_granularity", "all")),
        critical_node_patterns=list(
            raw.get(
                "critical_node_patterns",
                ["interaction hooks", "trigger", "execution flow"],
            )
        ),
        thresholds=base_thresholds,
        thresholds_by_mode=ModeThresholds(
            full=_load_thresholds(mode_thresholds_raw.get("full", {}), fallback=base_thresholds),
            diff=_load_thresholds(mode_thresholds_raw.get("diff", {}), fallback=base_thresholds),
        ),
        tiny_diff=TinyDiffConfig(
            min_impacted_nodes=max(0, int(tiny_diff_raw.get("min_impacted_nodes", 3))),
            node_tested_min_count=max(0, int(tiny_diff_raw.get("node_tested_min_count", 1))),
            degrade_to_warning=bool(tiny_diff_raw.get("degrade_to_warning", True)),
        ),
        coverage_profiles=_load_coverage_profiles(profiles_raw),
        diff_base=str(raw.get("diff_defaults", {}).get("base", "")),
        diff_head=str(raw.get("diff_defaults", {}).get("head", "")),
        diff_strict=bool(raw.get("diff_defaults", {}).get("strict", True)),
        diff_provider=str(raw.get("diff_defaults", {}).get("provider", "filesystem_snapshot")),
        schema_file=str(schema_raw.get("file", DEFAULT_SCHEMA_SOURCE)),
        schema_mode=str(schema_raw.get("mode", "compat")),
        schema_exclude_markdown_globs=list(schema_raw.get("exclude_markdown_globs", ["README.md"])),
        page_only=PageOnlyConfig(
            enabled=bool(page_only_raw.get("enabled", False)),
        ),
        authoring=AuthoringConfig(
            tokens=AuthoringTokensConfig(
                enabled=bool(tokens_raw.get("enabled", True)),
                scope=str(tokens_raw.get("scope", "global")),
            ),
            node_generation_mode=_load_node_generation_mode(
                authoring_raw.get(
                    "node_generation_mode", raw.get("node_generation_mode", "structural")
                )
            ),
            kind_unknown_policy=_load_kind_unknown_policy(
                authoring_raw.get("kind_unknown_policy", "warn")
            ),
            strict_annotation_params=bool(authoring_raw.get("strict_annotation_params", True)),
            strict_scopes=[str(item) for item in authoring_raw.get("strict_scopes", [])],
        ),
        node_registry_file=str(raw.get("node_registry_file", DEFAULT_NODE_REGISTRY_FILE)),
        node_id_min_length=int(raw.get("node_id_min_length", 4)),
        node_catalog_file=str(raw.get("node_catalog_file", DEFAULT_NODE_CATALOG_FILE)),
        cache_dir=str(raw.get("cache", {}).get("dir", ".iwp/cache")),
        snapshot_db_file=str(
            raw.get("cache", {}).get("snapshot_db_file", ".iwp/cache/snapshots.sqlite")
        ),
        node_index_db_file=str(
            raw.get("cache", {}).get("node_index_db_file", DEFAULT_NODE_INDEX_DB_FILE)
        ),
        compiled_dir=str(raw.get("compiled", {}).get("dir", ".iwp/compiled")),
        code_sidecar=CodeSidecarConfig(
            enabled=bool(raw.get("code_sidecar", {}).get("enabled", True)),
            dir=str(raw.get("code_sidecar", {}).get("dir", ".iwp/compiled/code")),
            replace_pure_link_line=bool(
                raw.get("code_sidecar", {}).get("replace_pure_link_line", True)
            ),
            max_diagnostics=max(0, int(raw.get("code_sidecar", {}).get("max_diagnostics", 20))),
            include_node_anchor_text=bool(
                raw.get("code_sidecar", {}).get("include_node_anchor_text", True)
            ),
            include_node_block_text=bool(
                raw.get("code_sidecar", {}).get("include_node_block_text", True)
            ),
        ),
        session=SessionConfig(
            auto_start_on_missing=bool(session_raw.get("auto_start_on_missing", False)),
            link_density_threshold=_load_non_negative_float(
                session_raw.get("link_density_threshold", 0.25),
                fallback=0.25,
            ),
            code_diff_level=_load_code_diff_level(session_raw.get("code_diff_level", "summary")),
            code_diff_context_lines=max(0, int(session_raw.get("code_diff_context_lines", 3))),
            code_diff_max_chars=max(0, int(session_raw.get("code_diff_max_chars", 12000))),
            diff_node_severity=_load_node_severity(session_raw.get("diff_node_severity", "all")),
            markdown_excerpt_max_chars=max(
                0, int(session_raw.get("markdown_excerpt_max_chars", 240))
            ),
            max_text_lines=max(20, int(session_raw.get("max_text_lines", 200))),
            max_hint_items=max(1, int(session_raw.get("max_hint_items", 20))),
            max_diagnostics_items=max(1, int(session_raw.get("max_diagnostics_items", 20))),
            baseline_gap_max_items=max(1, int(session_raw.get("baseline_gap_max_items", 20))),
            warning_summary_top_n=max(1, int(session_raw.get("warning_summary_top_n", 2))),
        ),
        history=HistoryConfig(
            enabled=bool(history_raw.get("enabled", True)),
            backend=_load_history_backend(history_raw.get("backend", "dulwich")),
            git_dir=str(history_raw.get("git_dir", ".iwp/cache/history.git")),
            retention=HistoryRetentionConfig(
                max_snapshots=max(1, int(history_retention_raw.get("max_snapshots", 200))),
                max_days=max(1, int(history_retention_raw.get("max_days", 30))),
                max_bytes=max(1, int(history_retention_raw.get("max_bytes", 2147483648))),
            ),
            safety=HistorySafetyConfig(
                block_restore_on_dirty=bool(history_safety_raw.get("block_restore_on_dirty", True)),
                auto_checkpoint_before_restore=bool(
                    history_safety_raw.get("auto_checkpoint_before_restore", True)
                ),
                strict_dulwich_restore=bool(
                    history_safety_raw.get("strict_dulwich_restore", False)
                ),
                allow_sqlite_fallback=bool(history_safety_raw.get("allow_sqlite_fallback", True)),
            ),
        ),
        workflow=WorkflowConfig(
            mode=_load_workflow_mode(
                workflow_raw.get("mode", "aligned") if isinstance(workflow_raw, dict) else "aligned"
            ),
        ),
        execution_presets=_load_execution_presets(raw.get("execution_presets", {})),
    )


def _load_tracking(raw_tracking: Any) -> TrackingConfig:
    if raw_tracking is None:
        raise RuntimeError(
            "missing required config: `tracking`.\n"
            "expected structure:\n"
            "tracking:\n"
            "  protocol:\n"
            "    include_ext: [...]\n"
            "    exclude_globs: [...]\n"
            "  snapshot:\n"
            "    include_ext: [...]\n"
            "    exclude_globs: [...]"
        )
    if not isinstance(raw_tracking, dict):
        raise RuntimeError("`tracking` must be an object")
    protocol_raw = raw_tracking.get("protocol")
    snapshot_raw = raw_tracking.get("snapshot")
    if not isinstance(protocol_raw, dict) or not isinstance(snapshot_raw, dict):
        raise RuntimeError("`tracking.protocol` and `tracking.snapshot` must both be objects")
    protocol_include_ext = _normalize_include_ext(protocol_raw.get("include_ext"))
    snapshot_include_ext = _normalize_include_ext(snapshot_raw.get("include_ext"))
    protocol_exclude_globs = _normalize_globs(protocol_raw.get("exclude_globs"))
    snapshot_exclude_globs = _normalize_globs(snapshot_raw.get("exclude_globs"))
    protocol_max_file_size_kb = _load_tracking_max_file_size_kb(
        protocol_raw.get("max_file_size_kb"),
        field_name="tracking.protocol.max_file_size_kb",
    )
    snapshot_max_file_size_kb = _load_tracking_max_file_size_kb(
        snapshot_raw.get("max_file_size_kb"),
        field_name="tracking.snapshot.max_file_size_kb",
    )
    if not protocol_include_ext:
        raise RuntimeError("`tracking.protocol.include_ext` must not be empty")
    if not snapshot_include_ext:
        raise RuntimeError("`tracking.snapshot.include_ext` must not be empty")
    return TrackingConfig(
        protocol=TrackingScopeConfig(
            include_ext=protocol_include_ext,
            exclude_globs=protocol_exclude_globs,
            max_file_size_kb=protocol_max_file_size_kb,
        ),
        snapshot=TrackingScopeConfig(
            include_ext=snapshot_include_ext,
            exclude_globs=snapshot_exclude_globs,
            max_file_size_kb=snapshot_max_file_size_kb,
        ),
    )


def _normalize_include_ext(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise RuntimeError("tracking scope `include_ext` must be a list")
    normalized: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        normalized.append(value if value.startswith(".") else f".{value}")
    return sorted(set(normalized))


def _normalize_globs(raw: Any) -> list[str]:
    if raw is None:
        return list(DEFAULT_TRACKING_EXCLUDE_GLOBS)
    if not isinstance(raw, list):
        raise RuntimeError("tracking scope `exclude_globs` must be a list")
    normalized: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value:
            normalized.append(value)
    return normalized


def _load_tracking_max_file_size_kb(raw: Any, *, field_name: str) -> int:
    if raw is None:
        return DEFAULT_TRACKING_MAX_FILE_SIZE_KB
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"`{field_name}` must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"`{field_name}` must be a positive integer")
    return value


def _load_coverage_profiles(raw_profiles: Any) -> list[CoverageProfile]:
    if not isinstance(raw_profiles, list) or not raw_profiles:
        return _default_coverage_profiles()
    profiles: list[CoverageProfile] = []
    for idx, raw in enumerate(raw_profiles):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", f"profile_{idx}"))
        profiles.append(
            CoverageProfile(
                name=name,
                file_type_ids=[str(item) for item in raw.get("file_type_ids", [])],
                section_keys=[str(item) for item in raw.get("section_keys", [])],
                computed_kind_prefixes=[
                    str(item) for item in raw.get("computed_kind_prefixes", [])
                ],
                anchor_levels=[str(item) for item in raw.get("anchor_levels", [])],
                node_linked_min=float(raw.get("node_linked_min", 0.0)),
                critical_linked_min=float(raw.get("critical_linked_min", 0.0)),
                missing_severity=str(raw.get("missing_severity", "error")),
            )
        )
    return profiles or _default_coverage_profiles()


def _load_execution_presets(raw_presets: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_presets, dict):
        return {}
    presets: dict[str, dict[str, Any]] = {}
    for name, raw_value in raw_presets.items():
        if not isinstance(name, str):
            continue
        key = name.strip()
        if not key or not isinstance(raw_value, dict):
            continue
        normalized: dict[str, Any] = {}
        for command_name, command_options in raw_value.items():
            if not isinstance(command_name, str) or not isinstance(command_options, dict):
                continue
            command_key = command_name.strip()
            if not command_key:
                continue
            normalized[command_key] = dict(command_options)
        if normalized:
            presets[key] = normalized
    return presets


def _load_thresholds(raw: Any, fallback: LintThresholds | None) -> LintThresholds:
    base = fallback or LintThresholds()
    if not isinstance(raw, dict):
        return LintThresholds(
            node_linked_min=base.node_linked_min,
            critical_linked_min=base.critical_linked_min,
            node_tested_min=base.node_tested_min,
        )
    return LintThresholds(
        node_linked_min=float(raw.get("node_linked_min", base.node_linked_min)),
        critical_linked_min=float(raw.get("critical_linked_min", base.critical_linked_min)),
        node_tested_min=float(raw.get("node_tested_min", base.node_tested_min)),
    )


def _load_critical_granularity(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in CRITICAL_GRANULARITIES:
        return value
    return "all"


def _load_non_negative_float(raw: Any, *, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if value < 0:
        return fallback
    return value


def _load_code_diff_level(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"summary", "hunk"}:
        return value
    return "summary"


def _load_node_severity(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"all", "error", "warning"}:
        return value
    return "all"


def _load_kind_unknown_policy(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"warn", "error"}:
        return value
    return "warn"


def _load_node_generation_mode(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"structural", "annotated_only"}:
        return value
    return "structural"


def _load_workflow_mode(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"fast", "aligned"}:
        return value
    return "aligned"


def _load_history_backend(raw: Any) -> str:
    value = str(raw).strip().lower()
    if value in {"snapshot", "dulwich"}:
        return value
    return "dulwich"


def is_builtin_schema_source(schema_file: str) -> bool:
    normalized = schema_file.strip().lower()
    return normalized == "builtin" or normalized.startswith("builtin:")


def resolve_schema_source(config: LintConfig) -> str | Path:
    schema_file = config.schema_file.strip()
    if is_builtin_schema_source(schema_file):
        return schema_file
    return (config.project_root / schema_file).resolve()
