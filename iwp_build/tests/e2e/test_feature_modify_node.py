from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    assert_build_diff_contract,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    write_architecture_markdown,
    write_links_for_source,
)


class FeatureModifyNodeBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_feature_modify_node_build_flow(self) -> None:
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
                    run_build(["build", "--config", str(config_path), "--mode", "auto"]),
                    f"initial build ({profile})",
                )

                write_architecture_markdown(workspace, profile, ["Alpha V2"])
                fail_result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "diff",
                        "--json",
                        str(out_dir / "build_fail.json"),
                        "--diff-json",
                        str(out_dir / "build_fail.diff.json"),
                    ]
                )
                self.assertEqual(fail_result.returncode, 1)
                fail_payload = read_json(out_dir / "build_fail.json")
                fail_diff_payload = read_json(out_dir / "build_fail.diff.json")
                assert_build_diff_contract(
                    self,
                    fail_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=True,
                )
                self.assertEqual(
                    fail_diff_payload["intent_diff"]["changed_md_files"],
                    fail_payload["intent_diff"]["changed_md_files"],
                )
                self.assertNotIn("compile", fail_diff_payload)
                self.assertNotIn("links_valid", fail_diff_payload["gap_report"])

                write_links_for_source(workspace, "architecture.md")
                pass_result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "diff",
                        "--json",
                        str(out_dir / "build_pass.json"),
                        "--diff-json",
                        str(out_dir / "build_pass.diff.json"),
                    ]
                )
                self._assert_ok(pass_result, f"rebuild after link update ({profile})")
                pass_payload = read_json(out_dir / "build_pass.json")
                pass_diff_payload = read_json(out_dir / "build_pass.diff.json")
                assert_build_diff_contract(
                    self,
                    pass_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=False,
                )
                self.assertEqual(
                    pass_diff_payload["intent_diff"]["changed_md_files"],
                    pass_payload["intent_diff"]["changed_md_files"],
                )
                self.assertNotIn("compile", pass_diff_payload)
                self.assertNotIn("links_valid", pass_diff_payload["gap_report"])
