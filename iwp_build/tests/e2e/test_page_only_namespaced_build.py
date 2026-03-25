from __future__ import annotations

import unittest

from test.helpers import copy_scenario_to_workspace, read_json, run_build, write_links_for_source


class PageOnlyNamespacedBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_build_payload_marks_page_only_enabled_and_passes(self) -> None:
        tempdir, workspace = copy_scenario_to_workspace("page_only_namespaced")
        self.addCleanup(tempdir.cleanup)
        config_path = workspace / ".iwp-lint.yaml"
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
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
        self._assert_ok(build_result, "build page_only")

        payload = read_json(out_dir / "build.json")
        self.assertTrue(bool(payload.get("mode_flags", {}).get("page_only_enabled")))
        self.assertTrue(bool(payload.get("summary", {}).get("page_only_enabled")))
        self.assertEqual(int(payload["summary"]["gap_error_count"]), 0)

        verify_result = run_build(["verify", "--config", str(config_path)])
        self._assert_ok(verify_result, "verify page_only")


if __name__ == "__main__":
    unittest.main()
