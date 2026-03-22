from __future__ import annotations

import unittest

from test.helpers import (
    SCHEMA_PROFILES,
    apply_schema_profile,
    copy_scenario_to_workspace,
    read_json,
    run_build,
    write_architecture_markdown,
    write_links_for_source,
)


class BootstrapOfficialSchemaBuildE2E(unittest.TestCase):
    def _assert_ok(self, result, label: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{label} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_bootstrap_first_build_profiles(self) -> None:
        for profile in SCHEMA_PROFILES:
            with self.subTest(schema_profile=profile):
                tempdir, workspace = copy_scenario_to_workspace("bootstrap_first_build")
                self.addCleanup(tempdir.cleanup)
                config_path = workspace / ".iwp-lint.yaml"
                out_json = workspace / f"out/bootstrap_{profile}.json"
                out_json.parent.mkdir(parents=True, exist_ok=True)
                apply_schema_profile(config_path, profile)
                write_architecture_markdown(workspace, profile, ["Alpha"])
                write_links_for_source(workspace, "architecture.md")
                result = run_build(
                    [
                        "build",
                        "--config",
                        str(config_path),
                        "--mode",
                        "auto",
                        "--json",
                        str(out_json),
                    ]
                )
                self._assert_ok(result, f"bootstrap first build ({profile})")
                payload = read_json(out_json)
                self.assertEqual(payload["summary"]["build_mode"], "bootstrap_full")
                self.assertFalse(payload["summary"]["baseline_bootstrapped"])
