from __future__ import annotations

import argparse


def add_watch_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    watch_cmd = subparsers.add_parser(
        "watch", help="Watch markdown changes and rebuild .iwc incrementally"
    )
    watch_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    watch_cmd.add_argument(
        "--debounce-ms",
        type=int,
        default=600,
        help="Debounce window for change batching",
    )
    watch_cmd.add_argument(
        "--poll-ms",
        type=int,
        default=250,
        help="Polling interval for file change detection",
    )
    watch_cmd.add_argument(
        "--verify",
        action="store_true",
        help="Verify compiled artifacts after each build",
    )
    watch_cmd.add_argument(
        "--run-tests",
        action="store_true",
        help="Run regression tests after each successful build",
    )
    watch_cmd.add_argument(
        "--once",
        action="store_true",
        help="Run one compile cycle and exit",
    )
    watch_cmd.set_defaults(command="watch")
