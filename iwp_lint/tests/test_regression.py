from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from iwp_build.cli import build_parser as build_build_parser
from iwp_lint.cli import _print_nodes_result
from iwp_lint.cli import build_parser as build_lint_parser
from iwp_lint.config import LintConfig, load_config, resolve_schema_source
from iwp_lint.core.engine import _compute_metrics, run_diff, run_schema
from iwp_lint.core.node_catalog import (
    build_node_catalog,
    compile_node_context,
    query_node_catalog,
    verify_compiled_context,
)
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.schema.schema_semantics import resolve_section_keys
from iwp_lint.schema.schema_validator import validate_markdown_schema
from iwp_lint.vcs.diff_resolver import load_diff
from iwp_lint.vcs.snapshot_store import SnapshotStore, collect_workspace_files
from iwp_lint.vcs.task_store import create_diff_task, list_tasks, update_task_status


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
        "section_i18n": {
            "layout_tree": {"en": ["Layout Tree"]},
            "interaction_hooks": {"en": ["Interaction Hooks"]},
            "dup_a": {"en": ["Shared"]},
            "dup_b": {"en": ["Shared"]},
        },
        "file_type_schemas": [
            {
                "id": "views.pages",
                "path_patterns": ["views/pages/**/*.md", "views/pages/*.md"],
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


class IwpLintRegressionTests(unittest.TestCase):
    def test_iwp_lint_cli_rejects_legacy_snapshot_and_tasks_commands(self) -> None:
        parser = build_lint_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["snapshot", "init"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["tasks", "list"])

    def test_iwp_build_cli_supports_build_verify_watch_only(self) -> None:
        parser = build_build_parser()
        parsed = parser.parse_args(["build"])
        self.assertEqual(parsed.command, "build")
        self.assertEqual(parsed.mode, "auto")
        self.assertIsNone(parsed.diff_json)
        parsed_with_diff = parser.parse_args(["build", "--diff-json", "out/iwp-diff.json"])
        self.assertEqual(parsed_with_diff.diff_json, "out/iwp-diff.json")
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--id", "123"])

    def test_tuple_key_metrics_count_distinct_source_path(self) -> None:
        metrics = _compute_metrics(
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
            _write(cfg_dir / ".iwp-lint.json", "{}")
            config = load_config(str(cfg_dir / ".iwp-lint.json"), cwd=root / "somewhere")
            self.assertEqual(config.project_root, cfg_dir.resolve())

    def test_config_project_root_can_be_relative_to_config_dir(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            cfg_dir = root / "ci"
            _write(
                cfg_dir / ".iwp-lint.json",
                json.dumps({"project_root": ".."}),
            )
            config = load_config(str(cfg_dir / ".iwp-lint.json"), cwd=root / "other")
            self.assertEqual(config.project_root, root.resolve())

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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
            self.assertGreaterEqual(query_result["returned"], 1)
            top = query_result["results"][0]
            self.assertEqual(top["anchor_text"], "Read Manifesto")
            self.assertIn("index_db_path", build_result)

    def test_node_catalog_query_requires_catalog_file(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            compile_result = compile_node_context(config)
            self.assertEqual(compile_result["compiled_count"], 1)
            self.assertNotIn("compiled_files", compile_result)
            compiled_json_file = root / ".iwp/compiled/json/views/pages/home.md.iwc.json"
            compiled_md_file = root / ".iwp/compiled/md/views/pages/home.md.iwc.md"
            payload = json.loads(compiled_json_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["artifact"], "iwc")
            self.assertIn("dict", payload)
            self.assertEqual(len(payload["nodes"]), 3)
            first_node = payload["nodes"][0]
            self.assertIsInstance(first_node, list)
            self.assertEqual(len(first_node), 10)
            self.assertIsInstance(first_node[9], str)
            md_text = compiled_md_file.read_text(encoding="utf-8")
            self.assertIn("<!-- @iwp.meta artifact=iwc_md -->", md_text)
            self.assertIn("<!-- @iwp.node id=", md_text)

            verify_result = verify_compiled_context(config)
            self.assertTrue(verify_result["ok"])
            self.assertEqual(verify_result["checked_sources"], 1)

    def test_verify_iwc_detects_invalid_v2_node_shape(self) -> None:
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
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
                include_ext=[".ts"],
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
                include_ext=[".ts"],
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
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_lines = [f"// @iwp.link architecture.md::{node.node_id}" for node in nodes]
            _write(root / "_ir/src/iwp_links.ts", "\n".join(link_lines) + "\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
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

    def test_diff_task_lifecycle(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            task = create_diff_task(
                task_dir=root / ".iwp/tasks",
                changed_files={"a.py", "InstructWare.iw/views/pages/home.md"},
                changed_md_files={"views/pages/home.md"},
                changed_code_files={"a.py"},
                impacted_nodes=[{"source_path": "views/pages/home.md", "node_id": "n.1"}],
                notes="init",
            )
            self.assertEqual(task.status, "pending")
            tasks = list_tasks(root / ".iwp/tasks")
            self.assertEqual(len(tasks), 1)
            done = update_task_status(root / ".iwp/tasks", task.task_id, "done", notes="ok")
            self.assertEqual(done.status, "done")


if __name__ == "__main__":
    unittest.main()
