from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from iwp_build.cli import _run_build, _run_verify
from iwp_lint.api import compile_context, verify_code_sidecar_freshness
from iwp_lint.config import LintConfig, PageOnlyConfig, TrackingConfig, TrackingScopeConfig
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.vcs.snapshot_store import SnapshotStore


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
        },
        "file_type_schemas": [
            {
                "id": "docs",
                "path_patterns": ["**/*.md", "*.md"],
                "sections": [{"key": "layout_tree", "required": True}],
            }
        ],
    }


def _workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = Path.cwd() / ".tmp_iwp_build_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


def _tracking_ts() -> TrackingConfig:
    return TrackingConfig(
        protocol=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
        snapshot=TrackingScopeConfig(include_ext=[".ts"], exclude_globs=[]),
    )


class IwpBuildCommitTests(unittest.TestCase):
    def test_build_json_exposes_page_only_mode_flag(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            _write(iwp_root / "architecture.md", "# Architecture\n\n## Layout Tree\n- Alpha\n")
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                root / "_ir/src/iwp_links.ts",
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                page_only=PageOnlyConfig(enabled=True),
            )
            payload_path = root / "out/build.json"
            self.assertEqual(_run_build(config, mode="auto", json_path=payload_path.as_posix()), 0)
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(payload.get("mode_flags", {}).get("page_only_enabled")))
            self.assertTrue(bool(payload.get("summary", {}).get("page_only_enabled")))

    def test_build_emits_code_sidecar_by_default(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            md_file = iwp_root / "architecture.md"
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_file = root / "_ir/src/iwp_links.ts"
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )

            exit_code = _run_build(config, mode="auto", json_path=None)
            self.assertEqual(exit_code, 0)
            sidecar_file = root / ".iwp/compiled/code/_ir/src/iwp_links.ts"
            sidecar_manifest = root / ".iwp/compiled/code/.iwp_sidecar_meta.v1.json"
            self.assertTrue(sidecar_file.exists())
            self.assertTrue(sidecar_manifest.exists())
            sidecar_text = sidecar_file.read_text(encoding="utf-8")
            self.assertIn("<<<IWP_NODE_CONTEXT source=architecture.md", sidecar_text)

    def test_sidecar_freshness_survives_catalog_regeneration(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            md_file = iwp_root / "architecture.md"
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_file = root / "_ir/src/iwp_links.ts"
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )

            self.assertEqual(_run_build(config, mode="auto", json_path=None), 0)
            first = verify_code_sidecar_freshness(config)
            self.assertTrue(bool(first.get("fresh", False)))

            # Simulate reconcile/gate path that recompiles catalog artifacts.
            compile_context(config)
            second = verify_code_sidecar_freshness(config)
            self.assertTrue(bool(second.get("fresh", False)))
            self.assertEqual(second.get("stale_reasons", []), [])

    def test_build_never_advances_baseline_and_keeps_failures_readonly(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            md_file = iwp_root / "architecture.md"
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_file = root / "_ir/src/iwp_links.ts"
            initial_links = [f"// @iwp.link architecture.md::{node.node_id}" for node in nodes]
            _write(link_file, "\n".join(initial_links) + "\n")

            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )

            first_exit = _run_build(config, mode="auto", json_path=None)
            self.assertEqual(first_exit, 0)
            store = SnapshotStore((root / config.snapshot_db_file).resolve())
            baseline_before = store.latest_snapshot_id()
            self.assertIsNone(baseline_before)

            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            second_exit = _run_build(config, mode="diff", json_path=None)
            self.assertEqual(second_exit, 1)

            baseline_after_failure = store.latest_snapshot_id()
            self.assertEqual(baseline_after_failure, baseline_before)

            new_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{node.node_id}" for node in new_nodes)
                + "\n",
            )

            third_exit = _run_build(config, mode="diff", json_path=None)
            self.assertEqual(third_exit, 0)
            baseline_after_success = store.latest_snapshot_id()
            self.assertEqual(baseline_after_success, baseline_before)

    def test_verify_protocol_only_gate_passes_without_tests(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            _write(schema_path, json.dumps(_base_schema()))
            md_file = iwp_root / "architecture.md"
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            link_file = root / "_ir/src/iwp_links.ts"
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                tracking=_tracking_ts(),
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
            )
            self.assertEqual(_run_build(config, mode="auto", json_path=None), 0)
            verify_exit = _run_verify(
                config,
                with_tests=False,
                protocol_only=True,
                min_severity="warning",
                quiet_warnings=False,
            )
            self.assertEqual(verify_exit, 0)


if __name__ == "__main__":
    unittest.main()
