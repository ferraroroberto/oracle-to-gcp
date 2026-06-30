"""Environment-backed secret lookup and redaction helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.config import ROOT_DIR

SECRET_KEYS = ("password", "secret", "token", "api_key", "apikey", "credential", "authorization")


def load_dotenv(path: Path | str | None = None) -> None:
    """Load a simple .env file into the process environment without overriding values."""
    env_path = Path(path) if path is not None else ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env_secret(env_var: str | None) -> str:
    """Return a secret from an environment variable reference."""
    if not env_var:
        return ""
    load_dotenv()
    return os.environ.get(env_var, "")


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys from dictionaries/lists."""
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _is_secret_key(str(key)) and item not in (None, "") else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(marker in lowered for marker in SECRET_KEYS)
