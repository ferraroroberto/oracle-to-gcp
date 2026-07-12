"""Tests for the shared BigQuery query/schema/load helpers (src/bigquery_io.py)."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src import bigquery_io


class _FakeRow:
    def __init__(self, data: dict) -> None:
        self._data = data

    def items(self):
        return self._data.items()


@pytest.fixture
def fake_bigquery_client(monkeypatch):
    """Install a fake google.cloud.bigquery module and return the fake Client instance."""
    client_instance = MagicMock(name="bigquery.Client()")
    client_cls = MagicMock(name="bigquery.Client", return_value=client_instance)

    fake_bq_module = types.ModuleType("google.cloud.bigquery")
    fake_bq_module.Client = client_cls
    fake_bq_module.QueryJobConfig = MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw))
    fake_bq_module.ScalarQueryParameter = MagicMock(
        side_effect=lambda name, typ, value: SimpleNamespace(name=name, type_=typ, value=value)
    )
    fake_bq_module.LoadJobConfig = MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw))

    fake_cloud_module = types.ModuleType("google.cloud")
    fake_cloud_module.bigquery = fake_bq_module

    fake_google_module = types.ModuleType("google")
    fake_google_module.cloud = fake_cloud_module

    monkeypatch.setitem(sys.modules, "google", fake_google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", fake_bq_module)

    return client_instance


def test_run_query_returns_rows_as_dicts(fake_bigquery_client):
    fake_bigquery_client.query.return_value.result.return_value = [
        _FakeRow({"id": 1, "name": "a"}),
        _FakeRow({"id": 2, "name": "b"}),
    ]

    rows = bigquery_io.run_query("SELECT * FROM t", project="proj")

    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    args, _ = fake_bigquery_client.query.call_args
    assert args[0] == "SELECT * FROM t"


def test_run_query_builds_scalar_params(fake_bigquery_client):
    fake_bigquery_client.query.return_value.result.return_value = []

    bigquery_io.run_query("SELECT 1", params={"n": 5, "flag": True, "label": "x"})

    _, kwargs = fake_bigquery_client.query.call_args
    types_by_name = {p.name: p.type_ for p in kwargs["job_config"].query_parameters}
    assert types_by_name == {"n": "INT64", "flag": "BOOL", "label": "STRING"}


def test_execute_script_blocks_until_done(fake_bigquery_client):
    bigquery_io.execute_script("CREATE TABLE t (x INT64);")

    fake_bigquery_client.query.assert_called_once_with("CREATE TABLE t (x INT64);")
    fake_bigquery_client.query.return_value.result.assert_called_once()


def test_fetch_table_schema_returns_schema_list(fake_bigquery_client):
    schema = [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
    fake_bigquery_client.get_table.return_value = SimpleNamespace(schema=schema)

    assert bigquery_io.fetch_table_schema("proj.ds.tbl") == schema


def test_load_dataframe_skips_empty(fake_bigquery_client):
    count = bigquery_io.load_dataframe(pd.DataFrame(), "proj.ds.tbl")

    assert count == 0
    fake_bigquery_client.load_table_from_dataframe.assert_not_called()


def test_load_dataframe_loads_rows(fake_bigquery_client):
    df = pd.DataFrame({"a": [1, 2]})
    fake_bigquery_client.load_table_from_dataframe.return_value.result.return_value = None

    count = bigquery_io.load_dataframe(df, "proj.ds.tbl")

    assert count == 2
    fake_bigquery_client.load_table_from_dataframe.assert_called_once()


def test_missing_google_cloud_bigquery_raises_runtime_error():
    with pytest.raises(RuntimeError, match="Install google-cloud-bigquery"):
        bigquery_io.run_query("SELECT 1")


def test_map_pandas_dtype():
    assert bigquery_io.map_pandas_dtype(pd.Series([True, False])) == "BOOL"
    assert bigquery_io.map_pandas_dtype(pd.Series([1, 2, 3])) == "FLOAT64"
    assert bigquery_io.map_pandas_dtype(pd.Series(["a", "b"])) == "STRING"
    assert bigquery_io.map_pandas_dtype(pd.to_datetime(pd.Series(["2026-01-01"]))) == "DATETIME"


def _schema_field(name: str, field_type: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, field_type=field_type)


def test_compare_dataframe_schema_coerces_and_drops_extra_columns():
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "amount": [1.5, 2.5],
            "flag": [1.0, 0.0],
            "extra_col": ["x", "y"],
        }
    )
    bq_schema = [
        _schema_field("id", "INTEGER"),
        _schema_field("amount", "STRING"),
        _schema_field("flag", "BOOL"),
    ]

    all_ok, converted = bigquery_io.compare_dataframe_schema(df, bq_schema)

    assert all_ok is True
    assert list(converted.columns) == ["id", "amount", "flag"]
    assert converted["amount"].astype(str).tolist() == ["1.5", "2.5"]
    assert converted["flag"].tolist() == [True, False]


def test_compare_dataframe_schema_flags_incompatible_type():
    df = pd.DataFrame({"weird": [1.1, 2.2]})
    bq_schema = [_schema_field("weird", "DATE")]

    all_ok, converted = bigquery_io.compare_dataframe_schema(df, bq_schema)

    assert all_ok is False
    assert "weird" in converted.columns


def test_compare_dataframe_schema_lenient_datetime_column():
    df = pd.DataFrame({"snapshot_date": pd.to_datetime(["2026-01-01", "2026-01-01"])})
    bq_schema = [_schema_field("snapshot_date", "DATETIME")]

    all_ok, converted = bigquery_io.compare_dataframe_schema(
        df, bq_schema, lenient_datetime_columns=["snapshot_date"]
    )

    assert all_ok is True
    assert "snapshot_date" in converted.columns
