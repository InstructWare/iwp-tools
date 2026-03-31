from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from iwp_build.cli import build_parser as build_build_parser
from iwp_build.output import collect_remediation_hints
from iwp_lint.cli import _print_nodes_result
from iwp_lint.config import (
    DEFAULT_TRACKING_EXCLUDE_GLOBS,
    AuthoringConfig,
    LintConfig,
    LintThresholds,
    ModeThresholds,
    TinyDiffConfig,
    TrackingConfig,
    TrackingScopeConfig,
    load_config,
    resolve_schema_source,
)
from iwp_lint.core.coverage_policy import compute_metrics
from iwp_lint.core.engine import print_console_report, run_diff, run_schema
from iwp_lint.core.link_normalizer import normalize_links
from iwp_lint.core.node_catalog import (
    build_code_sidecar_context,
    build_node_catalog,
    compile_node_context,
    query_node_catalog,
    verify_compiled_context,
)
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.schema.schema_loader import load_schema_profile
from iwp_lint.schema.schema_semantics import resolve_section_keys
from iwp_lint.schema.schema_validator import validate_markdown_schema
from iwp_lint.vcs.diff_resolver import load_diff
from iwp_lint.vcs.snapshot_store import SnapshotStore, collect_workspace_files
from iwp_lint.versioning import (
    DEFAULT_NODE_CATALOG_FILE,
    DEFAULT_NODE_INDEX_DB_FILE,
    DEFAULT_NODE_REGISTRY_FILE,
    DEFAULT_SCHEMA_SOURCE,
    IWC_JSON_FORMAT_VERSION,
    IWC_MD_META_VERSION,
    SUPPORTED_IWC_JSON_VERSIONS,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_schema() -> dict:
    return {
        "schema_name": "test",
        "schema_version": "1.0.0",
        "modes": {"default": "compat", "supported": ["compat", "strict"]},
        "global_rules": {
            "h1_required_exactly_one": True,
            "h2_unknown_policy": {"compat": "warn", "strict": "error"},
        },
        "kind_rules": {"format": "{file_type_id}.{section_key}"},
        "authoring_rules": {
            "aliases": [
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "trigger",
                    "target_file_type_id": "logic",
                    "target_section_key": "trigger",
                    "labels": ["Logic.Trigger"],
                    "title_aliases": ["Logic.Trigger", "Logic Trigger", "Logic_Trigger"],
                },
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "input",
                    "target_file_type_id": "logic",
                    "target_section_key": "input",
                    "labels": ["Logic.Input"],
                    "title_aliases": ["Logic.Input", "Logic Input", "Logic_Input"],
                },
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "output",
                    "target_file_type_id": "logic",
                    "target_section_key": "output",
                    "labels": ["Logic.Output"],
                    "title_aliases": ["Logic.Output", "Logic Output", "Logic_Output"],
                },
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "fields",
                    "target_file_type_id": "state",
                    "target_section_key": "fields",
                    "labels": ["State.Fields"],
                    "title_aliases": ["State.Fields", "State Fields", "State_Fields"],
                },
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "constraints",
                    "target_file_type_id": "state",
                    "target_section_key": "constraints",
                    "labels": ["State.Constraints"],
                    "title_aliases": [
                        "State.Constraints",
                        "State Constraints",
                        "State_Constraints",
                    ],
                },
                {
                    "source_file_type_id": "views.pages",
                    "source_section_key": "update_rules",
                    "target_file_type_id": "state",
                    "target_section_key": "update_rules",
                    "labels": ["State.UpdateRules"],
                    "title_aliases": [
                        "State.UpdateRules",
                        "State UpdateRules",
                        "State_UpdateRules",
                    ],
                },
            ]
        },
        "section_i18n": {
            "layout_tree": {"en": ["Layout Tree"]},
            "interaction_hooks": {"en": ["Interaction Hooks"]},
            "trigger": {"en": ["Trigger"]},
            "input": {"en": ["Input"]},
            "output": {"en": ["Output"]},
            "fields": {"en": ["Fields"]},
            "constraints": {"en": ["Constraints"]},
            "update_rules": {"en": ["Update Rules"]},
            "dup_a": {"en": ["Shared"]},
            "dup_b": {"en": ["Shared"]},
        },
        "file_type_schemas": [
            {
                "id": "views.pages",
                "path_patterns": ["views/pages/**/*.md", "views/pages/*.md"],
                "allow_unknown_sections": False,
                "sections": [
                    {"key": "layout_tree", "required": True},
                    {"key": "interaction_hooks", "required": False},
                ],
            }
        ],
    }


def _workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = Path.cwd() / ".tmp_iwp_lint_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


def _tracking_ts() -> TrackingConfig:
    return TrackingConfig(
        protocol=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
        snapshot=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
    )


def _tracking_config_raw() -> dict[str, object]:
    return {
        "tracking": {
            "protocol": {"include_ext": [".ts"]},
            "snapshot": {"include_ext": [".ts"]},
        }
    }


