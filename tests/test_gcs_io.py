"""Tests for the shared GCS upload/download helpers (src/gcs_io.py)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from src import gcs_io


@pytest.fixture
def fake_storage_client(monkeypatch):
    """Install a fake google.cloud.storage module and return the fake Client instance."""
    client_instance = MagicMock(name="storage.Client()")
    client_cls = MagicMock(name="storage.Client", return_value=client_instance)

    fake_storage_module = types.ModuleType("google.cloud.storage")
    fake_storage_module.Client = client_cls

    fake_cloud_module = types.ModuleType("google.cloud")
    fake_cloud_module.storage = fake_storage_module

    fake_google_module = types.ModuleType("google")
    fake_google_module.cloud = fake_cloud_module

    monkeypatch.setitem(sys.modules, "google", fake_google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage_module)

    return client_instance


def test_parse_gcs_uri_valid():
    assert gcs_io.parse_gcs_uri("gs://my-bucket/some/path.txt") == ("my-bucket", "some/path.txt")


def test_parse_gcs_uri_invalid():
    with pytest.raises(ValueError, match="Invalid GCS URI"):
        gcs_io.parse_gcs_uri("not-a-gcs-uri")


def test_upload_file_missing_local_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        gcs_io.upload_file(tmp_path / "missing.txt", "gs://bucket/dest.txt")


def test_upload_file_calls_blob_upload(fake_storage_client, tmp_path):
    local = tmp_path / "data.txt"
    local.write_text("hello")
    blob = MagicMock()
    fake_storage_client.bucket.return_value.blob.return_value = blob

    gcs_io.upload_file(local, "gs://my-bucket/dest/data.txt", content_type="text/plain")

    fake_storage_client.bucket.assert_called_once_with("my-bucket")
    fake_storage_client.bucket.return_value.blob.assert_called_once_with("dest/data.txt")
    blob.upload_from_filename.assert_called_once_with(str(local), content_type="text/plain")


def test_upload_string_calls_blob_upload_from_string(fake_storage_client):
    blob = MagicMock()
    fake_storage_client.bucket.return_value.blob.return_value = blob

    gcs_io.upload_string('{"a": 1}', "gs://my-bucket/result.json", content_type="application/json")

    blob.upload_from_string.assert_called_once_with('{"a": 1}', content_type="application/json")


def test_download_file_missing_blob_raises(fake_storage_client, tmp_path):
    blob = MagicMock()
    blob.exists.return_value = False
    fake_storage_client.bucket.return_value.blob.return_value = blob

    with pytest.raises(FileNotFoundError):
        gcs_io.download_file("gs://bucket/missing.txt", tmp_path / "out.txt")


def test_download_file_downloads_when_present(fake_storage_client, tmp_path):
    blob = MagicMock()
    blob.exists.return_value = True
    fake_storage_client.bucket.return_value.blob.return_value = blob
    dest = tmp_path / "nested" / "out.txt"

    gcs_io.download_file("gs://bucket/file.txt", dest)

    blob.download_to_filename.assert_called_once_with(str(dest))
    assert dest.parent.is_dir()


def test_download_bytes_returns_content(fake_storage_client):
    blob = MagicMock()
    blob.exists.return_value = True
    blob.download_as_bytes.return_value = b"payload"
    fake_storage_client.bucket.return_value.blob.return_value = blob

    assert gcs_io.download_bytes("gs://bucket/file.bin") == b"payload"


def test_download_prefix_downloads_matching_blobs(fake_storage_client, tmp_path):
    blob_a = MagicMock(name="blob_a")
    blob_a.name = "reports/2026/a.csv"
    blob_b = MagicMock(name="blob_b")
    blob_b.name = "reports/2026/sub/b.csv"
    dir_marker = MagicMock(name="dir_marker")
    dir_marker.name = "reports/2026/"

    bucket = MagicMock()
    bucket.list_blobs.return_value = [dir_marker, blob_a, blob_b]
    fake_storage_client.bucket.return_value = bucket

    count = gcs_io.download_prefix("gs://bucket/reports/2026", tmp_path / "dest")

    assert count == 2
    bucket.list_blobs.assert_called_once_with(prefix="reports/2026")
    blob_a.download_to_filename.assert_called_once_with(str(tmp_path / "dest" / "a.csv"))
    blob_b.download_to_filename.assert_called_once_with(str(tmp_path / "dest" / "sub" / "b.csv"))


def test_missing_google_cloud_storage_raises_runtime_error(tmp_path):
    local = tmp_path / "f.txt"
    local.write_text("x")
    with pytest.raises(RuntimeError, match="Install google-cloud-storage"):
        gcs_io.upload_file(local, "gs://bucket/f.txt")
