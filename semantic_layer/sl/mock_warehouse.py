"""Deterministic mock warehouses: a *legacy* (Oracle-shaped) and a *cloud*
(BigQuery-shaped) DuckDB file holding the same fictitious retail data.

The cloud side is a copy of the legacy side mid-migration: legacy uses an
UPPERCASE ``DWH`` schema and integer flags, cloud uses lowercase ``analytics``
tables and booleans, and one legacy table (``STORE_TARGETS``) has not been
migrated yet. ``ops.query_history`` on the cloud side stands in for
``INFORMATION_SCHEMA.JOBS_BY_PROJECT`` and feeds the Phase 2 bulk harvest.

All values come from a seeded RNG, so every run (and every test) sees the same
numbers — the committed anchors depend on that.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from semantic_layer.sl.common import Layer, logger

SEED = 20261003
DATA_START = dt.date(2023, 1, 1)
DATA_END = dt.date(2026, 9, 30)
SNAPSHOTS = [
    dt.date(2025, 10, 31), dt.date(2025, 11, 30), dt.date(2025, 12, 31), dt.date(2026, 1, 31),
    dt.date(2026, 2, 28), dt.date(2026, 3, 31), dt.date(2026, 4, 30), dt.date(2026, 5, 31),
    dt.date(2026, 6, 30), dt.date(2026, 7, 31), dt.date(2026, 8, 31), dt.date(2026, 9, 30),
]
CUSTOMER_COUNT = 800

STORES = [
    (1, "Northgate", "Harbor", "North"),
    (2, "Pier Market", "Harbor", "North"),
    (3, "Lighthouse Row", "Harbor", "North"),
    (4, "Oak Hill", "Uplands", "North"),
    (5, "Ridgeview", "Uplands", "North"),
    (6, "Riverside", "Delta", "South"),
    (7, "Mill Lane", "Delta", "South"),
    (8, "Sun Plaza", "Lagoon", "South"),
    (9, "Palm Court", "Lagoon", "South"),
    (10, "Canyon Point", "Mesa", "West"),
    (11, "Red Rock", "Mesa", "West"),
    (12, "Sunset Arcade", "Mesa", "West"),
]

CLOUD_DDL = """
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE analytics.stores (store_id INTEGER, store_name VARCHAR, district VARCHAR, region VARCHAR,
    opened_date DATE);
CREATE TABLE analytics.customers (customer_id INTEGER, signup_date DATE, first_purchase_date DATE,
    first_purchase_store_id INTEGER, home_store_id INTEGER, account_status VARCHAR, is_staff BOOLEAN);
CREATE TABLE analytics.orders (order_id INTEGER, customer_id INTEGER, store_id INTEGER, order_date DATE,
    amount DECIMAL(10, 2), order_status VARCHAR);
CREATE TABLE analytics.customer_monthly (snapshot_date DATE, customer_id INTEGER, home_store_id INTEGER,
    first_purchase_store_id INTEGER, last_purchase_date DATE, account_status VARCHAR, is_staff BOOLEAN,
    orders_12m INTEGER);
CREATE TABLE analytics.loyalty_members (customer_id INTEGER, enrolled_date DATE, tier VARCHAR);
CREATE TABLE ops.query_history (query_id INTEGER, user_name VARCHAR, team VARCHAR, executed_at TIMESTAMP,
    query_text VARCHAR);
"""

LEGACY_DDL = """
CREATE SCHEMA IF NOT EXISTS DWH;
CREATE TABLE DWH.STORES (STORE_ID INTEGER, STORE_NAME VARCHAR, DISTRICT VARCHAR, REGION VARCHAR,
    OPENED_DATE DATE);
CREATE TABLE DWH.CUSTOMERS (CUSTOMER_ID INTEGER, SIGNUP_DATE DATE, FIRST_PURCHASE_DATE DATE,
    FIRST_PURCHASE_STORE_ID INTEGER, HOME_STORE_ID INTEGER, ACCOUNT_STATUS VARCHAR, IS_STAFF INTEGER);
