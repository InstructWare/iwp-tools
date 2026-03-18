from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    read_json,
    run_lint,
    write_architecture_markdown,
    write_links_for_source,
)


class DeletedNodeLintE2E(unittest.TestCase):
    def test_deleted_node_with_stale_link_is_reported(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("feature_delete_node")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_json = workspace / "out/lint_full_deleted.json"
                out_json.parent.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)

                write_architecture_markdown(workspace, profile, ["Alpha", "Beta"])
                write_links_for_source(workspace, "architecture.md")
                write_architecture_markdown(workspace, profile, ["Alpha"])

                result = run_lint(["full", "--config", str(config_path), "--json", str(out_json)])
                self.assertEqual(result.returncode, 1)
                report = read_json(out_json)
                codes = {item["code"] for item in report.get("diagnostics", [])}
                self.assertTrue({"IWP104", "IWP105"}.intersection(codes))
