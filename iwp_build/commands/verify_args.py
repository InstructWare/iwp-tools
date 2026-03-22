from __future__ import annotations

import argparse


def add_verify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    verify_cmd = subparsers.add_parser("verify", help="Run compiled check + lint gate (+ tests)")
    verify_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    verify_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    verify_cmd.add_argument("--run-tests", action="store_true", help="Run regression tests")
    verify_cmd.add_argument(
        "--with-tests",
        action="store_true",
        help="Run regression tests after protocol gate passes",
    )
    verify_cmd.add_argument(
        "--protocol-only",
        action="store_true",
        help="Run protocol gates only and skip test gate",
    )
    verify_cmd.add_argument(
        "--min-severity",
        choices=["warning", "error"],
        default=None,
        help="Minimum severity to print in diagnostics output",
    )
    verify_cmd.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Hide warning diagnostics while keeping summary counts",
    )
    verify_cmd.set_defaults(command="verify")
