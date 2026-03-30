from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    write_architecture_markdown,
)


class FastModeNoIwpBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_fast_mode_without_iwp_allows_reconcile_and_commit(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("fast_mode_no_iwp")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])

                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "start",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "session.start.json"),
                        ]
                    ),
                    f"session start ({profile})",
                )

                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "diff",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "session.diff.json"),
                        ]
                    ),
                    f"session diff ({profile})",
                )
                diff_payload = read_json(out_dir / "session.diff.json")
                self.assertEqual(diff_payload["impacted_nodes"], [])
                self.assertEqual(diff_payload["link_targets_suggested"], [])

                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "reconcile",
                            "--config",
                            str(config_path),
                            "--auto-build-sidecar",
                            "--json",
                            str(out_dir / "session.reconcile.json"),
                        ]
                    ),
                    f"session reconcile ({profile})",
                )
                reconcile_payload = read_json(out_dir / "session.reconcile.json")
                self.assertTrue(bool(reconcile_payload.get("can_commit")))
                self.assertEqual(reconcile_payload.get("status"), "pass")

                self._assert_ok(
                    run_build(
                        [
                            "session",
                            "commit",
                            "--config",
                            str(config_path),
                            "--json",
                            str(out_dir / "session.commit.json"),
                        ]
                    ),
                    f"session commit ({profile})",
                )
                commit_payload = read_json(out_dir / "session.commit.json")
                self.assertEqual(commit_payload["status"], "committed")
                self.assertIsNotNone(commit_payload.get("checkpoint_id"))

                self._assert_ok(
                    run_build(["verify", "--config", str(config_path)]),
                    f"verify ({profile})",
                )

    def test_fast_mode_structural_emits_mode_warning(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("fast_mode_no_iwp")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                config_text = config_path.read_text(encoding="utf-8")
                config_text = config_text.replace(
                    "node_generation_mode: annotated_only",
                    "node_generation_mode: structural",
                )
                config_text += "\nworkflow:\n  mode: fast\n"
                config_path.write_text(config_text, encoding="utf-8")

                self._assert_ok(
                    run_build(["session", "start", "--config", str(config_path)]),
                    f"session start structural fast ({profile})",
                )
                reconcile = run_build(
                    [
                        "session",
                        "reconcile",
                        "--config",
                        str(config_path),
                        "--auto-build-sidecar",
                        "--json",
                        str(out_dir / "session.reconcile.structural-fast.json"),
                    ]
                )
                self.assertNotEqual(reconcile.returncode, 0)
                payload = read_json(out_dir / "session.reconcile.structural-fast.json")
                self.assertEqual(payload.get("meta", {}).get("workflow_mode"), "fast")
                self.assertTrue(bool(payload.get("mode_warnings")))
