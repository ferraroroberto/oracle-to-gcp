"""The one place this repo opens a SQLite file.

``connect_sqlite`` returns a connection with row dictionaries enabled, for a
caller that holds it open across several steps. ``transaction`` is the
short-lived form: it commits on success, rolls back on an error and closes the
connection either way. ``with sqlite3.connect(...)`` alone commits but leaves
the connection, and the file handle Windows locks, open.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from src.fs_write import require_runtime_path


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with row dictionaries enabled."""
    require_runtime_path(path)
    # runtime-write-exempt: the file is caller-supplied; require_runtime_path above refuses a code folder
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    """One connection for one unit of work: commit or roll back, then close."""
    with closing(connect_sqlite(path)) as conn, conn:
        yield conn
