"""A transaction commits its work and closes; an error rolls it back and closes too."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.sqlite_db import connect_sqlite, transaction


def _rows(path: Path) -> list[tuple[str]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT name FROM t").fetchall()
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "registry.db"
    with transaction(path) as conn:
        conn.execute("CREATE TABLE t (name TEXT)")
    return path


def test_connect_sqlite_returns_rows_by_name(db: Path) -> None:
    conn = connect_sqlite(db)
    try:
        conn.execute("INSERT INTO t VALUES ('orders')")
        assert conn.execute("SELECT name FROM t").fetchone()["name"] == "orders"
    finally:
        conn.close()


def test_a_transaction_commits_and_closes(db: Path) -> None:
    with transaction(db) as conn:
        conn.execute("INSERT INTO t VALUES ('orders')")
    assert _rows(db) == [("orders",)]
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # closed


def test_a_failed_transaction_rolls_back_and_closes(db: Path) -> None:
    with pytest.raises(RuntimeError), transaction(db) as conn:
        conn.execute("INSERT INTO t VALUES ('orders')")
        raise RuntimeError("boom")
    assert _rows(db) == []
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # closed
    db.unlink()  # no handle left open: Windows refuses to delete a file still held