class IwpLintRegressionTests(unittest.TestCase):
    def test_iwp_build_cli_supports_build_verify_watch_only(self) -> None:
        parser = build_build_parser()
        parsed = parser.parse_args(["build"])
        self.assertEqual(parsed.command, "build")
        self.assertEqual(parsed.mode, "auto")
        self.assertFalse(parsed.normalize_links)
        self.assertFalse(parsed.no_code_sidecar)
        parsed_with_normalize = parser.parse_args(["build", "--normalize-links"])
        self.assertTrue(parsed_with_normalize.normalize_links)
        parsed_without_sidecar = parser.parse_args(["build", "--no-code-sidecar"])
        self.assertTrue(parsed_without_sidecar.no_code_sidecar)
        parsed_verify = parser.parse_args(["verify", "--min-severity", "error", "--quiet-warnings"])
        self.assertEqual(parsed_verify.command, "verify")
        self.assertEqual(parsed_verify.min_severity, "error")
        self.assertTrue(parsed_verify.quiet_warnings)

    def test_iwp_lint_cli_supports_check_alias(self) -> None:
        from iwp_lint.cli import build_parser as build_lint_parser

        parser = build_lint_parser()
        parsed = parser.parse_args(["check"])
        self.assertEqual(parsed.command, "check")
        parsed_full = parser.parse_args(["full", "--min-severity", "error", "--quiet-warnings"])
        self.assertEqual(parsed_full.min_severity, "error")
        self.assertTrue(parsed_full.quiet_warnings)
        parsed_diff = parser.parse_args(["diff", "--min-severity", "warning"])
        self.assertEqual(parsed_diff.min_severity, "warning")
        parsed_links_sidecar = parser.parse_args(["links", "sidecar"])
        self.assertEqual(parsed_links_sidecar.command, "links")
        self.assertEqual(parsed_links_sidecar.links_action, "sidecar")

    def test_print_console_report_tags_and_filters_warning_lines(self) -> None:
        report = {
            "mode": "full",
            "summary": {
                "error_count": 1,
                "warning_count": 1,
                "total_nodes_in_scope": 2,
                "covered_nodes": 1,
            },
            "metrics": {
                "node_linked_percent": 50.0,
                "critical_linked_percent": 0.0,
                "node_tested_percent": 0.0,
            },
            "diagnostics": [
                {
                    "code": "IWP105",
                    "file_path": "views/pages/home.md",
                    "line": 1,
                    "column": 0,
                    "message": "node mismatch",
                    "severity": "error",
                },
                {
                    "code": "IWP107",
                    "file_path": "views/pages/home.md",
                    "line": 2,
                    "column": 0,
                    "message": "node not covered",
                    "severity": "warning",
                },
            ],
        }
        with StringIO() as buf, redirect_stdout(buf):
            print_console_report(report, min_severity="error")
            out = buf.getvalue()
        self.assertIn("status=FAIL", out)
        self.assertIn("[E][IWP105]", out)
        self.assertNotIn("[W][IWP107]", out)

    def test_print_console_report_quiet_warnings_keeps_summary(self) -> None:
        report = {
            "mode": "full",
            "summary": {
                "error_count": 0,
                "warning_count": 1,
                "total_nodes_in_scope": 1,
                "covered_nodes": 0,
            },
            "metrics": {
                "node_linked_percent": 0.0,
                "critical_linked_percent": 0.0,
                "node_tested_percent": 0.0,
            },
            "diagnostics": [
                {
                    "code": "IWP107",
                    "file_path": "views/pages/home.md",
                    "line": 2,
                    "column": 0,
                    "message": "node not covered",
                    "severity": "warning",
                }
            ],
        }
        with StringIO() as buf, redirect_stdout(buf):
            print_console_report(report, quiet_warnings=True)
            out = buf.getvalue()
        self.assertIn("status=PASS_WITH_WARNINGS", out)
        self.assertNotIn("[W][IWP107]", out)

    def test_iwp_lint_cli_unknown_command_prints_hint(self) -> None:
        from iwp_lint.cli import build_parser as build_lint_parser

        parser = build_lint_parser()
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                parser.parse_args(["ful"])
        self.assertIn("Did you mean `iwp-lint full`?", stderr.getvalue())

    def test_tuple_key_metrics_count_distinct_source_path(self) -> None:
        metrics = compute_metrics(
            nodes=[],
            linked_node_keys={("a.md", "dup.id"), ("b.md", "dup.id")},
            critical_node_keys={("a.md", "dup.id"), ("b.md", "dup.id")},
            linked_critical_node_keys={("a.md", "dup.id"), ("b.md", "dup.id")},
            tested_node_keys={("a.md", "dup.id"), ("b.md", "dup.id")},
        )
        self.assertEqual(metrics.linked_nodes, 2)
        self.assertEqual(metrics.critical_nodes, 2)
        self.assertEqual(metrics.tested_nodes, 2)

    def test_markdown_exclude_scope_is_consistent(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- A\n",
            )
            _write(
                iwp_root / "README.md",
                "# Readme\n\n## Layout Tree\n- Ignore me\n",
            )

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                exclude_markdown_globs=["README.md"],
            )
            self.assertTrue(nodes)
            self.assertTrue(all(node.source_path != "README.md" for node in nodes))

            schema_result = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                exclude_markdown_globs=["README.md"],
            )
            self.assertEqual(schema_result.checked_files, 1)

    def test_config_root_defaults_to_config_directory(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(cfg_dir / ".iwp-lint.json", json.dumps(_tracking_config_raw()))
            config = load_config(str(cfg_dir / ".iwp-lint.json"), cwd=root / "somewhere")
            self.assertEqual(config.project_root, cfg_dir.resolve())

    def test_config_project_root_can_be_relative_to_config_dir(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(
                cfg_dir / ".iwp-lint.json",
                json.dumps({**_tracking_config_raw(), "project_root": ".."}),
            )
            config = load_config(str(cfg_dir / ".iwp-lint.json"), cwd=root / "other")
            self.assertEqual(config.project_root, root.resolve())

    def test_config_code_exclude_globs_defaults_and_override(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(cfg_dir / ".iwp-lint.json", json.dumps(_tracking_config_raw()))
            config_default = load_config(str(cfg_dir / ".iwp-lint.json"))
            self.assertEqual(config_default.protocol_exclude_globs, DEFAULT_TRACKING_EXCLUDE_GLOBS)

            _write(
                cfg_dir / ".iwp-lint.custom.json",
                json.dumps(
                    {
                        **_tracking_config_raw(),
                        "tracking": {
                            "protocol": {
                                "include_ext": [".ts"],
                                "exclude_globs": ["**/vendor/**"],
                            },
                            "snapshot": {"include_ext": [".ts"], "exclude_globs": []},
                        },
                    }
                ),
            )
            config_custom = load_config(str(cfg_dir / ".iwp-lint.custom.json"))
            self.assertEqual(config_custom.protocol_exclude_globs, ["**/vendor/**"])

    def test_config_requires_tracking_section(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(cfg_dir / ".iwp-lint.missing-tracking.json", json.dumps({"project_root": ".."}))
            with self.assertRaisesRegex(RuntimeError, "missing required config: `tracking`"):
                load_config(str(cfg_dir / ".iwp-lint.missing-tracking.json"))

    def test_config_page_only_can_be_enabled(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(
                cfg_dir / ".iwp-lint.json",
                json.dumps({**_tracking_config_raw(), "schema": {"page_only": {"enabled": True}}}),
            )
            config = load_config(str(cfg_dir / ".iwp-lint.json"))
            self.assertTrue(config.page_only.enabled)

    def test_config_strict_annotation_params_defaults_to_true(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(cfg_dir / ".iwp-lint.json", json.dumps(_tracking_config_raw()))
            config = load_config(str(cfg_dir / ".iwp-lint.json"))
            self.assertTrue(config.authoring.strict_annotation_params)

    def test_config_strict_annotation_params_can_be_disabled(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(
                cfg_dir / ".iwp-lint.json",
                json.dumps(
                    {**_tracking_config_raw(), "authoring": {"strict_annotation_params": False}}
                ),
            )
            config = load_config(str(cfg_dir / ".iwp-lint.json"))
            self.assertFalse(config.authoring.strict_annotation_params)

    def test_config_workflow_mode_defaults_to_aligned(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(cfg_dir / ".iwp-lint.json", json.dumps(_tracking_config_raw()))
            config = load_config(str(cfg_dir / ".iwp-lint.json"))
            self.assertEqual(config.workflow.mode, "aligned")

    def test_config_workflow_mode_accepts_fast_and_fallbacks_on_invalid(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(
                cfg_dir / ".iwp-lint.fast.json",
                json.dumps({**_tracking_config_raw(), "workflow": {"mode": "fast"}}),
            )
            fast_config = load_config(str(cfg_dir / ".iwp-lint.fast.json"))
            self.assertEqual(fast_config.workflow.mode, "fast")
            _write(
                cfg_dir / ".iwp-lint.invalid.json",
                json.dumps({**_tracking_config_raw(), "workflow": {"mode": "unknown"}}),
            )
            invalid_config = load_config(str(cfg_dir / ".iwp-lint.invalid.json"))
            self.assertEqual(invalid_config.workflow.mode, "aligned")

    def test_schema_authoring_aliases_are_loaded_from_schema_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            profile = load_schema_profile(schema_path)
            alias_labels = [label for alias in profile.authoring_aliases for label in alias.labels]
            self.assertIn("Logic.Trigger", alias_labels)
            self.assertIn("State.UpdateRules", alias_labels)

    def test_builtin_schema_source_works_without_local_schema_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Node\n",
            )
            config = LintConfig(project_root=root, iwp_root="InstructWare.iw")
            self.assertEqual(resolve_schema_source(config), "builtin:iwp-schema.v1")
            report = run_schema(config)
            self.assertIn("summary", report)

    def test_page_only_parser_maps_namespaced_h2_to_logic_and_state(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "\n".join(
                    [
                        "# Home",
                        "",
                        "## Layout Tree",
                        "- Hero",
                        "",
                        "## Logic.Trigger",
                        "- Click CTA",
                        "",
                        "## Logic.Input",
                        "- User context",
                        "",
                        "## Logic.Output",
                        "- Navigate docs",
                        "",
                        "## State.Fields",
                        "- current_doc",
                    ]
                )
                + "\n",
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                page_only_enabled=True,
            )
            mapped = {node.anchor_text: node for node in nodes}
            self.assertEqual(mapped["Click CTA"].file_type_id, "logic")
            self.assertEqual(mapped["Click CTA"].section_key, "trigger")
            self.assertEqual(mapped["Click CTA"].computed_kind, "logic.trigger")
            self.assertEqual(mapped["current_doc"].file_type_id, "state")
            self.assertEqual(mapped["current_doc"].section_key, "fields")
            self.assertEqual(mapped["current_doc"].computed_kind, "state.fields")
            self.assertEqual(mapped["Click CTA"].source_path, "views/pages/home.md")

    def test_page_only_schema_allows_namespaced_h2_only_when_enabled(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "\n".join(
                    [
                        "# Home",
                        "",
                        "## Layout Tree",
                        "- Hero",
                        "",
                        "## Logic.Trigger",
                        "- Click CTA",
                    ]
                )
                + "\n",
            )
            disabled_diags = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="strict",
                page_only_enabled=False,
            ).diagnostics
            self.assertTrue(
                any(
                    "Logic.Trigger" in diag.message
                    for diag in disabled_diags
                    if diag.code == "IWP202"
                )
            )
            enabled_diags = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="strict",
                page_only_enabled=True,
            ).diagnostics
            self.assertFalse(any(diag.code == "IWP202" for diag in enabled_diags))

    def test_annotation_param_unknown_kind_is_error(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- CTA @iwp(kind=views.pages.unknown_section)\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=True,
            ).diagnostics
            self.assertTrue(
                any(
                    d.code == "IWP301"
                    and "Unknown `kind` value" in d.message
                    and d.severity == "error"
                    for d in diagnostics
                )
            )

    def test_annotation_param_requires_file_and_section_together(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- CTA @iwp(file=views.pages)\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=True,
            ).diagnostics
            self.assertTrue(any(d.code == "IWP302" and d.severity == "error" for d in diagnostics))

    def test_annotation_param_detects_kind_file_section_conflict(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                (
                    "# Home\n\n## Layout Tree\n"
                    "- CTA @iwp(kind=views.pages.layout_tree,file=views.pages,section=interaction_hooks)\n"
                ),
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=True,
            ).diagnostics
            self.assertTrue(any(d.code == "IWP303" and d.severity == "error" for d in diagnostics))

    def test_annotation_param_type_key_is_rejected(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- CTA @iwp(type=policy.rule)\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=True,
            ).diagnostics
            self.assertTrue(
                any(
                    d.code == "IWP301"
                    and "Unsupported annotation parameter key" in d.message
                    and d.severity == "error"
                    for d in diagnostics
                )
            )

    def test_annotation_param_valid_forms_pass(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "\n".join(
                    [
                        "# Home",
                        "",
                        "## Layout Tree",
                        "- CTA @iwp",
                        "- CTA2 @iwp(kind=views.pages.layout_tree)",
                        "- CTA3 @iwp(file=views.pages,section=interaction_hooks)",
                    ]
                )
                + "\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=True,
            ).diagnostics
            self.assertFalse(any(d.code.startswith("IWP30") for d in diagnostics))

    def test_annotation_param_strict_check_can_be_disabled(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- CTA @iwp\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="compat",
                strict_annotation_params=False,
            ).diagnostics
            self.assertFalse(any(d.code.startswith("IWP30") for d in diagnostics))

    def test_versioning_constants_drive_default_config(self) -> None:
        config = LintConfig(project_root=Path.cwd())
        self.assertEqual(config.schema_file, DEFAULT_SCHEMA_SOURCE)
        self.assertEqual(config.node_registry_file, DEFAULT_NODE_REGISTRY_FILE)
        self.assertEqual(config.node_catalog_file, DEFAULT_NODE_CATALOG_FILE)
        self.assertEqual(config.node_index_db_file, DEFAULT_NODE_INDEX_DB_FILE)
        self.assertIn(IWC_JSON_FORMAT_VERSION, SUPPORTED_IWC_JSON_VERSIONS)

    def test_section_i18n_ambiguity_is_detected(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["section_i18n"] = {
                "layout_tree": {"en": ["Layout Tree"]},
                "dup_a": {"en": ["Shared"]},
                "dup_b": {"en": ["Shared"]},
            }
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Shared\n- item\n",
            )

            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="strict",
            ).diagnostics
            self.assertTrue(
                any(
                    d.code == "IWP204" and "Ambiguous section title mapping" in d.message
                    for d in diagnostics
                )
            )
            self.assertEqual(
                resolve_section_keys("Shared", schema["section_i18n"]), ["dup_a", "dup_b"]
            )

    def test_node_id_is_stable_when_list_items_reordered(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            target_file = iwp_root / "views/pages/home.md"
            _write(
                target_file,
                "# Home\n\n## Layout Tree\n- Alpha\n- Beta\n",
            )

            first_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            first_map = {
                node.anchor_text: node.node_id
                for node in first_nodes
                if node.anchor_text in {"Alpha", "Beta"}
            }

            _write(
                target_file,
                "# Home\n\n## Layout Tree\n- Beta\n- Alpha\n",
            )
            second_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            second_map = {
                node.anchor_text: node.node_id
                for node in second_nodes
                if node.anchor_text in {"Alpha", "Beta"}
            }

            self.assertEqual(first_map, second_map)

    def test_node_id_is_stable_for_cjk_minor_text_change(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["section_i18n"] = {"layout_tree": {"zh-CN": ["布局树"]}}
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema, ensure_ascii=False))
            target_file = iwp_root / "views/pages/home.md"
            _write(
                target_file,
                "# 首页\n\n## 布局树\n- 阅读宣言\n",
            )

            first_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            first_id = next(node.node_id for node in first_nodes if node.anchor_text == "阅读宣言")

            _write(
                target_file,
                "# 首页\n\n## 布局树\n- 阅读《宣言》\n",
            )
            second_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            second_id = next(
                node.node_id for node in second_nodes if node.anchor_text == "阅读《宣言》"
            )

            self.assertEqual(first_id, second_id)

    def test_node_id_uses_short_unique_prefix_per_source(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            items = "\n".join(f"- Item {idx}" for idx in range(40))
            _write(
                iwp_root / "views/pages/home.md",
                f"# Home\n\n## Layout Tree\n{items}\n",
            )

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_id_min_length=1,
            )
            list_nodes = [node for node in nodes if node.anchor_text.startswith("Item ")]
            ids = [node.node_id for node in list_nodes]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(any(len(node_id.split(".", 1)[1]) > 1 for node_id in ids))

    def test_node_catalog_build_and_query(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n- Read Protocol\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
            )
            build_result = build_node_catalog(config)
            self.assertEqual(build_result["entry_count"], 4)

            query_result = query_node_catalog(
                config=config,
                source_path="views/pages/home.md",
                text="read manifesto",
                line=None,
                limit=2,
                exact_text=False,
            )
            raw_returned = query_result.get("returned", 0)
            returned = raw_returned if isinstance(raw_returned, int) else 0
            self.assertGreaterEqual(returned, 1)
            results = query_result.get("results", [])
            self.assertTrue(isinstance(results, list) and results)
            first_item: object = results[0] if isinstance(results, list) and results else {}
            top: dict[str, object] = first_item if isinstance(first_item, dict) else {}
            self.assertEqual(top.get("anchor_text"), "Read Manifesto")
            self.assertIn("index_db_path", build_result)

    def test_node_catalog_query_requires_catalog_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
            )
            with self.assertRaises(RuntimeError):
                query_node_catalog(
                    config=config,
                    source_path=None,
                    text="anything",
                    line=None,
                    limit=5,
                    exact_text=False,
                )

    def test_views_text_marker_sets_anchor_level(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["section_i18n"]["display_rules"] = {"en": ["Display Rules"]}
            schema["marker_rules"] = {
                "text_marker": {
                    "enabled": True,
                    "token": "[text]",
                    "allowed_sections": ["layout_tree", "display_rules"],
                }
            }
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- [text] Hero title\n- Hero wrapper\n",
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            text_node = next(item for item in nodes if item.anchor_text == "Hero title")
            structure_node = next(item for item in nodes if item.anchor_text == "Hero wrapper")
            self.assertEqual(text_node.anchor_level, "text")
            self.assertEqual(structure_node.anchor_level, "structure")

    def test_text_marker_outside_allowed_sections_is_error(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["section_i18n"]["interaction_hooks"] = {"en": ["Interaction Hooks"]}
            schema["file_type_schemas"][0]["sections"].append(
                {"key": "interaction_hooks", "required": False}
            )
            schema["marker_rules"] = {
                "text_marker": {
                    "enabled": True,
                    "token": "[text]",
                    "allowed_sections": ["layout_tree"],
                }
            }
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Interaction Hooks\n- [text] should fail\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root, schema_path=schema_path, mode="strict"
            ).diagnostics
            self.assertTrue(
                any(
                    d.code == "IWP204"
                    and "`[text]` marker is not allowed in this section" in d.message
                    for d in diagnostics
                )
            )

    def test_profile_coverage_marks_views_text_as_warning(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["marker_rules"] = {
                "text_marker": {
                    "enabled": True,
                    "token": "[text]",
                    "allowed_sections": ["layout_tree"],
                }
            }
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- [text] Hero title\n",
            )
            _write(root / "_ir/src/view.ts", "export const ok = true;\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
            )
            from iwp_lint.core.engine import run_full

            report = run_full(config)
            warning_nodes = [
                diag
                for diag in report["diagnostics"]
                if diag["code"] == "IWP107" and diag["severity"] == "warning"
            ]
            self.assertTrue(warning_nodes)

    def test_links_normalize_removes_duplicate_and_stale_links(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Alpha\n- Beta\n",
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            alpha = next(item for item in nodes if item.anchor_text == "Alpha")
            beta = next(item for item in nodes if item.anchor_text == "Beta")
            stale = "n.deadbeef"
            code_file = root / "_ir/src/view.ts"
            _write(
                code_file,
                "\n".join(
                    [
                        f"// @iwp.link views/pages/home.md::{alpha.node_id}",
                        f"// @iwp.link views/pages/home.md::{alpha.node_id}",
                        f"// @iwp.link views/pages/home.md::{stale}",
                        f"// @iwp.link views/pages/home.md::{beta.node_id}",
                    ]
                )
                + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
            )
            check = normalize_links(config=config, write=False)
            self.assertEqual(check["changed_count"], 1)
            write = normalize_links(config=config, write=True)
            self.assertEqual(write["removed_stale_links"], 1)
            self.assertEqual(write["removed_duplicate_links"], 1)

    def test_compile_and_verify_iwc_context(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_result = compile_node_context(config)
            self.assertEqual(compile_result["compiled_count"], 1)
            self.assertNotIn("compiled_files", compile_result)
            compiled_json_file = root / ".iwp/compiled/json/views/pages/home.md.iwc.json"
            compiled_md_file = root / ".iwp/compiled/md/views/pages/home.md.iwc.md"
            payload = json.loads(compiled_json_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], IWC_JSON_FORMAT_VERSION)
            self.assertEqual(payload["artifact"], "iwc")
            self.assertIn("dict", payload)
            self.assertEqual(len(payload["nodes"]), 3)
            first_node = payload["nodes"][0]
            self.assertIsInstance(first_node, list)
            self.assertEqual(len(first_node), 10)
            self.assertIsInstance(first_node[9], str)
            md_text = compiled_md_file.read_text(encoding="utf-8")
            self.assertIn("<!-- @iwp.meta artifact=iwc_md -->", md_text)
            self.assertIn(f"<!-- @iwp.meta version={IWC_MD_META_VERSION} -->", md_text)
            self.assertIn("<!-- @iwp.node id=", md_text)

            verify_result = verify_compiled_context(config)
            self.assertTrue(verify_result["ok"])
            self.assertEqual(verify_result["checked_sources"], 1)

    def test_verify_iwc_annotated_only_accepts_non_annotated_markdown_sources(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree @iwp\n- Hero @iwp\n",
            )
            _write(
                iwp_root / "views/pages/locales/en.md",
                "# Home Copy\n\n- title: Home\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
                authoring=AuthoringConfig(node_generation_mode="annotated_only"),
            )
            compile_result = compile_node_context(config)
            self.assertEqual(compile_result["compiled_count"], 1)

            verify_result = verify_compiled_context(config)
            self.assertTrue(verify_result["ok"])
            self.assertEqual(verify_result["checked_sources"], 1)
            self.assertEqual(verify_result["missing_files"], [])

    def test_verify_iwc_detects_invalid_node_shape(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)
            compiled_file = root / ".iwp/compiled/json/views/pages/home.md.iwc.json"
            payload = json.loads(compiled_file.read_text(encoding="utf-8"))
            payload["nodes"][0] = payload["nodes"][0][:-1]
            compiled_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            verify_result = verify_compiled_context(config)
            self.assertFalse(verify_result["ok"])
            self.assertEqual(verify_result["invalid_files"], ["views/pages/home.md"])

    def test_verify_iwc_detects_stale_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            page_file = iwp_root / "views/pages/home.md"
            _write(
                page_file,
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)

            _write(
                page_file,
                "# Home\n\n## Layout Tree\n- Read Protocol\n",
            )
            verify_result = verify_compiled_context(config)
            self.assertFalse(verify_result["ok"])
            self.assertEqual(verify_result["stale_files"], ["views/pages/home.md"])

    def test_compile_iwc_removes_deleted_source_artifact(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            home_file = iwp_root / "views/pages/home.md"
            about_file = iwp_root / "views/pages/about.md"
            _write(home_file, "# Home\n\n## Layout Tree\n- Read Manifesto\n")
            _write(about_file, "# About\n\n## Layout Tree\n- About Content\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)
            about_compiled_json = root / ".iwp/compiled/json/views/pages/about.md.iwc.json"
            about_compiled_md = root / ".iwp/compiled/md/views/pages/about.md.iwc.md"
            self.assertTrue(about_compiled_json.exists())
            self.assertTrue(about_compiled_md.exists())

            about_file.unlink()
            result = compile_node_context(config, source_paths=["views/pages/about.md"])
            self.assertEqual(result["compiled_count"], 0)
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(about_compiled_json.exists())
            self.assertFalse(about_compiled_md.exists())

    def test_compile_iwc_with_source_filter_keeps_other_artifacts(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            home_file = iwp_root / "views/pages/home.md"
            about_file = iwp_root / "views/pages/about.md"
            _write(home_file, "# Home\n\n## Layout Tree\n- Read Manifesto\n")
            _write(about_file, "# About\n\n## Layout Tree\n- About Content\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)
            about_compiled_json = root / ".iwp/compiled/json/views/pages/about.md.iwc.json"
            about_compiled_md = root / ".iwp/compiled/md/views/pages/about.md.iwc.md"
            before_json = about_compiled_json.read_text(encoding="utf-8")
            before_md = about_compiled_md.read_text(encoding="utf-8")

            _write(home_file, "# Home\n\n## Layout Tree\n- Read Protocol\n")
            result = compile_node_context(config, source_paths=["views/pages/home.md"])
            self.assertEqual(result["compiled_count"], 1)
            self.assertEqual(result["removed_count"], 0)
            self.assertEqual(about_compiled_json.read_text(encoding="utf-8"), before_json)
            self.assertEqual(about_compiled_md.read_text(encoding="utf-8"), before_md)

    def test_verify_iwc_detects_json_md_node_mismatch(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)
            md_file = root / ".iwp/compiled/md/views/pages/home.md.iwc.md"
            md_text = md_file.read_text(encoding="utf-8")
            md_file.write_text(md_text.replace("id=n.", "id=n.ff", 1), encoding="utf-8")

            verify_result = verify_compiled_context(config)
            self.assertFalse(verify_result["ok"])
            self.assertEqual(verify_result["invalid_files"], ["views/pages/home.md"])

    def test_verify_iwc_detects_missing_md_artifact(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                node_catalog_file=DEFAULT_NODE_CATALOG_FILE,
                compiled_dir=".iwp/compiled",
            )
            compile_node_context(config)
            md_file = root / ".iwp/compiled/md/views/pages/home.md.iwc.md"
            md_file.unlink()

            verify_result = verify_compiled_context(config)
            self.assertFalse(verify_result["ok"])
            self.assertEqual(verify_result["missing_files"], ["views/pages/home.md"])
            self.assertEqual(verify_result["missing_md_files"], ["views/pages/home.md"])

    def test_nodes_query_link_format_prints_single_annotation(self) -> None:
        payload = {
            "results": [
                {
                    "source_path": "views/pages/home.md",
                    "node_id": "n.abc123",
                }
            ]
        }
        with StringIO() as buf, redirect_stdout(buf):
            _print_nodes_result("query", payload, output_format="link")
            out = buf.getvalue().strip()
        self.assertEqual(out, "@iwp.link views/pages/home.md::n.abc123")

    def test_code_sidecar_replaces_pure_link_and_inserts_for_mixed_line(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            node = next(
                item
                for item in parse_markdown_nodes(
                    iwp_root=iwp_root,
                    critical_patterns=[],
                    schema_path=schema_path,
                    node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                )
                if item.anchor_text == "Read Manifesto"
            )
            source_link = f"views/pages/home.md::{node.node_id}"
            code_file = root / "_ir/src/iwp_links.ts"
            _write(
                code_file,
                "\n".join(
                    [
                        f"// @iwp.link {source_link}",
                        f"const marker = 1; // @iwp.link {source_link}",
                    ]
                )
                + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
            )

            first = build_code_sidecar_context(config)
            sidecar_file = root / ".iwp/compiled/code/_ir/src/iwp_links.ts"
            output_first = sidecar_file.read_text(encoding="utf-8")
            second = build_code_sidecar_context(config)
            self.assertEqual(first["unresolved_links"], 0)
            self.assertEqual(second["unresolved_links"], 0)
            self.assertEqual(first["files_written"], 1)
            output_second = sidecar_file.read_text(encoding="utf-8")
            self.assertIn("<<<IWP_NODE_CONTEXT source=views/pages/home.md", output_second)
            self.assertIn("Read Manifesto", output_second)
            self.assertFalse(output_second.startswith(f"// @iwp.link {source_link}\n"))
            self.assertIn(f"const marker = 1; // @iwp.link {source_link}", output_second)
            self.assertEqual(output_first, output_second)

    def test_code_sidecar_reports_unresolved_node_reference(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Read Manifesto\n",
            )
            _write(
                root / "_ir/src/iwp_links.ts",
                "// @iwp.link views/pages/home.md::n.missing\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
            )
            result = build_code_sidecar_context(config)
            self.assertEqual(result["links_found"], 1)
            self.assertEqual(result["resolved_links"], 0)
            self.assertEqual(result["unresolved_links"], 1)
            diagnostics = result.get("diagnostics", [])
            self.assertTrue(isinstance(diagnostics, list))
            if isinstance(diagnostics, list):
                self.assertTrue(diagnostics)
                first = diagnostics[0]
                self.assertTrue(isinstance(first, dict))
                if isinstance(first, dict):
                    self.assertEqual(first["code"], "IWP305")

    def test_filesystem_snapshot_diff_provider_detects_markdown_change(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            target_file = iwp_root / "views/pages/home.md"
            _write(target_file, "# Home\n\n## Layout Tree\n- Read Manifesto\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                diff_provider="filesystem_snapshot",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
            )

            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            files = collect_workspace_files(
                project_root=config.project_root,
                iwp_root=config.iwp_root,
                iwp_root_path=config.iwp_root_path,
                code_roots=config.code_roots,
                include_ext=config.include_ext,
                exclude_markdown_globs=config.schema_exclude_markdown_globs,
            )
            store.create_snapshot(files)

            _write(target_file, "# Home\n\n## Layout Tree\n- Read Protocol\n")
            diff = load_diff(
                config=config,
                base="unused",
                head="unused",
                cwd=config.project_root,
                strict=True,
            )
            self.assertIn("InstructWare.iw/views/pages/home.md", diff.changed_files)

    def test_diff_handles_top_level_markdown_path_and_impacted_nodes(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["file_type_schemas"][0]["path_patterns"] = ["**/*.md", "*.md"]
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            target = iwp_root / "architecture.md"
            _write(target, "# Architecture\n\n## Layout Tree\n- Alpha\n")

            parsed = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            node = next(
                item
                for item in parsed
                if item.source_path == "architecture.md" and item.anchor_text == "Alpha"
            )
            _write(
                root / "_ir/src/iwp_links.ts",
                f"// @iwp.link architecture.md::{node.node_id}\n",
            )

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
            )
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            files = collect_workspace_files(
                project_root=config.project_root,
                iwp_root=config.iwp_root,
                iwp_root_path=config.iwp_root_path,
                code_roots=config.code_roots,
                include_ext=config.include_ext,
                exclude_markdown_globs=config.schema_exclude_markdown_globs,
            )
            store.create_snapshot(files)

            _write(target, "# Architecture\n\n## Layout Tree\n- Beta\n")
            report = run_diff(config, None, None)
            self.assertGreaterEqual(report["summary"]["total_nodes_in_scope"], 1)
            codes = {item["code"] for item in report["diagnostics"]}
            self.assertNotIn("IWP103", codes)

    def test_diff_code_only_change_does_not_emit_source_not_found_noise(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["file_type_schemas"][0]["path_patterns"] = ["**/*.md", "*.md"]
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            target = iwp_root / "architecture.md"
            _write(target, "# Architecture\n\n## Layout Tree\n- Alpha\n")

            parsed = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
            )
            node = next(
                item
                for item in parsed
                if item.source_path == "architecture.md" and item.anchor_text == "Alpha"
            )
            link_file = root / "_ir/src/iwp_links.ts"
            _write(link_file, f"// @iwp.link architecture.md::{node.node_id}\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
            )
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            files = collect_workspace_files(
                project_root=config.project_root,
                iwp_root=config.iwp_root,
                iwp_root_path=config.iwp_root_path,
                code_roots=config.code_roots,
                include_ext=config.include_ext,
                exclude_markdown_globs=config.schema_exclude_markdown_globs,
            )
            store.create_snapshot(files)

            _write(link_file, f"// @iwp.link architecture.md::{node.node_id}\n// touch\n")
            report = run_diff(config, None, None)
            self.assertEqual(report["summary"]["total_nodes_in_scope"], 0)
            codes = {item["code"] for item in report["diagnostics"]}
            self.assertNotIn("IWP103", codes)
            self.assertFalse(
                any(
                    "Tiny-diff tested node count below minimum" in item["message"]
                    for item in report["diagnostics"]
                )
            )

    def test_iwp202_unknown_section_includes_allowed_sections_hint(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Output\n- Unknown section item\n",
            )
            diagnostics = validate_markdown_schema(
                iwp_root=iwp_root,
                schema_path=schema_path,
                mode="strict",
            ).diagnostics
            target = next(item for item in diagnostics if item.code == "IWP202")
            self.assertIn("file type views.pages", target.message)
            self.assertIn("Allowed:", target.message)
            self.assertIn("Layout Tree", target.message)

    def test_link_migration_suggestions_and_repair_summary_exist(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(
                iwp_root / "views/pages/home.md",
                "# Home\n\n## Layout Tree\n- Alpha\n- Beta\n",
            )
            _write(root / "_ir/src/view.ts", "// @iwp.link views/pages/home.md::n.deadbeef\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
            )
            from iwp_lint.core.engine import run_full

            report = run_full(config)
            suggestions = report.get("link_migration_suggestions", [])
            self.assertTrue(isinstance(suggestions, list))
            self.assertEqual(suggestions[0]["stale_node_id"], "n.deadbeef")
            self.assertTrue(suggestions[0]["candidates"])
            repair_summary = report.get("repair_summary", {})
            self.assertGreaterEqual(int(repair_summary.get("missing_total", 0)), 1)
            self.assertIn("next_actions", repair_summary)

    def test_critical_granularity_title_only_reduces_inherited_critical_nodes(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["file_type_schemas"] = [
                {
                    "id": "logic",
                    "path_patterns": ["logic/**/*.md", "logic/*.md"],
                    "sections": [
                        {"key": "trigger", "required": False},
                        {"key": "execution_flow", "required": False},
                    ],
                }
            ]
            schema["section_i18n"] = {
                "trigger": {"en": ["Trigger"]},
                "execution_flow": {"en": ["Execution Flow"]},
            }
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            _write(
                iwp_root / "logic/on_add.md",
                "# Add\n\n## Trigger\n- non critical bullet\n- another bullet\n",
            )
            base_kwargs = {
                "project_root": root.resolve(),
                "iwp_root": "InstructWare.iw",
                "schema_file": "schema.json",
                "tracking": {
                    "protocol": {"include_ext": [".ts"], "exclude_globs": []},
                    "snapshot": {"include_ext": [".ts"], "exclude_globs": []},
                },
                "code_roots": ["_ir/src"],
                "critical_node_patterns": ["trigger", "execution flow"],
            }
            from iwp_lint.core.engine import run_full

            all_report = run_full(LintConfig(**base_kwargs, critical_granularity="all"))
            title_only_report = run_full(
                LintConfig(**base_kwargs, critical_granularity="title_only")
            )
            all_critical = [item for item in all_report["diagnostics"] if item["code"] == "IWP108"]
            title_only_critical = [
                item for item in title_only_report["diagnostics"] if item["code"] == "IWP108"
            ]
            self.assertGreater(len(all_critical), len(title_only_critical))

    def test_diff_does_not_emit_false_iwp105_for_unchanged_links_in_changed_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema = _base_schema()
            schema["file_type_schemas"][0]["path_patterns"] = ["**/*.md", "*.md"]
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(schema))
            target = iwp_root / "architecture.md"
            _write(
                target,
                "# Architecture\n\n## Layout Tree\n- A\n- B\n- C\n\n## Interaction Hooks\n- D\n",
            )

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
            )
            link_lines = [f"// @iwp.link architecture.md::{node.node_id}" for node in nodes]
            _write(root / "_ir/src/iwp_links.ts", "\n".join(link_lines) + "\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
            )
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            files = collect_workspace_files(
                project_root=config.project_root,
                iwp_root=config.iwp_root,
                iwp_root_path=config.iwp_root_path,
                code_roots=config.code_roots,
                include_ext=config.include_ext,
                exclude_markdown_globs=config.schema_exclude_markdown_globs,
            )
            store.create_snapshot(files)

            _write(
                target,
                "# Architecture\n\n## Layout Tree\n- A\n- B\n- C\n\n\n## Interaction Hooks\n- D\n",
            )
            report = run_diff(config, None, None)
            iwp105 = [item for item in report["diagnostics"] if item["code"] == "IWP105"]
            self.assertEqual(iwp105, [])

    def test_diff_tiny_guardrail_degrades_node_tested_to_warning(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            target = iwp_root / "views/pages/home.md"
            _write(target, "# Home\n\n## Layout Tree\n- Alpha\n")

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
            )
            node_id = nodes[0].node_id
            _write(root / "_ir/src/view.ts", f"// @iwp.link views/pages/home.md::{node_id}\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=DEFAULT_NODE_REGISTRY_FILE,
                thresholds=LintThresholds(node_tested_min=100.0),
                thresholds_by_mode=ModeThresholds(
                    full=LintThresholds(node_tested_min=0.0),
                    diff=LintThresholds(node_tested_min=100.0),
                ),
                tiny_diff=TinyDiffConfig(
                    min_impacted_nodes=3,
                    node_tested_min_count=1,
                    degrade_to_warning=True,
                ),
            )
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            files = collect_workspace_files(
                project_root=config.project_root,
                iwp_root=config.iwp_root,
                iwp_root_path=config.iwp_root_path,
                code_roots=config.code_roots,
                include_ext=config.include_ext,
                exclude_markdown_globs=config.schema_exclude_markdown_globs,
            )
            store.create_snapshot(files)
            _write(target, "# Home\n\n## Layout Tree\n- Beta\n")
            report = run_diff(config, None, None)
            node_tested_diags = [
                item
                for item in report["diagnostics"]
                if item["code"] == "IWP109" and "NodeTested%" in item["message"]
            ]
            self.assertTrue(node_tested_diags)
            self.assertEqual(node_tested_diags[0]["severity"], "warning")
            self.assertIn("tiny-diff guardrail active", node_tested_diags[0]["message"])

    def test_build_collect_remediation_hints(self) -> None:
        hints = collect_remediation_hints(
            [
                {"code": "IWP105"},
                {"code": "IWP107"},
                {"code": "IWP109"},
            ]
        )
        self.assertTrue(any("links normalize" in item for item in hints))
        self.assertTrue(any("@iwp.link" in item for item in hints))
        self.assertTrue(any("tiny-diff" in item for item in hints))


if __name__ == "__main__":
    unittest.main()
