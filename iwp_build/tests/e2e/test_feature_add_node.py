from __future__ import annotations

import unittest

from iwp_lint.config import load_config
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from test.helpers import (
    SCHEMA_PROFILES,
    append_link_line,
    apply_schema_profile,
    assert_build_diff_contract,
    copy_scenario_to_workspace,
    latest_snapshot_id,
    read_json,
    run_build,
    write_architecture_markdown,
    write_links_for_source,
)


class FeatureAddNodeBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_feature_add_node_build_then_patch_links(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_add_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                init_result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "auto",
                        "--json",
                        str(out_dir / "build_init.json"),
                    ]
                )
                self._assert_ok(init_result, f"initial build ({profile})")
                baseline_before = latest_snapshot_id(config_path)
                self.assertIsNone(baseline_before)

                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                fail_result = run_build(
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
                self.assertEqual(fail_result.returncode, 1)
                fail_payload = read_json(out_dir / "build_fail.json")
                assert_build_diff_contract(
                    self,
                    fail_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=True,
                    expected_mode="bootstrap_full",
                )
                baseline_after_fail = latest_snapshot_id(config_path)
                self.assertEqual(baseline_after_fail, baseline_before)

                config = load_config(str(config_path))
                nodes = parse_markdown_nodes(
                    iwp_root=config.iwp_root_path,
                    critical_patterns=config.critical_node_patterns,
                    schema_path=(workspace / config.schema_file).resolve(),
                    exclude_markdown_globs=config.schema_exclude_markdown_globs,
                    node_registry_file=config.node_registry_file,
                )
                beta_id = next(node.node_id for node in nodes if node.anchor_text == "Beta")
                append_link_line(workspace, "architecture.md", beta_id)

                pass_result = run_build(
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
                self._assert_ok(pass_result, f"patched build ({profile})")
                pass_payload = read_json(out_dir / "build_pass.json")
                assert_build_diff_contract(
                    self,
                    pass_payload,
                    expected_md_file="architecture.md",
                    expect_gap_errors=False,
                    expected_mode="bootstrap_full",
                )
                baseline_after_pass = latest_snapshot_id(config_path)
                self.assertEqual(baseline_after_pass, baseline_before)

                verify_result = run_build(["verify", "--config", str(config_path)])
                self._assert_ok(verify_result, f"verify after patch ({profile})")
