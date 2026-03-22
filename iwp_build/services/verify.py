from __future__ import annotations

import sys
import unittest
from typing import Any

from ..output import print_compiled_failure_summary, print_lint_failure_summary

try:
    from iwp_lint.api import baseline_status, run_quality_gate, verify_compiled
    from iwp_lint.core.engine import print_console_report
except ImportError:
    from ...iwp_lint.api import baseline_status, run_quality_gate, verify_compiled
    from ...iwp_lint.core.engine import print_console_report


def run_verify(
    config: Any,
    with_tests: bool,
    *,
    protocol_only: bool = False,
    min_severity: str = "warning",
    quiet_warnings: bool = False,
) -> int:
    baseline = baseline_status(config)
    print(
        "[iwp-build] verify baseline "
        f"exists={baseline.get('baseline_exists')} "
        f"id={baseline.get('baseline_snapshot_id')}"
    )
    compiled = verify_compiled(config)
    protocol_gate = "PASS"
    tests_gate = "SKIPPED"
    if not bool(compiled.get("ok", False)):
        print(f"[iwp-build] verify compiled checked={compiled.get('checked_sources', 0)} ok=False")
        print_compiled_failure_summary(compiled)
        print(
            "[iwp-build] next "
            "rebuild compiled context first: "
            "uv run iwp-build build --config .iwp-lint.yaml --mode diff"
        )
        protocol_gate = "FAIL"
        print(f"[iwp-build] verify gates protocol={protocol_gate} tests={tests_gate} overall=FAIL")
        return 1

    gate = run_quality_gate(config)
    print_console_report(
        gate["lint_report"],
        min_severity=min_severity,
        quiet_warnings=quiet_warnings,
    )
    if gate["lint_exit_code"] != 0:
        print_lint_failure_summary(
            gate["lint_report"],
            min_severity=min_severity,
            quiet_warnings=quiet_warnings,
        )
        protocol_gate = "FAIL"
        print(f"[iwp-build] verify gates protocol={protocol_gate} tests={tests_gate} overall=FAIL")
        return 1
    if not protocol_only and with_tests:
        suite = unittest.defaultTestLoader.loadTestsFromName("tools.iwp_lint.tests.test_regression")
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
        if not result.wasSuccessful():
            tests_gate = "FAIL"
            print(
                f"[iwp-build] verify gates protocol={protocol_gate} tests={tests_gate} overall=FAIL"
            )
            return 1
        tests_gate = "PASS"
    overall = "PASS"
    print(f"[iwp-build] verify gates protocol={protocol_gate} tests={tests_gate} overall={overall}")
    print("[iwp-build] verify ok")
    return 0
