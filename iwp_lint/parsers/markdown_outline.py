from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

H1_RE = re.compile(r"^#\s+.+$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")


@dataclass(frozen=True)
class H2Section:
    title: str
    line: int


@dataclass(frozen=True)
class MarkdownOutline:
    h1_count: int
    h2_sections: list[H2Section]


def extract_outline(markdown_path: Path) -> MarkdownOutline:
    h1_count = 0
    h2_sections: list[H2Section] = []
    for idx, line in enumerate(markdown_path.read_text(encoding="utf-8").splitlines(), start=1):
        if H1_RE.match(line):
            h1_count += 1
            continue
        h2_match = H2_RE.match(line)
        if h2_match:
            h2_sections.append(H2Section(title=h2_match.group(1).strip(), line=idx))
    return MarkdownOutline(h1_count=h1_count, h2_sections=h2_sections)