CREATE TABLE DWH.ORDERS (ORDER_ID INTEGER, CUSTOMER_ID INTEGER, STORE_ID INTEGER, ORDER_DATE DATE,
    AMOUNT DECIMAL(10, 2), ORDER_STATUS VARCHAR);
CREATE TABLE DWH.CUSTOMER_MONTHLY (SNAPSHOT_DATE DATE, CUSTOMER_ID INTEGER, HOME_STORE_ID INTEGER,
    FIRST_PURCHASE_STORE_ID INTEGER, LAST_PURCHASE_DATE DATE, ACCOUNT_STATUS VARCHAR, IS_STAFF INTEGER,
    ORDERS_12M INTEGER);
CREATE TABLE DWH.LOYALTY_MEMBERS (CUSTOMER_ID INTEGER, ENROLLED_DATE DATE, TIER VARCHAR);
CREATE TABLE DWH.STORE_TARGETS (STORE_ID INTEGER, TARGET_MONTH DATE, TARGET_AMOUNT DECIMAL(12, 2));
"""


@dataclass
class MockData:
    stores: list[tuple] = field(default_factory=list)
    customers: list[tuple] = field(default_factory=list)
    orders: list[tuple] = field(default_factory=list)
    customer_monthly: list[tuple] = field(default_factory=list)
    loyalty: list[tuple] = field(default_factory=list)
    targets: list[tuple] = field(default_factory=list)
    history: list[tuple] = field(default_factory=list)


def _random_date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, max((end - start).days, 0)))


def _months_back(day: dt.date, months: int) -> dt.date:
    month_index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    # Clamp to the last valid day, matching ADD_MONTHS / DATE_SUB semantics on month ends.
    for candidate in (day.day, 30, 29, 28):
        try:
            return dt.date(year, month, candidate)
        except ValueError:
            continue
    raise ValueError(day)


def generate() -> MockData:
    """Build every mock row from one seeded RNG."""
    rng = random.Random(SEED)
    data = MockData()
    for store_id, name, district, region in STORES:
        data.stores.append((store_id, name, district, region, dt.date(2015 + store_id % 6, 3, 1)))

    order_id = 0
    store_ids = [store[0] for store in STORES]
    store_weights = [3, 2, 2, 2, 1, 3, 2, 2, 1, 2, 1, 1]
    for customer_id in range(1, CUSTOMER_COUNT + 1):
        signup = _random_date(rng, DATA_START, dt.date(2026, 8, 31))
        home_store = rng.choices(store_ids, weights=store_weights)[0]
        roll = rng.random()
        status = "A" if roll < 0.85 else ("P" if roll < 0.90 else "C")
        closed_on = _random_date(rng, signup, DATA_END) if status == "C" else None
        is_staff = rng.random() < 0.02
        # Order volume: a fifth never buy, the rest skew towards a few orders.
        order_total = 0 if rng.random() < 0.2 else min(int(rng.expovariate(1 / 6)) + 1, 30)
        orders: list[tuple] = []
        last_possible = closed_on or DATA_END
        for _ in range(order_total):
            order_id += 1
            order_day = _random_date(rng, signup, last_possible)
            store = home_store if rng.random() < 0.8 else rng.choice(store_ids)
            amount = round(rng.uniform(5, 250), 2)
            order_roll = rng.random()
            order_status = "COMPLETED" if order_roll < 0.90 else ("CANCELLED" if order_roll < 0.96 else "RETURNED")
            orders.append((order_id, customer_id, store, order_day, amount, order_status))
        data.orders.extend(orders)

        completed = sorted((o for o in orders if o[5] == "COMPLETED"), key=lambda o: (o[3], o[0]))
        first_purchase = completed[0][3] if completed else None
        first_store = completed[0][2] if completed else None
        data.customers.append((customer_id, signup, first_purchase, first_store, home_store, status, is_staff))

        for snapshot in SNAPSHOTS:
            if signup > snapshot:
                continue
            done = [o for o in completed if o[3] <= snapshot]
            last_purchase = done[-1][3] if done else None
            window_start = _months_back(snapshot, 12)
            orders_12m = sum(1 for o in done if o[3] > window_start)
            snap_status = "C" if closed_on and closed_on <= snapshot else ("P" if status == "P" else "A")
            data.customer_monthly.append(
                (snapshot, customer_id, home_store, first_store, last_purchase, snap_status, is_staff, orders_12m)
            )

        if first_purchase and rng.random() < 0.3:
            enrolled = _random_date(rng, first_purchase, DATA_END)
            data.loyalty.append((customer_id, enrolled, rng.choice(["BRONZE", "BRONZE", "SILVER", "GOLD"])))

    for store_id in store_ids:
        for snapshot in SNAPSHOTS:
            data.targets.append((store_id, snapshot.replace(day=1), round(rng.uniform(20000, 60000), 2)))

    data.history = _query_history(rng)
    return data


def seed(layer: Layer) -> dict[str, str]:
    """(Re)create both mock warehouse files under the layer root."""
    data = generate()
    paths = {platform: layer.mock_path(platform) for platform in ("legacy", "cloud")}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

    with tempfile.TemporaryDirectory() as scratch, duckdb.connect(str(paths["cloud"])) as con:
        con.execute(CLOUD_DDL)
        _bulk_insert(con, "analytics.stores", data.stores, scratch)
        _bulk_insert(con, "analytics.customers", data.customers, scratch)
        _bulk_insert(con, "analytics.orders", data.orders, scratch)
        _bulk_insert(con, "analytics.customer_monthly", data.customer_monthly, scratch)
        _bulk_insert(con, "analytics.loyalty_members", data.loyalty, scratch)
        # Few rows with multi-line text: a plain parameterised insert is simplest here.
        con.executemany("INSERT INTO ops.query_history VALUES (?, ?, ?, ?, ?)", data.history)

    with tempfile.TemporaryDirectory() as scratch, duckdb.connect(str(paths["legacy"])) as con:
        con.execute(LEGACY_DDL)
        _bulk_insert(con, "DWH.STORES", data.stores, scratch)
        _bulk_insert(con, "DWH.CUSTOMERS", [row[:6] + (int(row[6]),) for row in data.customers], scratch)
        _bulk_insert(con, "DWH.ORDERS", data.orders, scratch)
        _bulk_insert(con, "DWH.CUSTOMER_MONTHLY",
                     [row[:6] + (int(row[6]),) + row[7:] for row in data.customer_monthly], scratch)
        _bulk_insert(con, "DWH.LOYALTY_MEMBERS", data.loyalty, scratch)
        _bulk_insert(con, "DWH.STORE_TARGETS", data.targets, scratch)

    logger.info("✅ Mock warehouses seeded: %s customers, %s orders", len(data.customers), len(data.orders))
    return {platform: str(path) for platform, path in paths.items()}


def _bulk_insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple], scratch: str) -> None:
    """Load rows through a CSV + COPY — DuckDB's executemany is far too slow for ~20k rows."""
    path = Path(scratch) / f"{table}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(["" if v is None else (str(v).lower() if isinstance(v, bool) else v) for v in row])
    con.execute(f"COPY {table} FROM '{path.as_posix()}' (HEADER false, NULLSTR '')")


