from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import LintConfig
from .vcs.diff_resolver import DiffResult as VcsDiffResult
from .vcs.diff_resolver import impacted_nodes as _impacted_nodes
from .vcs.diff_resolver import load_diff as _load_diff

DiffResult = VcsDiffResult


def load_diff(
    config: LintConfig, base: str, head: str, cwd: Path, strict: bool = True
) -> DiffResult:
    return _load_diff(config=config, base=base, head=head, cwd=cwd, strict=strict)


def impacted_nodes(nodes: list[Any], diff_result: DiffResult) -> list[Any]:
    return _impacted_nodes(nodes, diff_result)
