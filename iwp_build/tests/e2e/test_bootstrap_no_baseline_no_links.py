from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    latest_snapshot_id,
    read_json,
    run_build,
    write_architecture_markdown,
    write_links_for_source,
)


class BootstrapNoBaselineNoLinksBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_bootstrap_fail_then_patch_links_then_verify(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("bootstrap_no_baseline_no_links")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])

                self.assertIsNone(latest_snapshot_id(config_path))
                fail_result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "auto",
                        "--json",
                        str(out_dir / "build_fail.json"),
                    ]
                )
                self.assertEqual(fail_result.returncode, 1)
                fail_payload = read_json(out_dir / "build_fail.json")
                self.assertEqual(fail_payload["summary"]["build_mode"], "bootstrap_full")
                self.assertFalse(fail_payload["summary"]["baseline_bootstrapped"])
                self.assertGreater(fail_payload["summary"]["gap_error_count"], 0)
                self.assertIsNone(latest_snapshot_id(config_path))

                write_links_for_source(workspace, "architecture.md")
                pass_result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "auto",
                        "--json",
                        str(out_dir / "build_pass.json"),
                    ]
                )
                self._assert_ok(pass_result, f"bootstrap after link patch ({profile})")
                pass_payload = read_json(out_dir / "build_pass.json")
                self.assertEqual(pass_payload["summary"]["build_mode"], "bootstrap_full")
                self.assertFalse(pass_payload["summary"]["baseline_bootstrapped"])
                self.assertEqual(pass_payload["summary"]["gap_error_count"], 0)
                self.assertIsNone(latest_snapshot_id(config_path))
                self._assert_ok(
                    run_build(["verify", "--config", str(config_path)]),
                    f"verify after bootstrap ({profile})",
                )
