from __future__ import annotations

import unittest

from test.helpers import copy_scenario_to_workspace, read_json, run_build, write_links_for_source


class AuthoringTokensBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def _assert_fail(self, result, label: str) -> None:
        self.assertNotEqual(
            result.returncode,
            0,
            msg=f"{label} unexpectedly passed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_build_outputs_trace_required_summary_fields(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree\n"
            "- Primary CTA @iwp(file=logic,section=trigger)\n"
            "- Secondary copy @no-iwp\n\n"
            "## Interaction Hooks\n"
            "- Open docs @iwp(kind=logic.output)\n",
            encoding="utf-8",
        )
        write_links_for_source(workspace, "views/pages/home.md")
        build_result = run_build(
            [
                "build",
                "--config",
                str(config_path),
                "--mode",
                "auto",
                "--json",
                str(out_dir / "build.json"),
            ]
        )
        self._assert_ok(build_result, "build authoring tokens")
        payload = read_json(out_dir / "build.json")
        summary = payload["gap_report"]["summary"]
        self.assertIn("trace_required_nodes", summary)
        self.assertIn("trace_required_uncovered_nodes", summary)
        self.assertIn("trace_token_profile_enabled", summary)
        self.assertIn("kind_unknown_nodes", summary)
        self.assertGreaterEqual(int(summary["trace_required_nodes"]), 1)
        self.assertEqual(int(summary["trace_required_uncovered_nodes"]), 0)
        self.assertTrue(bool(summary["trace_token_profile_enabled"]))

    def test_build_fails_when_required_trace_nodes_uncovered(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        source_file = workspace / "InstructWare.iw/views/pages/home.md"
        source_file.write_text(
            "# Page: Home\n\n"
            "## Layout Tree\n"
            "- Primary CTA @iwp(file=logic,section=trigger)\n\n"
            "## Interaction Hooks\n"
            "- Open docs @iwp(kind=logic.output)\n",
            encoding="utf-8",
        )
        build_result = run_build(
            [
                "build",
                "--config",
                str(config_path),
                "--mode",
                "auto",
                "--json",
                str(out_dir / "build-fail.json"),
            ]
        )
        self._assert_fail(build_result, "build required trace uncovered")
        payload = read_json(out_dir / "build-fail.json")
        summary = payload["gap_report"]["summary"]
        self.assertGreaterEqual(int(summary["trace_required_nodes"]), 1)
        self.assertGreaterEqual(int(summary["trace_required_uncovered_nodes"]), 1)


if __name__ == "__main__":
    unittest.main()
