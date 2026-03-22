from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    assert_build_diff_contract,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    run_lint,
    write_architecture_markdown,
    write_links_for_source,
)


class FeatureDeleteNodeBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_feature_delete_node_requires_link_cleanup_before_verify(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_delete_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["build", "--config", str(config_path), "--mode", "auto"]),
                    f"initial baseline build ({profile})",
                )

                write_architecture_markdown(workspace, profile, ["Alpha"])
                build_fail = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "diff",
                        "--json",
                        str(out_dir / "build_fail.json"),
                    ]
                )
                self.assertEqual(build_fail.returncode, 1)
                fail_payload = read_json(out_dir / "build_fail.json")
                assert_build_diff_contract(
                    self,
                    fail_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=True,
                    expected_mode="bootstrap_full",
                )

                verify_fail = run_build(["verify", "--config", str(config_path)])
                self.assertEqual(verify_fail.returncode, 1)

                lint_json = workspace / "out/lint_after_delete.json"
                lint_fail = run_lint(
                    ["full", "--config", str(config_path), "--json", str(lint_json)]
                )
                self.assertEqual(lint_fail.returncode, 1)
                report = read_json(lint_json)
                codes = {item["code"] for item in report.get("diagnostics", [])}
                self.assertTrue({"IWP104", "IWP105"}.intersection(codes))

                write_links_for_source(workspace, "architecture.md")
                build_pass = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "diff",
                        "--json",
                        str(out_dir / "build_pass.json"),
                    ]
                )
                self._assert_ok(build_pass, f"build after cleanup ({profile})")
                pass_payload = read_json(out_dir / "build_pass.json")
                assert_build_diff_contract(
                    self,
                    pass_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=False,
                    expected_mode="bootstrap_full",
                )
                self._assert_ok(
                    run_build(["verify", "--config", str(config_path)]),
                    f"verify after cleanup ({profile})",
                )
