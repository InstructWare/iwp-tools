from __future__ import annotations

import unittest

from iwp_lint.config import load_config
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    run_lint,
    write_i18n_home_markdown,
    write_links_for_source,
)


class I18nAndSchemaLintE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_i18n_minor_text_change_keeps_node_id_stable(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("i18n_zh_en")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                apply_schema_profile(config_path, profile)
                write_i18n_home_markdown(workspace, profile, "阅读宣言")
                config = load_config(str(config_path))

                nodes_before = parse_markdown_nodes(
                    iwp_root=config.iwp_root_path,
                    critical_patterns=config.critical_node_patterns,
                    schema_path=(workspace / config.schema_file).resolve(),
                    exclude_markdown_globs=config.schema_exclude_markdown_globs,
                    node_registry_file=config.node_registry_file,
                )
                first_id = next(
                    node.node_id for node in nodes_before if node.anchor_text == "阅读宣言"
                )

                write_i18n_home_markdown(workspace, profile, "阅读《宣言》")
                nodes_after = parse_markdown_nodes(
                    iwp_root=config.iwp_root_path,
                    critical_patterns=config.critical_node_patterns,
                    schema_path=(workspace / config.schema_file).resolve(),
                    exclude_markdown_globs=config.schema_exclude_markdown_globs,
                    node_registry_file=config.node_registry_file,
                )
                second_id = next(
                    node.node_id for node in nodes_after if node.anchor_text == "阅读《宣言》"
                )
                self.assertEqual(first_id, second_id)

                write_links_for_source(workspace, "views/pages/home.md")
                result = run_lint(["full", "--config", str(config_path)])
                self._assert_ok(result, f"lint full i18n ({profile})")
