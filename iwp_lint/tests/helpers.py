from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def sqlite_conn(path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(path.resolve())) as conn:
        yield conn
