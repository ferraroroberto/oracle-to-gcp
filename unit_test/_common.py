"""Shared boilerplate for the standalone ``unit_test/`` audit modules.

``schema_compatibility_audit.py``, ``query_cost_audit.py``, and
``query_optimization_loop.py`` are each invoked as independent CLI scripts,
but share the same config-loading and logging-setup boilerplate. Keep that
boilerplate here instead of copy-pasting it across the three modules.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def configure_logging(config: dict[str, Any]) -> None:
    level_name = str(config.get("level", "INFO")).upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
