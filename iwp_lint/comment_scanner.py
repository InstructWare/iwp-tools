from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .errors import Diagnostic
from .models import LinkAnnotation

LINK_RE = re.compile(r"@iwp\.link\s+([^:\s]+\.md)::([^:\s]+)(?!::)")
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")


def discover_code_files(root: Path, code_roots: list[str], include_ext: list[str]) -> list[Path]:
    include_ext_set = {ext if ext.startswith(".") else f".{ext}" for ext in include_ext}
    files: list[Path] = []
    for code_root in code_roots:
        base = (root / code_root).resolve()
        if not base.exists():
            continue
        for file_path in base.rglob("*"):
            if file_path.is_file() and file_path.suffix in include_ext_set:
                files.append(file_path)
    return sorted(set(files))


def scan_links(
    project_root: Path,
    files: list[Path],
    allow_multi_link_per_symbol: bool,
) -> tuple[list[LinkAnnotation], list[Diagnostic]]:
    links: list[LinkAnnotation] = []
    diagnostics: list[Diagnostic] = []

    for file_path in files:
        rel_path = file_path.relative_to(project_root).as_posix()
        for line_no, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
            if "@iwp.link" not in line:
                continue
            matches = list(LINK_RE.finditer(line))
            if not matches:
                diagnostics.append(
                    Diagnostic(
                        code="IWP101",
                        message="Annotation does not match @iwp.link <source_path>::<node_id>",
                        file_path=rel_path,
                        line=line_no,
                    )
                )
                continue
            if len(matches) > 1 and not allow_multi_link_per_symbol:
                diagnostics.append(
                    Diagnostic(
                        code="IWP106",
                        message="Multiple @iwp.link entries on same code position are not allowed",
                        file_path=rel_path,
                        line=line_no,
                    )
                )
            for match in matches:
                source_path, node_id = match.group(1), match.group(2)
                col = match.start() + 1
                links.append(
                    LinkAnnotation(
                        source_path=source_path,
                        node_id=node_id,
                        file_path=rel_path,
                        line=line_no,
                        column=col,
                        raw=match.group(0),
                    )
                )

    return links, diagnostics


def validate_link_protocol(links: list[LinkAnnotation]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for link in links:
        if not link.source_path.endswith(".md") or link.source_path.startswith("/"):
            diagnostics.append(
                Diagnostic(
                    code="IWP103",
                    message=f"Invalid source_path: {link.source_path}",
                    file_path=link.file_path,
                    line=link.line,
                    column=link.column,
                )
            )
        if not NODE_ID_RE.match(link.node_id):
            diagnostics.append(
                Diagnostic(
                    code="IWP104",
                    message=f"Invalid node_id: {link.node_id}",
                    file_path=link.file_path,
                    line=link.line,
                    column=link.column,
                )
            )
    return diagnostics


def is_test_file(file_path: str, test_globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(file_path, pattern) for pattern in test_globs)
