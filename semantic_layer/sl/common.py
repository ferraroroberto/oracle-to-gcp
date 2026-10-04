"""Shared paths, config loading and YAML/JSON helpers for the semantic layer.

Everything in ``semantic_layer`` resolves paths from one *layer root* — by
default the ``semantic_layer/`` package directory itself, overridable with the
CLI's ``--root`` so tests (and a ported copy) can run against another tree.
Nothing here imports from the host repo's ``src/``: the folder must lift out
as one unit.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

KINDS = ("entity", "metric", "dimension", "filter", "relationship", "template", "anchor")
KIND_DIRS = {
    "entity": "entities",
    "metric": "metrics",
    "dimension": "dimensions",
    "filter": "filters",
    "relationship": "relationships",
    "template": "templates",
    "anchor": "anchors",
}
PLATFORMS = ("legacy", "cloud")

DEFAULT_CONFIG: dict[str, Any] = {
    "platforms": {
        "legacy": {"adapter": "mock_duckdb", "dialect": "oracle", "mock_path": ".mock/legacy.duckdb"},
        "cloud": {"adapter": "mock_duckdb", "dialect": "bigquery", "mock_path": ".mock/cloud.duckdb"},
    },
    "execution": {"allow_execute": True, "max_result_rows": 500},
    "evals": {"thresholds": {}},
    "phase2": {"bulk_harvest": False, "full_checks": False, "navigator": False, "osi_export": False},
}

logger = logging.getLogger("semantic_layer")


class LayerError(Exception):
    """A user-facing error: the CLI prints the message and exits non-zero."""


@dataclass(frozen=True)
class Layer:
    """Resolved paths and configuration for one semantic-layer tree."""

    root: Path
    config: dict[str, Any]

    @property
    def domains(self) -> Path:
        return self.root / "domains"

    @property
    def catalog(self) -> Path:
        return self.root / "catalog"

    @property
    def harvest(self) -> Path:
        return self.root / "harvest"

    @property
    def queries(self) -> Path:
        return self.root / "queries"

    @property
    def evals(self) -> Path:
        return self.root / "evals"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def schema(self) -> Path:
        # Schemas are part of the tool, not the content: always read them from
        # the package so a content-only root (tests, a clean bootstrap) works.
        local = self.root / "schema"
        return local if local.is_dir() else PACKAGE_ROOT / "schema"

    def platform_config(self, platform: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise LayerError(f"Unknown platform '{platform}' (expected one of {', '.join(PLATFORMS)})")
        return self.config["platforms"][platform]

    def dialect(self, platform: str) -> str:
        return str(self.platform_config(platform)["dialect"])

    def mock_path(self, platform: str) -> Path:
        return self.root / self.platform_config(platform).get("mock_path", f".mock/{platform}.duckdb")

    def phase2_enabled(self, module: str) -> bool:
        return bool(self.config.get("phase2", {}).get(module, False))

    def require_phase2(self, module: str, force: bool = False) -> None:
        """Refuse a Phase 2 command unless its flag is on (or ``--force``)."""
        if force or self.phase2_enabled(module):
            return
        raise LayerError(
            f"Phase 2 module '{module}' is disabled (config.json → phase2.{module} = false). "
            f"Turn it on when its trigger is hit (see README 'Phase 2'), or pass --force for a one-off run."
        )


def load_layer(root: str | Path | None = None, config_path: str | Path | None = None) -> Layer:
    """Resolve the layer root and merge its ``config.json`` over the defaults."""
    root_path = Path(root).resolve() if root else PACKAGE_ROOT
    cfg_path = Path(config_path) if config_path else root_path / "config.json"
    config = copy.deepcopy(DEFAULT_CONFIG)
    if cfg_path.exists():
        _deep_merge(config, json.loads(cfg_path.read_text(encoding="utf-8")))
    return Layer(root=root_path, config=config)


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value: Any) -> dt.date | None:
    """Coerce a YAML date / ISO string / datetime into a ``date``; ``None`` if absent."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def resolve_today(value: str | None) -> dt.date:
    """``--today`` override for reproducible checks; defaults to the real date."""
    return dt.date.fromisoformat(value) if value else dt.date.today()


def month_end(period: str) -> dt.date:
    """``'2026-08'`` → ``2026-08-31``: snapshot tables are keyed by month end."""
    year, month = (int(part) for part in period.split("-")[:2])
    first_next = dt.date(year + (month // 12), month % 12 + 1, 1)
    return first_next - dt.timedelta(days=1)


def emit(text: str = "") -> None:
    """Write CLI output. The CLI's stdout *is* the product (JSON/markdown the
    agent reads), so this is deliberate output, not diagnostic logging."""
    sys.stdout.write(text + "\n")


def to_jsonable(value: Any) -> Any:
    """Normalise DB values (dates, decimals) so results compare and serialise cleanly."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal | float):
        number = float(value)
        return int(number) if number.is_integer() else round(number, 6)
    return value
