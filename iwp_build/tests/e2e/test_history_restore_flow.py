from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    write_architecture_markdown,
    write_links_for_source,
)


class HistoryRestoreFlowBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def _assert_fail(self, result, label: str) -> None:
        self.assertNotEqual(
            result.returncode,
            0,
            msg=f"{label} unexpectedly passed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_history_restore_supports_backward_and_forward_jump(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start #1 ({profile})",
                )
                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--allow-stale-sidecar",
                        ]
                    ),
                    f"session commit #1 ({profile})",
                )

                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start #2 ({profile})",
                )
                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--allow-stale-sidecar",
                        ]
                    ),
                    f"session commit #2 ({profile})",
                )

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "list",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "history.list.json"),
                        ]
                    ),
                    f"history list ({profile})",
                )
                listed = read_json(out_dir / "history.list.json")
                checkpoints = listed["checkpoints"]
                self.assertGreaterEqual(len(checkpoints), 2)
                latest_checkpoint_id = int(checkpoints[0]["checkpoint_id"])
                oldest_checkpoint_id = int(checkpoints[-1]["checkpoint_id"])

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(oldest_checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.oldest.json"),
                        ]
                    ),
                    f"history restore oldest ({profile})",
                )
                restore_oldest = read_json(out_dir / "history.restore.oldest.json")
                self.assertEqual(restore_oldest["status"], "applied")
                self.assertIn("next_required_actions", restore_oldest)
                markdown_after_oldest = (workspace / "InstructWare.iw/architecture.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Alpha", markdown_after_oldest)
                self.assertNotIn("Beta", markdown_after_oldest)

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(latest_checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.latest.json"),
                        ]
                    ),
                    f"history restore latest ({profile})",
                )
                markdown_after_latest = (workspace / "InstructWare.iw/architecture.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Beta", markdown_after_latest)

    def test_history_restore_blocks_dirty_workspace_without_force(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start ({profile})",
                )
                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--allow-stale-sidecar",
                            "--json",
                            str(out_dir / "session.commit.json"),
                        ]
                    ),
                    f"session commit ({profile})",
                )
                checkpoint_id = int(read_json(out_dir / "session.commit.json")["checkpoint_id"])

                write_architecture_markdown(workspace, profile, ["Dirty"])
                blocked = run_build(
                    [
                        "history",
                        "restore",
                        "--config",
                        str(config_path),
                        "--to",
                        str(checkpoint_id),
                        "--json",
                        str(out_dir / "history.restore.blocked.json"),
                    ]
                )
                self._assert_fail(blocked, f"history restore blocked ({profile})")
                blocked_payload = read_json(out_dir / "history.restore.blocked.json")
                self.assertEqual(blocked_payload["status"], "blocked")
                self.assertEqual(blocked_payload["blocked_reason"], "dirty_workspace")

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.force.json"),
                        ]
                    ),
                    f"history restore force ({profile})",
                )
                forced_payload = read_json(out_dir / "history.restore.force.json")
                self.assertEqual(forced_payload["status"], "applied")

    def test_history_prune_keeps_latest_and_restore_safety_checkpoint(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start #1 ({profile})",
                )
                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--allow-stale-sidecar",
                        ]
                    ),
                    f"session commit #1 ({profile})",
                )
                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start #2 ({profile})",
                )
                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--allow-stale-sidecar",
                        ]
                    ),
                    f"session commit #2 ({profile})",
                )

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "list",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "history.list.before.json"),
                        ]
                    ),
                    f"history list before restore ({profile})",
                )
                list_before = read_json(out_dir / "history.list.before.json")
                checkpoints_before = list_before["checkpoints"]
                latest_checkpoint_before = int(checkpoints_before[0]["checkpoint_id"])
                target_checkpoint = int(checkpoints_before[-1]["checkpoint_id"])

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(target_checkpoint),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.apply.json"),
                        ]
                    ),
                    f"history restore apply ({profile})",
                )

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "list",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "history.list.after-restore.json"),
                        ]
                    ),
                    f"history list after restore ({profile})",
                )
                checkpoints_after_restore = read_json(out_dir / "history.list.after-restore.json")[
                    "checkpoints"
                ]
                restore_before_candidates = [
                    int(item["checkpoint_id"])
                    for item in checkpoints_after_restore
                    if str(item.get("source", "")) == "restore_before_apply"
                ]
                self.assertTrue(restore_before_candidates)
                protected_restore_before = restore_before_candidates[0]

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "prune",
                            "--config",
                            str(config_path),
                            "--max-snapshots",
                            "1",
                            "--max-days",
                            "1",
                            "--max-bytes",
                            "1",
                            "--json",
                            str(out_dir / "history.prune.json"),
                        ]
                    ),
                    f"history prune ({profile})",
                )
                prune_payload = read_json(out_dir / "history.prune.json")
                kept = set(int(item) for item in prune_payload["kept_checkpoint_ids"])
                self.assertIn(protected_restore_before, kept)
                self.assertIn(latest_checkpoint_before, kept)

    def test_history_checkpoint_restore_blocks_when_open_session_exists(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "checkpoint",
                            "--config",
                            str(config_path),
                            "--message",
                            "fast checkpoint",
                            "--json",
                            str(out_dir / "history.checkpoint.json"),
                        ]
                    ),
                    f"history checkpoint ({profile})",
                )
                checkpoint_payload = read_json(out_dir / "history.checkpoint.json")
                checkpoint_id = int(checkpoint_payload["checkpoint_id"])

                write_architecture_markdown(workspace, profile, ["Dirty"])
                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start ({profile})",
                )
                blocked = run_build(
                    [
                        "history",
                        "restore",
                        "--config",
                        str(config_path),
                        "--to",
                        str(checkpoint_id),
                        "--json",
                        str(out_dir / "history.restore.blocked.open-session.json"),
                    ]
                )
                self._assert_fail(blocked, f"history restore blocked by open session ({profile})")
                blocked_payload = read_json(out_dir / "history.restore.blocked.open-session.json")
                self.assertEqual(blocked_payload["status"], "blocked")
                self.assertEqual(blocked_payload["blocked_reason"], "open_session")

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.force.open-session.json"),
                        ]
                    ),
                    f"history restore force with open session ({profile})",
                )
                restored_text = (workspace / "InstructWare.iw/architecture.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Alpha", restored_text)
                self.assertNotIn("Dirty", restored_text)

    def test_history_checkpoint_restore_recovers_file_add_delete_and_empty_content(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                preserved_code = workspace / "_ir/src/preserved.ts"
                preserved_code.write_text("export const preserved = 'alpha';\n", encoding="utf-8")
                empty_code = workspace / "_ir/src/empty.ts"
                empty_code.write_text("", encoding="utf-8")

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "checkpoint",
                            "--config",
                            str(config_path),
                            "--message",
                            "edge checkpoint",
                            "--json",
                            str(out_dir / "history.checkpoint.edge.json"),
                        ]
                    ),
                    f"history checkpoint edge ({profile})",
                )
                checkpoint_payload = read_json(out_dir / "history.checkpoint.edge.json")
                checkpoint_id = int(checkpoint_payload["checkpoint_id"])

                write_architecture_markdown(workspace, profile, ["Dirty"])
                preserved_code.unlink()
                empty_code.write_text("export const changed = true;\n", encoding="utf-8")
                added_code = workspace / "_ir/src/added_after_checkpoint.ts"
                added_code.write_text("export const added = 'after';\n", encoding="utf-8")

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.edge.json"),
                        ]
                    ),
                    f"history restore edge ({profile})",
                )
                restored_payload = read_json(out_dir / "history.restore.edge.json")
                self.assertEqual(restored_payload["status"], "applied")
                markdown_after_restore = (workspace / "InstructWare.iw/architecture.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Alpha", markdown_after_restore)
                self.assertNotIn("Dirty", markdown_after_restore)
                self.assertTrue(preserved_code.exists())
                self.assertEqual(
                    preserved_code.read_text(encoding="utf-8"),
                    "export const preserved = 'alpha';\n",
                )
                self.assertFalse(added_code.exists())
                self.assertEqual(empty_code.read_text(encoding="utf-8"), "")

    def test_history_restore_recovers_code_file_rename(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                original_code = workspace / "_ir/src/rename_target.ts"
                original_code.write_text("export const name = 'before';\n", encoding="utf-8")
                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "checkpoint",
                            "--config",
                            str(config_path),
                            "--message",
                            "rename baseline",
                            "--json",
                            str(out_dir / "history.checkpoint.rename.json"),
                        ]
                    ),
                    f"history checkpoint rename ({profile})",
                )
                checkpoint_id = int(read_json(out_dir / "history.checkpoint.rename.json")["checkpoint_id"])

                renamed_code = workspace / "_ir/src/renamed_target.ts"
                original_code.rename(renamed_code)
                renamed_code.write_text("export const name = 'after';\n", encoding="utf-8")
                write_architecture_markdown(workspace, profile, ["Dirty"])

                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.rename.json"),
                        ]
                    ),
                    f"history restore rename ({profile})",
                )
                restored_payload = read_json(out_dir / "history.restore.rename.json")
                self.assertEqual(restored_payload["status"], "applied")
                self.assertTrue(original_code.exists())
                self.assertFalse(renamed_code.exists())
                self.assertEqual(original_code.read_text(encoding="utf-8"), "export const name = 'before';\n")

    def test_history_restore_recovers_utf8_text_content(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                utf8_code = workspace / "_ir/src/utf8_note.ts"
                baseline_text = "export const note = '你好，🌍';\n"
                utf8_code.write_text(baseline_text, encoding="utf-8")
                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "checkpoint",
                            "--config",
                            str(config_path),
                            "--message",
                            "utf8 baseline",
                            "--json",
                            str(out_dir / "history.checkpoint.utf8.json"),
                        ]
                    ),
                    f"history checkpoint utf8 ({profile})",
                )
                checkpoint_id = int(read_json(out_dir / "history.checkpoint.utf8.json")["checkpoint_id"])

                utf8_code.write_text("export const note = 'changed';\n", encoding="utf-8")
                write_architecture_markdown(workspace, profile, ["Dirty"])
                self._assert_ok(
                    run_build(
                        [
                            "history",
                            "restore",
                            "--config",
                            str(config_path),
                            "--to",
                            str(checkpoint_id),
                            "--force",
                            "--json",
                            str(out_dir / "history.restore.utf8.json"),
                        ]
                    ),
                    f"history restore utf8 ({profile})",
                )
                restored_payload = read_json(out_dir / "history.restore.utf8.json")
                self.assertEqual(restored_payload["status"], "applied")
                self.assertEqual(utf8_code.read_text(encoding="utf-8"), baseline_text)

    def test_history_restore_remains_stable_across_multi_round_jumps(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_modify_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                code_file = workspace / "_ir/src/round.ts"
                code_file.write_text("export const round = 0;\n", encoding="utf-8")

                checkpoint_by_round: dict[int, int] = {}
                expected_markdown_by_round: dict[int, str] = {}
                expected_code_by_round: dict[int, str] = {}

                for round_id in range(5):
                    anchors = ["Alpha"] + [f"Node{idx}" for idx in range(1, round_id + 1)]
                    write_architecture_markdown(workspace, profile, anchors)
                    code_text = f"export const round = {round_id};\n"
                    code_file.write_text(code_text, encoding="utf-8")
                    markdown_text = (workspace / "InstructWare.iw/architecture.md").read_text(
                        encoding="utf-8"
                    )
                    self._assert_ok(
                        run_build(
                            [
                                "history",
                                "checkpoint",
                                "--config",
                                str(config_path),
                                "--message",
                                f"round {round_id}",
                                "--json",
                                str(out_dir / f"history.checkpoint.round-{round_id}.json"),
                            ]
                        ),
                        f"history checkpoint round {round_id} ({profile})",
                    )
                    checkpoint_payload = read_json(out_dir / f"history.checkpoint.round-{round_id}.json")
                    checkpoint_by_round[round_id] = int(checkpoint_payload["checkpoint_id"])
                    expected_markdown_by_round[round_id] = markdown_text
                    expected_code_by_round[round_id] = code_text

                for round_id in (4, 2, 0, 3, 1, 4):
                    self._assert_ok(
                        run_build(
                            [
                                "history",
                                "restore",
                                "--config",
                                str(config_path),
                                "--to",
                                str(checkpoint_by_round[round_id]),
                                "--force",
                                "--json",
                                str(out_dir / f"history.restore.round-{round_id}.json"),
                            ]
                        ),
                        f"history restore round {round_id} ({profile})",
                    )
                    restored_payload = read_json(out_dir / f"history.restore.round-{round_id}.json")
                    self.assertEqual(restored_payload["status"], "applied")
                    markdown_after = (workspace / "InstructWare.iw/architecture.md").read_text(
                        encoding="utf-8"
                    )
                    code_after = code_file.read_text(encoding="utf-8")
                    self.assertEqual(markdown_after, expected_markdown_by_round[round_id])
                    self.assertEqual(code_after, expected_code_by_round[round_id])


if __name__ == "__main__":
    unittest.main()
