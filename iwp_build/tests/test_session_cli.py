from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from iwp_build.cli import main
from iwp_lint.config import load_config
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
        "section_i18n": {"layout_tree": {"en": ["Layout Tree"]}},
        "file_type_schemas": [
            {
                "id": "docs",
                "path_patterns": ["**/*.md", "*.md"],
                "sections": [{"key": "layout_tree"}],
            }
        ],
    }


def _workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = Path.cwd() / ".tmp_iwp_build_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix=f"{uuid.uuid4().hex}_")


class IwpBuildSessionCliTests(unittest.TestCase):
    def test_session_reconcile_missing_session_returns_friendly_error(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            config_path = root / ".iwp-lint.yaml"
            md_file = iwp_root / "architecture.md"
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(
                config_path,
                "\n".join(
                    [
                        "iwp_root: InstructWare.iw",
                        "code_roots:",
                        "  - _ir/src",
                        "include_ext:",
                        "  - .ts",
                        "schema:",
                        "  file: schema.json",
                    ]
                )
                + "\n",
            )
            out = StringIO()
            with redirect_stdout(out):
                exit_code = main(
                    [
                        "session",
                        "reconcile",
                        "--config",
                        str(config_path),
                    ]
                )
            self.assertEqual(exit_code, 1)
            text = out.getvalue()
            self.assertIn("[iwp-build] error:", text)
            self.assertIn("no open session", text)
            self.assertNotIn("Traceback", text)

    def test_session_start_if_missing_reuses_open_session(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            config_path = root / ".iwp-lint.yaml"
            out_dir = root / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            _write(schema_path, json.dumps(_base_schema()))
            _write(iwp_root / "architecture.md", "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(
                config_path,
                "\n".join(
                    [
                        "iwp_root: InstructWare.iw",
                        "code_roots:",
                        "  - _ir/src",
                        "include_ext:",
                        "  - .ts",
                        "schema:",
                        "  file: schema.json",
                        "execution_presets:",
                        "  agent-default:",
                        "    session_start:",
                        "      if_missing: true",
                    ]
                )
                + "\n",
            )
            first_exit = main(
                [
                    "session",
                    "start",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "session.start.1.json"),
                ]
            )
            self.assertEqual(first_exit, 0)
            first_payload = json.loads(
                (out_dir / "session.start.1.json").read_text(encoding="utf-8")
            )
            second_exit = main(
                [
                    "session",
                    "start",
                    "--config",
                    str(config_path),
                    "--preset",
                    "agent-default",
                    "--json",
                    str(out_dir / "session.start.2.json"),
                ]
            )
            self.assertEqual(second_exit, 0)
            second_payload = json.loads(
                (out_dir / "session.start.2.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_payload["session_id"], first_payload["session_id"])
            self.assertTrue(bool(second_payload.get("reused_current")))

    def test_session_reconcile_auto_build_sidecar_recovers_stale_sidecar(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            config_path = root / ".iwp-lint.yaml"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            out_dir = root / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(
                config_path,
                "\n".join(
                    [
                        "iwp_root: InstructWare.iw",
                        "code_roots:",
                        "  - _ir/src",
                        "include_ext:",
                        "  - .ts",
                        "schema:",
                        "  file: schema.json",
                    ]
                )
                + "\n",
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
            self.assertEqual(main(["build", "--config", str(config_path)]), 0)
            self.assertEqual(main(["session", "start", "--config", str(config_path)]), 0)
            _write(link_file, link_file.read_text(encoding="utf-8") + "// changed\n")
            reconcile_exit = main(
                [
                    "session",
                    "reconcile",
                    "--config",
                    str(config_path),
                    "--auto-build-sidecar",
                    "--json",
                    str(out_dir / "session.reconcile.auto-sidecar.json"),
                ]
            )
            self.assertEqual(reconcile_exit, 0)
            reconcile_payload = json.loads(
                (out_dir / "session.reconcile.auto-sidecar.json").read_text(encoding="utf-8")
            )
            self.assertTrue(bool(reconcile_payload.get("can_commit")))
            self.assertTrue(bool(reconcile_payload.get("sidecar_fresh")))
            self.assertTrue(bool(reconcile_payload.get("auto_recovered")))
            refresh = reconcile_payload.get("sidecar_refresh", {})
            self.assertTrue(isinstance(refresh, dict) and bool(refresh.get("triggered")))
            self.assertTrue(bool(refresh.get("recovered")))
            self.assertEqual(reconcile_payload.get("recommended_next_command"), None)
            self.assertEqual(reconcile_payload.get("recommended_next_chain"), [])

    def test_session_normalize_links_command_entry(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            config_path = root / ".iwp-lint.yaml"
            link_file = root / "_ir/src/iwp_links.ts"
            out_dir = root / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            _write(schema_path, json.dumps(_base_schema()))
            _write(iwp_root / "architecture.md", "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(
                config_path,
                "\n".join(
                    [
                        "iwp_root: InstructWare.iw",
                        "code_roots:",
                        "  - _ir/src",
                        "include_ext:",
                        "  - .ts",
                        "schema:",
                        "  file: schema.json",
                    ]
                )
                + "\n",
            )
            nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            node_id = nodes[0].node_id
            _write(
                link_file,
                "\n".join(
                    [
                        f"// @iwp.link architecture.md::{node_id}",
                        f"// @iwp.link architecture.md::{node_id}",
                    ]
                )
                + "\n",
            )
            normalize_exit = main(
                [
                    "session",
                    "normalize-links",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "session.normalize-links.json"),
                ]
            )
            self.assertEqual(normalize_exit, 0)
            normalize_payload = json.loads(
                (out_dir / "session.normalize-links.json").read_text(encoding="utf-8")
            )
            self.assertEqual(normalize_payload["status"], "ok")
            self.assertIn("normalize", normalize_payload)
            self.assertGreaterEqual(int(normalize_payload["normalize"]["changed_count"]), 1)

    def test_session_commands_and_build_bridge(self) -> None:
        with _workspace_tmpdir() as td:
            root = Path(td)
            iwp_root = root / "InstructWare.iw"
            schema_path = root / "schema.json"
            config_path = root / ".iwp-lint.yaml"
            md_file = iwp_root / "architecture.md"
            link_file = root / "_ir/src/iwp_links.ts"
            out_dir = root / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            _write(schema_path, json.dumps(_base_schema()))
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n")
            _write(
                config_path,
                "\n".join(
                    [
                        "iwp_root: InstructWare.iw",
                        "code_roots:",
                        "  - _ir/src",
                        "include_ext:",
                        "  - .ts",
                        "schema:",
                        "  file: schema.json",
                    ]
                )
                + "\n",
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
            build_seed_exit = main(
                [
                    "build",
                    "--config",
                    str(config_path),
                ]
            )
            self.assertEqual(build_seed_exit, 0)

            start_exit = main(
                [
                    "session",
                    "start",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "session.start.json"),
                ]
            )
            self.assertEqual(start_exit, 0)
            started = json.loads((out_dir / "session.start.json").read_text(encoding="utf-8"))
            started_id = str(started["session_id"])

            current_exit = main(
                [
                    "session",
                    "current",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "session.current.json"),
                ]
            )
            self.assertEqual(current_exit, 0)
            current_payload = json.loads(
                (out_dir / "session.current.json").read_text(encoding="utf-8")
            )
            self.assertTrue(current_payload["has_open_session"])
            self.assertEqual(current_payload["session"]["session_id"], started_id)

            diff_stdout = StringIO()
            with redirect_stdout(diff_stdout):
                diff_exit = main(
                    [
                        "session",
                        "diff",
                        "--config",
                        str(config_path),
                        "--include-baseline-gaps",
                        "--focus-path",
                        "architecture.md",
                        "--max-gap-items",
                        "3",
                        "--format",
                        "both",
                        "--json",
                        str(out_dir / "session.diff.json"),
                    ]
                )
            self.assertEqual(diff_exit, 0)
            self.assertIn("changed_code_summary:", diff_stdout.getvalue())
            diff_payload = json.loads((out_dir / "session.diff.json").read_text(encoding="utf-8"))
            self.assertEqual(diff_payload["session_id"], started_id)
            self.assertEqual(diff_payload["meta"]["mode"], "diagnostic")
            self.assertIn("changed_code_details", diff_payload)
            self.assertIn("filters_applied", diff_payload)
            self.assertIn("markdown_change_blocks", diff_payload)
            self.assertIn("markdown_change_text", diff_payload)
            self.assertTrue(diff_payload["markdown_change_text"])
            self.assertIn("baseline_gap_summary", diff_payload)

            _write(link_file, link_file.read_text(encoding="utf-8") + "// changed\n")
            diff_hunk_exit = main(
                [
                    "session",
                    "diff",
                    "--config",
                    str(config_path),
                    "--code-diff-level",
                    "hunk",
                    "--code-diff-context-lines",
                    "2",
                    "--code-diff-max-chars",
                    "5000",
                    "--json",
                    str(out_dir / "session.diff.hunk.json"),
                ]
            )
            self.assertEqual(diff_hunk_exit, 0)
            diff_hunk_payload = json.loads(
                (out_dir / "session.diff.hunk.json").read_text(encoding="utf-8")
            )
            self.assertEqual(diff_hunk_payload["code_diff_level"], "hunk")
            self.assertTrue(diff_hunk_payload["changed_code_details"])
            self.assertIn("hunks", diff_hunk_payload["changed_code_details"][0])
            reconcile_stdout = StringIO()
            with redirect_stdout(reconcile_stdout):
                reconcile_exit = main(
                    [
                        "session",
                        "reconcile",
                        "--config",
                        str(config_path),
                        "--format",
                        "both",
                        "--debug-raw",
                        "--node-severity",
                        "all",
                        "--max-diagnostics",
                        "5",
                        "--suggest-fixes",
                        "--json",
                        str(out_dir / "session.reconcile.json"),
                    ]
                )
            self.assertIn(reconcile_exit, {0, 1})
            reconcile_output = reconcile_stdout.getvalue()
            reconcile_payload = json.loads(
                (out_dir / "session.reconcile.json").read_text(encoding="utf-8")
            )
            self.assertIn("<<<IWP_RECONCILE_V1>>>", reconcile_output)
            self.assertIn("diff_summary:", reconcile_output)
            if reconcile_payload.get("diagnostics_top"):
                self.assertIn("diagnostics_top:", reconcile_output)
            else:
                self.assertNotIn("diagnostics_top:", reconcile_output)
            if reconcile_payload.get("next_actions"):
                self.assertIn("next_actions:", reconcile_output)
            else:
                self.assertNotIn("next_actions:", reconcile_output)
            self.assertIn("can_commit", reconcile_payload)
            self.assertEqual(reconcile_payload["meta"]["mode"], "decision")
            self.assertIn("compiled_ok", reconcile_payload)
            self.assertIn("compiled_checked_at", reconcile_payload)
            self.assertIn("warning_count", reconcile_payload)
            self.assertIn("top_warnings", reconcile_payload)
            self.assertIn("compiled_ok", reconcile_payload["summary"])
            self.assertIn("compiled_checked_at", reconcile_payload["summary"])
            self.assertIn("warning_count", reconcile_payload["summary"])
            self.assertIn("filters_applied", reconcile_payload)
            self.assertIn("next_actions", reconcile_payload)
            self.assertIn("next_command_examples", reconcile_payload)
            self.assertIn("recommended_next_command", reconcile_payload)
            self.assertIn("recommended_next_chain", reconcile_payload)
            self.assertIn("hints", reconcile_payload)
            self.assertIn("diagnostics_top", reconcile_payload)
            self.assertIn("code_path_hints", reconcile_payload)
            self.assertIn("blocking_pairs_topn", reconcile_payload)
            self.assertIn("suggested_code_paths", reconcile_payload)
            self.assertIn("raw", reconcile_payload)
            if "code_sidecar" in reconcile_payload.get("blocking_reasons", []):
                self.assertTrue(reconcile_payload.get("next_actions"))
                self.assertEqual(reconcile_payload["next_actions"][0]["kind"], "refresh_sidecar")
                self.assertTrue(bool(reconcile_payload.get("recommended_next_command")))
                self.assertTrue(bool(reconcile_payload.get("recommended_next_chain")))
            if bool(reconcile_payload["can_commit"]):
                self.assertEqual(reconcile_payload["blocking_reasons"], [])
                self.assertEqual(reconcile_payload["blocking_pairs_topn"], [])
                self.assertEqual(reconcile_payload["next_actions"], [])
                self.assertEqual(reconcile_payload["next_command_examples"], [])
                self.assertEqual(reconcile_payload["recommended_next_command"], None)
                self.assertEqual(reconcile_payload["recommended_next_chain"], [])
            commit_blocked_exit = main(
                [
                    "session",
                    "commit",
                    "--config",
                    str(config_path),
                    "--evidence-json",
                    str(out_dir / "session.evidence.json"),
                    "--json",
                    str(out_dir / "session.commit.json"),
                ]
            )
            self.assertEqual(commit_blocked_exit, 1)
            blocked_payload = json.loads(
                (out_dir / "session.commit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(blocked_payload["status"], "blocked")
            self.assertFalse(bool(blocked_payload["sidecar_fresh"]))
            self.assertIn("code_sidecar", blocked_payload.get("blocked_by", []))

            commit_exit = main(
                [
                    "session",
                    "commit",
                    "--config",
                    str(config_path),
                    "--allow-stale-sidecar",
                    "--evidence-json",
                    str(out_dir / "session.evidence.json"),
                    "--json",
                    str(out_dir / "session.commit.json"),
                ]
            )
            self.assertEqual(commit_exit, 0)
            commit_payload = json.loads(
                (out_dir / "session.commit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(commit_payload["session_id"], started_id)
            self.assertIn("sidecar_fresh", commit_payload)
            self.assertIn("compiled_at", commit_payload)
            self.assertIn("compiled_from_baseline_id", commit_payload)
            config = load_config(str(config_path))
            store = SnapshotStore((config.project_root / config.snapshot_db_file).resolve())
            baseline_after_commit = store.latest_snapshot_id()
            self.assertIsNotNone(baseline_after_commit)
            evidence_payload = json.loads(
                (out_dir / "session.evidence.json").read_text(encoding="utf-8")
            )
            self.assertIn("intent_diff", evidence_payload)

            build_exit = main(
                [
                    "build",
                    "--config",
                    str(config_path),
                    "--mode",
                    "diff",
                    "--json",
                    str(out_dir / "build.readonly.json"),
                ]
            )
            self.assertEqual(build_exit, 0)
            baseline_after_build = store.latest_snapshot_id()
            self.assertEqual(baseline_after_build, baseline_after_commit)
            current_after_build_exit = main(
                [
                    "session",
                    "current",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "session.current.after-build.json"),
                ]
            )
            self.assertEqual(current_after_build_exit, 0)
            current_after_build = json.loads(
                (out_dir / "session.current.after-build.json").read_text(encoding="utf-8")
            )
            self.assertFalse(current_after_build["has_open_session"])
            verify_stdout = StringIO()
            with redirect_stdout(verify_stdout):
                verify_exit = main(
                    [
                        "verify",
                        "--config",
                        str(config_path),
                        "--protocol-only",
                    ]
                )
            self.assertEqual(verify_exit, 0)
            self.assertIn(
                "verify gates protocol=PASS tests=SKIPPED overall=PASS",
                verify_stdout.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
