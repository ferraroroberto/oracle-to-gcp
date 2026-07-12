"""Google Cloud Storage upload/download helpers, ADC-authenticated.

Real (non-mock) GCS client wiring. ``google-cloud-storage`` is imported
lazily inside each function so the mock pipeline never requires it to be
installed — same convention as ``unit_test/schema_compatibility_audit.py``'s
Oracle/BigQuery adapters. Consolidates upload/download logic previously
duplicated across sibling fleet repos (see issue #17).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_GCS_URI_RE = re.compile(r"^gs://([^/]+)/(.+)$")


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/path`` URI into ``(bucket_name, blob_name)``."""
    match = _GCS_URI_RE.match(gcs_uri)
    if not match:
        raise ValueError(f"Invalid GCS URI: {gcs_uri!r} (expected gs://bucket/path)")
    return match.group(1), match.group(2)


def _client():
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError("Install google-cloud-storage to use the GCS helpers") from exc
    return storage.Client()


def upload_file(local_path: str | Path, gcs_uri: str, content_type: str | None = None) -> None:
    """Upload a local file to GCS at ``gcs_uri``."""
    local_path = Path(local_path)
    if not local_path.is_file():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    blob = _client().bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(str(local_path), content_type=content_type)
    log.info("Uploaded %s to %s", local_path, gcs_uri)


def upload_string(content: str, gcs_uri: str, content_type: str = "text/plain") -> None:
    """Upload in-memory string content to GCS at ``gcs_uri``."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    blob = _client().bucket(bucket_name).blob(blob_name)
    blob.upload_from_string(content, content_type=content_type)
    log.info("Uploaded string content to %s", gcs_uri)


def download_file(gcs_uri: str, local_path: str | Path) -> None:
    """Download a single GCS blob to ``local_path``."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    blob = _client().bucket(bucket_name).blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"GCS object not found: {gcs_uri}")
    blob.download_to_filename(str(local_path))
    log.info("Downloaded %s to %s", gcs_uri, local_path)


def download_bytes(gcs_uri: str) -> bytes:
    """Download a GCS blob's content as bytes."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    blob = _client().bucket(bucket_name).blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"GCS object not found: {gcs_uri}")
    content = blob.download_as_bytes()
    log.info("Downloaded %s (%d bytes)", gcs_uri, len(content))
    return content


def download_prefix(gcs_prefix_uri: str, local_dir: str | Path) -> int:
    """Download every blob under a ``gs://`` prefix into ``local_dir``,
    preserving relative paths. Returns the number of files downloaded."""
    bucket_name, prefix = parse_gcs_uri(gcs_prefix_uri.rstrip("/"))
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    bucket = _client().bucket(bucket_name)
    count = 0
    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.endswith("/"):
            continue
        relative = blob.name[len(prefix):].lstrip("/")
        if not relative:
            continue
        dest = local_dir / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        count += 1
        log.info("Downloaded %s to %s", blob.name, dest)
    log.info("Downloaded %d files from %s to %s", count, gcs_prefix_uri, local_dir)
    return count
