from __future__ import annotations

import unittest

from iwp_lint.config import load_config
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from test.helpers import copy_scenario_to_workspace, run_lint, write_links_for_source


class PageOnlyNamespacedLintE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_page_only_namespaced_h2_maps_to_logic_state_and_passes_lint(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        config = load_config(str(config_path))
        self.assertTrue(config.page_only.enabled)

        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=config.schema_file,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
        )
        by_anchor = {node.anchor_text: node for node in nodes}
        self.assertEqual(by_anchor["Click hero primary action"].computed_kind, "logic.trigger")
        self.assertEqual(by_anchor["route=manifesto"].computed_kind, "logic.input")
        self.assertEqual(by_anchor["open docs/manifesto"].computed_kind, "logic.output")
        self.assertEqual(by_anchor["selected_doc"].computed_kind, "state.fields")

        links = write_links_for_source(workspace, "views/pages/home.md")
        self.assertGreaterEqual(len(links), 6)

        schema_result = run_lint(["schema", "--config", str(config_path), "--mode", "strict"])
        self._assert_ok(schema_result, "schema strict page_only")

        full_result = run_lint(["full", "--config", str(config_path)])
        self._assert_ok(full_result, "lint full page_only")


if __name__ == "__main__":
    unittest.main()
