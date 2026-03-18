from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    run_lint,
    write_architecture_markdown,
    write_links_for_source,
)


class CodeOnlyAndCompiledLintE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_code_only_change_diff_scope_stays_zero(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("code_only_change")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_dir = workspace / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha"])

                write_links_for_source(workspace, "architecture.md")
                self._assert_ok(
                    run_build(["build", "--config", str(config_path), "--mode", "auto"]),
                    f"baseline build ({profile})",
                )

                link_file = workspace / "_ir/src/iwp_links.ts"
                current = link_file.read_text(encoding="utf-8")
                link_file.write_text(current + "// touch\n", encoding="utf-8")

                diff_json = out_dir / "lint_diff.json"
                diff_result = run_lint(
                    ["diff", "--config", str(config_path), "--json", str(diff_json)]
                )
                self._assert_ok(diff_result, f"lint diff ({profile})")
                report = read_json(diff_json)
                self.assertEqual(report["summary"].get("total_nodes_in_scope", -1), 0)
                self.assertLessEqual(float(report["metrics"]["node_linked_percent"]), 100.0)
                self.assertLessEqual(float(report["metrics"]["node_tested_percent"]), 100.0)
                codes = {item["code"] for item in report.get("diagnostics", [])}
                self.assertNotIn("IWP103", codes)

    def test_verify_compiled_reports_missing_md_artifact(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("compiled_stale_or_missing")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha"])

                compile_result = run_lint(["nodes", "compile", "--config", str(config_path)])
                self._assert_ok(compile_result, f"nodes compile ({profile})")

                compiled_md = workspace / ".iwp/compiled/md/architecture.md.iwc.md"
                self.assertTrue(compiled_md.exists())
                compiled_md.unlink()

                verify_result = run_lint(["nodes", "verify-compiled", "--config", str(config_path)])
                self.assertEqual(verify_result.returncode, 1)
