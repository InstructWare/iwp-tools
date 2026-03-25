from __future__ import annotations

import unittest

from iwp_lint.config import load_config, resolve_schema_source
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from test.helpers import (
    apply_schema_profile,
    copy_scenario_to_workspace,
    run_lint,
    write_architecture_markdown,
    write_links_for_source,
)


class AuthoringTokensLintE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def _assert_fail(self, result, label: str, contains: str) -> None:
        self.assertNotEqual(
            result.returncode,
            0,
            msg=f"{label} unexpectedly passed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        output = f"{result.stdout}\n{result.stderr}"
        self.assertIn(contains, output, msg=f"{label} missing expected token: {contains}")

    def test_global_tokens_map_semantics_for_architecture_file(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("bootstrap_no_baseline_no_links")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        apply_schema_profile(config_path, "official")
        write_architecture_markdown(
            workspace,
            "official",
            [
                "Boundary Action @iwp(file=middleware,section=trigger)",
                "Compute Path @iwp(kind=logic.output)",
                "Doc Note @no-iwp",
            ],
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
        )
        by_anchor = {node.anchor_text: node for node in nodes}
        self.assertEqual(by_anchor["Boundary Action"].file_type_id, "middleware")
        self.assertEqual(by_anchor["Boundary Action"].section_key, "trigger")
        self.assertTrue(by_anchor["Boundary Action"].trace_required)
        self.assertEqual(by_anchor["Boundary Action"].trace_source, "item_token")
        self.assertEqual(by_anchor["Compute Path"].file_type_id, "logic")
        self.assertEqual(by_anchor["Compute Path"].section_key, "output")
        self.assertTrue(by_anchor["Compute Path"].trace_required)
        self.assertFalse(by_anchor["Doc Note"].trace_required)

        write_links_for_source(workspace, "architecture.md")
        result = run_lint(["full", "--config", str(config_path)])
        self._assert_ok(result, "lint full authoring token architecture")

    def test_heading_level_token_works_with_schema_validation(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree @iwp(file=logic,section=trigger)\n"
            "- Build card\n\n"
            "## Interaction Hooks\n"
            "- Open docs\n",
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
        )
        by_anchor = {node.anchor_text: node for node in nodes}
        self.assertEqual(by_anchor["Layout Tree"].file_type_id, "logic")
        self.assertEqual(by_anchor["Layout Tree"].section_key, "trigger")
        self.assertTrue(by_anchor["Layout Tree"].trace_required)

        write_links_for_source(workspace, "views/pages/home.md")
        schema_result = run_lint(["schema", "--config", str(config_path), "--mode", "strict"])
        self._assert_ok(schema_result, "schema strict heading token")
        full_result = run_lint(["full", "--config", str(config_path)])
        self._assert_ok(full_result, "lint full heading token")

    def test_schema_rejects_conflicting_or_non_trailing_control_token(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree\n"
            "- bad conflict @iwp @no-iwp\n"
            "- bad middle @iwp(kind=logic.trigger) trailing words\n\n"
            "## Interaction Hooks\n"
            "- Open docs\n",
            encoding="utf-8",
        )
        schema_result = run_lint(["schema", "--config", str(config_path), "--mode", "strict"])
        self._assert_fail(schema_result, "schema strict invalid token", "IWP204")

    def test_schema_ignores_control_token_inside_fenced_code(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree\n"
            "- Render Hero\n\n"
            "```md\n"
            "- demo @iwp(kind=logic.trigger)\n"
            "- demo @iwp @no-iwp\n"
            "```\n\n"
            "## Interaction Hooks\n"
            "- Open docs\n",
            encoding="utf-8",
        )
        schema_result = run_lint(["schema", "--config", str(config_path), "--mode", "strict"])
        self._assert_ok(schema_result, "schema strict ignores fenced code token")

    def test_node_generation_mode_annotated_only_filters_unannotated_nodes(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree @iwp(kind=views.pages.layout_tree)\n"
            "- tracked child\n\n"
            "## Free Notes\n"
            "- untracked child\n",
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        structural_nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode="structural",
        )
        annotated_nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode="annotated_only",
        )
        structural_anchor_set = {node.anchor_text for node in structural_nodes}
        annotated_anchor_set = {node.anchor_text for node in annotated_nodes}
        self.assertIn("untracked child", structural_anchor_set)
        self.assertNotIn("untracked child", annotated_anchor_set)
        self.assertIn("tracked child", annotated_anchor_set)

    def test_list_top_token_applies_to_following_list_block(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## State Expectations\n"
            "@iwp(file=state,section=fields)\n"
            "- `ui_prefs.locale`\n"
            "- `docs_runtime.active_doc_id`\n",
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode="annotated_only",
        )
        by_anchor = {node.anchor_text: node for node in nodes}
        self.assertIn("`ui_prefs.locale`", by_anchor)
        self.assertIn("`docs_runtime.active_doc_id`", by_anchor)
        self.assertEqual(by_anchor["`ui_prefs.locale`"].file_type_id, "state")
        self.assertEqual(by_anchor["`ui_prefs.locale`"].section_key, "fields")
        self.assertTrue(by_anchor["`ui_prefs.locale`"].trace_required)

    def test_list_top_no_iwp_excludes_block_even_under_h2_iwp(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## State Expectations @iwp(file=state,section=fields)\n"
            "@no-iwp\n"
            "- `ui_prefs.locale`\n"
            "- `docs_runtime.active_doc_id`\n",
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode="annotated_only",
        )
        anchor_set = {node.anchor_text for node in nodes}
        self.assertNotIn("`ui_prefs.locale`", anchor_set)
        self.assertNotIn("`docs_runtime.active_doc_id`", anchor_set)

    def test_h2_annotation_no_longer_tracks_plain_text_lines(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## State Expectations @iwp(file=state,section=fields)\n"
            "Required state fields:\n"
            "- `ui_prefs.locale`\n",
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        schema_path = resolve_schema_source(config)
        nodes = parse_markdown_nodes(
            iwp_root=config.iwp_root_path,
            critical_patterns=config.critical_node_patterns,
            schema_path=schema_path,
            exclude_markdown_globs=config.schema_exclude_markdown_globs,
            node_registry_file=config.node_registry_file,
            page_only_enabled=config.page_only.enabled,
            authoring_tokens_enabled=config.authoring.tokens.enabled,
            node_generation_mode="annotated_only",
        )
        anchor_set = {node.anchor_text for node in nodes}
        self.assertNotIn("Required state fields:", anchor_set)
        self.assertIn("`ui_prefs.locale`", anchor_set)


if __name__ == "__main__":
    unittest.main()
