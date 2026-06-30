"""SQLite-backed mock Oracle and BigQuery environments."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import EXAMPLES_DIR, INPUT_DIR, OUTPUT_DIR

DEMO_SCRIPT_PATH = EXAMPLES_DIR / "demo_oracle_script.sql"
DEMO_MAPPING_PATH = EXAMPLES_DIR / "mapping_registry.json"


def load_demo_script() -> str:
    """Return the tracked Oracle demo script."""
    return DEMO_SCRIPT_PATH.read_text(encoding="utf-8")


def load_mapping_registry(path: Path = DEMO_MAPPING_PATH) -> dict[str, str]:
    """Load a lower-cased source-to-target table mapping registry."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(key).lower(): str(value).lower() for key, value in raw.items()}


def bootstrap_mock_environment(run_dir: Path | None = None) -> dict[str, Path]:
    """Create fresh SQLite files that stand in for Oracle and BigQuery."""
    run_dir = run_dir or OUTPUT_DIR / "mock_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    oracle_path = run_dir / "oracle_mock.db"
    bigquery_path = run_dir / "bigquery_mock.db"
    for path in (oracle_path, bigquery_path):
        if path.exists():
            path.unlink()

    _seed_oracle(oracle_path)
    _seed_bigquery(bigquery_path, oracle_path)

    script_path = INPUT_DIR / "demo_oracle_script.sql"
    mapping_path = INPUT_DIR / "mapping_registry.json"
    script_path.write_text(load_demo_script(), encoding="utf-8")
    mapping_path.write_text(
        json.dumps(load_mapping_registry(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "oracle_db": oracle_path,
        "bigquery_db": bigquery_path,
        "script": script_path,
        "mapping": mapping_path,
        "run_dir": run_dir,
    }


def connect_sqlite(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with row dictionaries enabled."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_oracle(path: Path) -> None:
    with closing(connect_sqlite(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE sales_orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                order_date TEXT NOT NULL,
                load_date TEXT NOT NULL,
                amount REAL,
                region TEXT NOT NULL
            );

            CREATE TABLE customer_segments (
                customer_id INTEGER PRIMARY KEY,
                segment TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO sales_orders
                (order_id, customer_id, order_date, load_date, amount, region)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 101, "2026-06-18", "2026-06-18", 120.0, "north"),
                (2, 102, "2026-06-18", "2026-06-18", 90.5, "south"),
                (3, 101, "2026-06-20", "2026-06-20", 140.0, "north"),
                (4, 103, "2026-06-20", "2026-06-20", 220.0, "west"),
                (5, 104, "2026-06-20", "2026-06-20", None, "west"),
                (6, 102, "2026-06-20", "2026-06-20", 70.25, "south"),
            ],
        )
        conn.executemany(
            "INSERT INTO customer_segments (customer_id, segment) VALUES (?, ?)",
            [
                (101, "enterprise"),
                (102, "starter"),
                (103, "enterprise"),
                (104, "trial"),
            ],
        )
        conn.commit()


def _seed_bigquery(path: Path, oracle_path: Path) -> None:
    with closing(connect_sqlite(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE raw_sales_orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                order_date TEXT NOT NULL,
                load_date TEXT NOT NULL,
                amount REAL,
                region TEXT NOT NULL
            );

            CREATE TABLE raw_customer_segments (
                customer_id INTEGER PRIMARY KEY,
                segment TEXT NOT NULL
            );
            """
        )
        escaped = str(oracle_path).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped}' AS oracle_src")
        conn.execute("INSERT INTO raw_sales_orders SELECT * FROM oracle_src.sales_orders")
        conn.execute("INSERT INTO raw_customer_segments SELECT * FROM oracle_src.customer_segments")
        conn.commit()
        conn.execute("DETACH DATABASE oracle_src")
        conn.commit()
