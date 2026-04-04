from __future__ import annotations


def build_sidecar_next_actions() -> list[dict[str, object]]:
    return [
        {
            "kind": "refresh_sidecar",
            "reason": "code sidecar is stale or missing",
            "command": "uv run iwp-build build --config .iwp-lint.yaml",
        },
        {
            "kind": "reconcile",
            "reason": "re-check commit readiness after sidecar refresh",
            "command": "uv run iwp-build session reconcile --config .iwp-lint.yaml --preset agent-default",
        },
        {
            "kind": "commit",
            "reason": "commit when gate becomes pass",
            "command": "uv run iwp-build session commit --config .iwp-lint.yaml --preset ci-strict",
        },
    ]


def build_next_command_examples(
    *,
    next_actions: list[dict[str, object]],
    max_items: int,
) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for action in next_actions:
        raw_command = action.get("command", "")
        command = str(raw_command).strip() if raw_command is not None else ""
        if not command or command.lower() == "none" or command in seen:
            continue
        seen.add(command)
        commands.append(command)
        if len(commands) >= max(1, max_items):
            break
    return commands


def build_recommended_next_chain(
    *,
    next_actions: list[dict[str, object]],
    max_items: int,
) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    for action in next_actions:
        raw_command = action.get("command", "")
        command = str(raw_command).strip() if raw_command is not None else ""
        if not command or command.lower() == "none" or command in seen:
            continue
        seen.add(command)
        chain.append(command)
        if len(chain) >= max(1, max_items):
            break
    return chain
