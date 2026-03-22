from __future__ import annotations

import argparse


def add_session_node_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--node-severity",
        choices=["all", "error", "warning"],
        default=None,
        help="Filter impacted nodes by profile missing severity",
    )
    parser.add_argument(
        "--node-file-type-id",
        action="append",
        dest="node_file_type_ids",
        default=None,
        help="Filter impacted nodes by file_type_id; repeat for multiple values",
    )
    parser.add_argument(
        "--node-anchor-level",
        action="append",
        dest="node_anchor_levels",
        default=None,
        help="Filter impacted nodes by anchor level; repeat for multiple values",
    )
    parser.add_argument(
        "--node-kind-prefix",
        action="append",
        dest="node_kind_prefixes",
        default=None,
        help="Filter impacted nodes by computed_kind prefix; repeat for multiple values",
    )
    parser.add_argument(
        "--critical-only",
        action="store_true",
        help="Only include critical impacted nodes",
    )
    parser.add_argument(
        "--markdown-excerpt-max-chars",
        type=int,
        default=None,
        help="Maximum chars for impacted markdown node excerpt (0 disables truncation)",
    )
