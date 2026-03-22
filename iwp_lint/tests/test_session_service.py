from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from iwp_lint.config import CoverageProfile, LintConfig, SessionConfig
from iwp_lint.core.session_service import SessionService
from iwp_lint.parsers.md_parser import parse_markdown_nodes


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
    tmp_root = Path.cwd() / ".tmp_iwp_lint_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


class SessionServiceTests(unittest.TestCase):
    def test_session_start_rejects_parallel_active_session(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(link_file, "export const links = [];\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                session=SessionConfig(link_density_threshold=2.0),
            )
            service = SessionService(config)
            first = service.start()
            self.assertEqual(first["status"], "open")
            with self.assertRaisesRegex(RuntimeError, "open session already exists"):
                service.start()

    def test_session_diff_and_commit_flow(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                session=SessionConfig(link_density_threshold=2.0),
            )

            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )

            service = SessionService(config)
            started = service.start()
            self.assertEqual(started["status"], "open")
            self.assertIsNone(started["baseline_id_before"])
            session_id_1 = str(started["session_id"])

            diff_payload = service.diff(session_id_1)
            self.assertEqual(diff_payload["meta"]["mode"], "diagnostic")
            self.assertGreaterEqual(diff_payload["changed_count"], 1)
            self.assertIn("architecture.md", diff_payload["changed_md_files"])
            self.assertTrue(diff_payload["impacted_nodes"])
            self.assertEqual(diff_payload["link_density_signals"], [])

            committed = service.commit(session_id_1, allow_stale_sidecar=True)
            self.assertEqual(committed["status"], "committed")
            self.assertIsNotNone(committed["baseline_id_after"])

            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            started_2 = service.start()
            session_id_2 = str(started_2["session_id"])
            self.assertEqual(started_2["baseline_id_before"], committed["baseline_id_after"])
            diff_2 = service.diff(session_id_2)
            self.assertIn("architecture.md", diff_2["changed_md_files"])
            self.assertTrue(diff_2["link_targets_suggested"])
            commit_2 = service.commit(session_id_2, allow_stale_sidecar=True)
            self.assertEqual(commit_2["status"], "blocked")
            self.assertEqual(commit_2["gate_status"], "FAIL")

            audit = service.audit(session_id_2)
            self.assertEqual(audit["session"]["status"], "blocked")
            self.assertGreaterEqual(len(audit["events"]), 2)

    def test_session_diff_code_details_summary_and_hunk(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                session=SessionConfig(link_density_threshold=2.0),
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            service = SessionService(config)
            session_1 = service.start()
            service.commit(
                str(session_1["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )

            _write(link_file, link_file.read_text(encoding="utf-8") + "// touched\n")
            session_2 = service.start()
            session_id = str(session_2["session_id"])

            diff_summary = service.diff(session_id)
            self.assertEqual(diff_summary["code_diff_level"], "summary")
            details_summary = diff_summary["changed_code_details"]
            self.assertTrue(details_summary)
            self.assertEqual(details_summary[0]["file_path"], "_ir/src/iwp_links.ts")
            self.assertIn(details_summary[0]["change_kind"], {"modified", "added", "deleted"})
            self.assertTrue(details_summary[0]["changed_line_ranges"])
            self.assertNotIn("hunks", details_summary[0])

            diff_hunk = service.diff(
                session_id,
                code_diff_level="hunk",
                code_diff_context_lines=2,
                code_diff_max_chars=10000,
            )
            self.assertEqual(diff_hunk["code_diff_level"], "hunk")
            details_hunk = diff_hunk["changed_code_details"]
            self.assertTrue(details_hunk)
            self.assertIn("hunks", details_hunk[0])

    def test_session_diff_supports_node_filters_and_excerpt_truncation(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha Long Long Line\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                coverage_profiles=[
                    CoverageProfile(
                        name="docs_warn",
                        file_type_ids=["docs"],
                        missing_severity="warning",
                    )
                ],
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )
            service = SessionService(config)
            session_1 = service.start()
            service.commit(
                str(session_1["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )

            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha Long Long Line Updated\n")
            session_2 = service.start()
            session_id = str(session_2["session_id"])
            payload = service.diff(
                session_id,
                node_severity="warning",
                markdown_excerpt_max_chars=20,
            )
            self.assertEqual(payload["meta"]["mode"], "diagnostic")
            self.assertTrue(payload["impacted_nodes"])
            self.assertEqual(payload["filters_applied"]["node_severity"], "warning")
            self.assertIn("block_text_excerpt", payload["impacted_nodes"][0])
            excerpt = str(payload["impacted_nodes"][0]["block_text_excerpt"])
            self.assertLessEqual(len(excerpt), 20)
            self.assertIn("markdown_change_blocks", payload)
            self.assertIn("markdown_change_text", payload)
            self.assertNotIn("impacted_nodes_all", payload)
            self.assertNotIn("impacted_nodes_filtered", payload)

    def test_session_diff_can_include_baseline_gap_summary(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir/src"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                session=SessionConfig(link_density_threshold=2.0, baseline_gap_max_items=5),
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in nodes) + "\n",
            )

            service = SessionService(config)
            session_1 = service.start()
            service.commit(
                str(session_1["session_id"]),
                enforce_gate=False,
                allow_stale_sidecar=True,
            )
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            session_2 = service.start()
            payload = service.diff(
                str(session_2["session_id"]),
                include_baseline_gaps=True,
                focus_path="architecture.md",
                max_gap_items=3,
            )
            self.assertIn("baseline_gap_summary", payload)
            summary = payload["baseline_gap_summary"]
            self.assertEqual(summary["scope"]["focus_path"], "architecture.md")
            self.assertLessEqual(len(summary["top_uncovered_pairs"]), 3)

    def test_session_diff_excludes_default_cache_directories(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            md_file = iwp_root / "architecture.md"
            app_file = root / "_ir/src/app.ts"
            node_modules_file = root / "_ir/node_modules/lib/index.ts"
            dist_file = root / "_ir/dist/bundle.ts"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(app_file, "export const app = 1;\n")
            _write(node_modules_file, "export const dep = 1;\n")
            _write(dist_file, "export const out = 1;\n")
            config = LintConfig(
                project_root=root.resolve(),
                iwp_root="InstructWare.iw",
                schema_file="schema.json",
                snapshot_db_file=".iwp/cache/snapshots.sqlite",
                include_ext=[".ts"],
                code_roots=["_ir"],
                node_registry_file=".iwp/node_registry.v1.json",
                node_catalog_file=".iwp/node_catalog.v1.json",
                compiled_dir=".iwp/compiled",
                session=SessionConfig(link_density_threshold=2.0),
            )
            service = SessionService(config)
            started = service.start()
            diff_payload = service.diff(str(started["session_id"]))
            changed_code = diff_payload.get("changed_code_files", [])
            self.assertIn("_ir/src/app.ts", changed_code)
            self.assertNotIn("_ir/node_modules/lib/index.ts", changed_code)
            self.assertNotIn("_ir/dist/bundle.ts", changed_code)


if __name__ == "__main__":
    unittest.main()