# --- synthetic query history (Phase 2 bulk harvest input) -------------------

_ACTIVE_12M = (
    "SELECT s.region, COUNT(DISTINCT cm.customer_id) AS active_customers\n"
    "FROM analytics.customer_monthly AS cm\n"
    "JOIN analytics.stores AS s ON s.store_id = cm.home_store_id\n"
    "WHERE cm.snapshot_date = DATE '{snap}' AND cm.account_status = 'A' AND cm.is_staff = FALSE\n"
    "  AND cm.last_purchase_date >= DATE_SUB(cm.snapshot_date, INTERVAL 12 MONTH)\n"
    "GROUP BY s.region"
)
_ACTIVE_6M = (
    "SELECT s.region, COUNT(DISTINCT cm.customer_id) AS active_customers\n"
    "FROM analytics.customer_monthly AS cm\n"
    "JOIN analytics.stores AS s ON s.store_id = cm.home_store_id\n"
    "WHERE cm.snapshot_date = DATE '{snap}'\n"
    "  AND cm.last_purchase_date >= DATE_SUB(cm.snapshot_date, INTERVAL 6 MONTH)\n"
    "GROUP BY s.region"
)
_ACTIVE_3M_STATUS = (
    "SELECT COUNT(DISTINCT cm.customer_id) AS active_customers\n"
    "FROM analytics.customer_monthly AS cm\n"
    "WHERE cm.snapshot_date = DATE '{snap}' AND cm.account_status IN ('A', 'P')\n"
    "  AND cm.last_purchase_date >= DATE_SUB(cm.snapshot_date, INTERVAL 3 MONTH)"
)
_REVENUE_COMPLETED = (
    "SELECT s.store_name, SUM(o.amount) AS revenue\n"
    "FROM analytics.orders AS o\n"
    "JOIN analytics.stores AS s ON s.store_id = o.store_id\n"
    "WHERE o.order_status = 'COMPLETED' AND o.order_date BETWEEN DATE '{start}' AND DATE '{snap}'\n"
    "GROUP BY s.store_name"
)
_REVENUE_ALL = (
    "SELECT s.region, SUM(o.amount) AS revenue\n"
    "FROM analytics.orders AS o\n"
    "JOIN analytics.stores AS s ON s.store_id = o.store_id\n"
    "WHERE o.order_date BETWEEN DATE '{start}' AND DATE '{snap}'\n"
    "GROUP BY s.region"
)
_LOYALTY = (
    "SELECT lm.tier, COUNT(*) AS members\n"
    "FROM analytics.loyalty_members AS lm\n"
    "JOIN analytics.customers AS c ON c.customer_id = lm.customer_id\n"
    "WHERE c.account_status = 'A'\n"
    "GROUP BY lm.tier"
)
_COHORT = (
    "SELECT DATE_TRUNC(c.first_purchase_date, MONTH) AS join_month, COUNT(*) AS new_customers\n"
    "FROM analytics.customers AS c\n"
    "WHERE c.first_purchase_date IS NOT NULL AND c.is_staff = FALSE\n"
    "GROUP BY join_month"
)
_STORE_LIST = "SELECT s.store_id, s.store_name, s.region FROM analytics.stores AS s WHERE s.region = '{region}'"

