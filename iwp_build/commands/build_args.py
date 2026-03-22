from __future__ import annotations

import argparse


def add_build_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    build_cmd = subparsers.add_parser(
        "build",
        help="Compile .iwc + sidecar and compute implementation gap (no baseline checkpoint)",
    )
    build_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    build_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    build_cmd.add_argument(
        "--mode",
        choices=["auto", "diff", "full"],
        default="auto",
        help="Build mode: auto tries diff first and falls back to full on first run; build never advances baseline",
    )
    build_cmd.add_argument("--json", help="Write build summary JSON to path", default=None)
    build_cmd.add_argument(
        "--normalize-links",
        action="store_true",
        help="Normalize @iwp.link annotations before compile and gap checks",
    )
    build_cmd.add_argument(
        "--no-code-sidecar",
        action="store_true",
        help="Skip building code sidecar output under .iwp/compiled/code",
    )
    build_cmd.set_defaults(command="build")
