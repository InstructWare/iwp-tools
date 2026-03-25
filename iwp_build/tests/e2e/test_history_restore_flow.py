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


if __name__ == "__main__":
    unittest.main()
