from __future__ import annotations

import argparse


def add_history_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    history_cmd = subparsers.add_parser("history", help="Manage baseline checkpoints history")
    history_sub = history_cmd.add_subparsers(dest="history_action", required=True)

    history_list_cmd = history_sub.add_parser("list", help="List available checkpoints")
    history_list_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    history_list_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    history_list_cmd.add_argument("--limit", type=int, default=None, help="Maximum checkpoints")
    history_list_cmd.add_argument("--json", help="Write payload JSON to path", default=None)
    history_list_cmd.set_defaults(command="history", history_action="list")

    history_restore_cmd = history_sub.add_parser(
        "restore", help="Restore workspace to a specific checkpoint"
    )
    history_restore_cmd.add_argument(
        "--config", help="Path to .iwp-lint.yaml or .json", default=None
    )
    history_restore_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    history_restore_cmd.add_argument(
        "--to",
        type=int,
        required=True,
        help="Target checkpoint id",
    )
    history_restore_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview restore impact without applying changes",
    )
    history_restore_cmd.add_argument(
        "--force",
        action="store_true",
        help="Allow restore even when workspace has uncommitted local changes",
    )
    history_restore_cmd.add_argument("--json", help="Write payload JSON to path", default=None)
    history_restore_cmd.set_defaults(command="history", history_action="restore")

    history_prune_cmd = history_sub.add_parser(
        "prune", help="Prune checkpoints by retention policy"
    )
    history_prune_cmd.add_argument("--config", help="Path to .iwp-lint.yaml or .json", default=None)
    history_prune_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    history_prune_cmd.add_argument(
        "--max-snapshots", type=int, default=None, help="Maximum number of retained checkpoints"
    )
    history_prune_cmd.add_argument(
        "--max-days", type=int, default=None, help="Maximum retention days"
    )
    history_prune_cmd.add_argument(
        "--max-bytes", type=int, default=None, help="Maximum retained bytes"
    )
    history_prune_cmd.add_argument("--json", help="Write payload JSON to path", default=None)
    history_prune_cmd.set_defaults(command="history", history_action="prune")

    history_checkpoint_cmd = history_sub.add_parser(
        "checkpoint", help="Create a file-level checkpoint from current workspace"
    )
    history_checkpoint_cmd.add_argument(
        "--config", help="Path to .iwp-lint.yaml or .json", default=None
    )
    history_checkpoint_cmd.add_argument(
        "--preset",
        default=None,
        help="Execution preset name from .iwp-lint.yaml execution_presets",
    )
    history_checkpoint_cmd.add_argument(
        "--message",
        default=None,
        help="Checkpoint message for history timeline",
    )
    history_checkpoint_cmd.add_argument("--json", help="Write payload JSON to path", default=None)
    history_checkpoint_cmd.set_defaults(command="history", history_action="checkpoint")
