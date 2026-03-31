from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from iwp_build.cli import main
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


class IwpBuildHistoryCliTests(unittest.TestCase):
    def test_history_list_restore_and_prune(self) -> None:
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
                        "tracking:",
                        "  protocol:",
                        "    include_ext:",
                        "      - .ts",
                        "    exclude_globs: []",
                        "  snapshot:",
                        "    include_ext:",
                        "      - .ts",
                        "    exclude_globs: []",
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
            checkpoint_exit = main(
                [
                    "history",
                    "checkpoint",
                    "--config",
                    str(config_path),
                    "--message",
                    "fast loop savepoint",
                    "--json",
                    str(out_dir / "history.checkpoint.json"),
                ]
            )
            self.assertEqual(checkpoint_exit, 0)
            checkpoint_payload = json.loads(
                (out_dir / "history.checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint_payload["status"], "ok")
            self.assertIsInstance(checkpoint_payload["checkpoint_id"], int)
            self.assertEqual(main(["session", "start", "--config", str(config_path)]), 0)
            self.assertEqual(
                main(
                    [
                        "session",
                        "commit",
                        "--config",
                        str(config_path),
                        "--allow-stale-sidecar",
                        "--message",
                        "init baseline alpha",
                    ]
                ),
                0,
            )
            _write(md_file, "# Architecture\n\n## Layout Tree\n- Alpha\n- Beta\n")
            updated_nodes = parse_markdown_nodes(
                iwp_root=iwp_root,
                critical_patterns=[],
                schema_path=schema_path,
                node_registry_file=".iwp/node_registry.v1.json",
            )
            _write(
                link_file,
                "\n".join(f"// @iwp.link architecture.md::{item.node_id}" for item in updated_nodes)
                + "\n",
            )
            self.assertEqual(main(["session", "start", "--config", str(config_path)]), 0)
            self.assertEqual(
                main(
                    [
                        "session",
                        "commit",
                        "--config",
                        str(config_path),
                        "--allow-stale-sidecar",
                        "--message",
                        "add beta node",
                    ]
                ),
                0,
            )
            list_exit = main(
                [
                    "history",
                    "list",
                    "--config",
                    str(config_path),
                    "--json",
                    str(out_dir / "history.list.json"),
                ]
            )
            self.assertEqual(list_exit, 0)
            listed = json.loads((out_dir / "history.list.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(listed["checkpoints"]), 2)
            self.assertTrue(
                any(
                    str(item.get("source", "")) == "history_checkpoint"
                    for item in listed["checkpoints"]
                )
            )
            history_checkpoint_rows = [
                item
                for item in listed["checkpoints"]
                if str(item.get("source", "")) == "history_checkpoint"
            ]
            self.assertTrue(history_checkpoint_rows)
            self.assertIsInstance(history_checkpoint_rows[0].get("git_commit_oid"), str)
            self.assertTrue(str(history_checkpoint_rows[0]["git_commit_oid"]))
            self.assertEqual(str(listed["checkpoints"][0]["message"]), "add beta node")
            message_index = {
                str(item.get("message", "")): int(item["checkpoint_id"])
                for item in listed["checkpoints"]
            }
            self.assertIn("init baseline alpha", message_index)
            target_checkpoint_id = message_index["init baseline alpha"]

            dry_run_exit = main(
                [
                    "history",
                    "restore",
                    "--config",
                    str(config_path),
                    "--to",
                    str(target_checkpoint_id),
                    "--dry-run",
                    "--json",
                    str(out_dir / "history.restore.dryrun.json"),
                ]
            )
            self.assertEqual(dry_run_exit, 0)
            dry_run_payload = json.loads(
                (out_dir / "history.restore.dryrun.json").read_text(encoding="utf-8")
            )
            self.assertEqual(dry_run_payload["status"], "dry_run")

            restore_exit = main(
                [
                    "history",
                    "restore",
                    "--config",
                    str(config_path),
                    "--to",
                    str(target_checkpoint_id),
                    "--force",
                    "--json",
                    str(out_dir / "history.restore.apply.json"),
                ]
            )
            self.assertEqual(restore_exit, 0)
            restored_payload = json.loads(
                (out_dir / "history.restore.apply.json").read_text(encoding="utf-8")
            )
            self.assertEqual(restored_payload["status"], "applied")

            prune_exit = main(
                [
                    "history",
                    "prune",
                    "--config",
                    str(config_path),
                    "--max-snapshots",
                    "1",
                    "--json",
                    str(out_dir / "history.prune.json"),
                ]
            )
            self.assertEqual(prune_exit, 0)
            prune_payload = json.loads((out_dir / "history.prune.json").read_text(encoding="utf-8"))
            self.assertIn("removed_checkpoint_ids", prune_payload)
            self.assertIn("kept_checkpoint_ids", prune_payload)


if __name__ == "__main__":
    unittest.main()