_HISTORY_PLAN = [
    ("core_analytics", ["avery.lane", "jordan.bell"], _ACTIVE_12M, 14),
    ("marketing", ["sam.ortiz", "riley.chen"], _ACTIVE_6M, 9),
    ("store_ops", ["casey.moreau"], _ACTIVE_3M_STATUS, 4),
    ("finance", ["morgan.reyes", "taylor.kim"], _REVENUE_COMPLETED, 8),
    ("store_ops", ["casey.moreau", "devon.park"], _REVENUE_ALL, 5),
    ("marketing", ["sam.ortiz"], _LOYALTY, 4),
    ("core_analytics", ["avery.lane"], _COHORT, 3),
    ("store_ops", ["devon.park"], _STORE_LIST, 3),
]


def _query_history(rng: random.Random) -> list[tuple]:
    rows: list[tuple] = []
    query_id = 0
    for team, users, template, count in _HISTORY_PLAN:
        for _ in range(count):
            query_id += 1
            snap = rng.choice(SNAPSHOTS[-6:])
            text = template.format(
                snap=snap.isoformat(),
                start=snap.replace(day=1).isoformat(),
                region=rng.choice(["North", "South", "West"]),
            )
            executed = dt.datetime.combine(_random_date(rng, dt.date(2026, 7, 1), dt.date(2026, 9, 30)), dt.time(9))
            rows.append((query_id, rng.choice(users), team, executed, text))
    return rows


def mock_exists(layer: Layer) -> bool:
    return all(layer.mock_path(platform).exists() for platform in ("legacy", "cloud"))


def require_mock(layer: Layer) -> None:
    """Seed lazily so a fresh clone 'just works' for mock-adapter commands."""
    uses_mock = any(layer.platform_config(p).get("adapter") == "mock_duckdb" for p in ("legacy", "cloud"))
    if uses_mock and not mock_exists(layer):
        seed(layer)


def mock_files(layer: Layer) -> list[Path]:
    return [layer.mock_path(platform) for platform in ("legacy", "cloud")]
