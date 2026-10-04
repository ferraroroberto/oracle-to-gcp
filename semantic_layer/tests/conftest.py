"""Fixtures: every test works on a throw-away copy of the layer, never the committed tree.

The mock warehouses are seeded once per session and copied into each test's
layer root (seeding takes seconds; copying is instant).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from semantic_layer.sl import cli, mock_warehouse
from semantic_layer.sl.common import PACKAGE_ROOT, Layer, load_layer

CONTENT = ("domains", "catalog", "queries", "harvest", "evals", "build", "config.json")
RUNTIME = shutil.ignore_patterns("navigator.html", "health.json", "issues", "export", "bulk")


@pytest.fixture(scope="session")
def seeded_mock(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mock")
    shutil.copy(PACKAGE_ROOT / "config.json", root / "config.json")
    mock_warehouse.seed(load_layer(root))
    return root / ".mock"


@pytest.fixture
def layer_root(tmp_path: Path, seeded_mock: Path) -> Path:
    root = tmp_path / "semantic_layer"
    root.mkdir()
    for name in CONTENT:
        source = PACKAGE_ROOT / name
        if source.is_dir():
            shutil.copytree(source, root / name, ignore=RUNTIME)
        elif source.exists():
            shutil.copy(source, root / name)
    shutil.copytree(seeded_mock, root / ".mock")
    return root


@pytest.fixture
def layer(layer_root: Path) -> Layer:
    return load_layer(layer_root)


@pytest.fixture
def sl(layer_root: Path, capsys: pytest.CaptureFixture[str]) -> Callable[..., tuple[int, str]]:
    """Run the CLI against the test layer: ``code, out = sl("validate")``."""

    def run(*args: str) -> tuple[int, str]:
        capsys.readouterr()
        code = cli.main([*args, "--root", str(layer_root)])
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    return run


def enable_phase2(layer_root: Path, **flags: bool) -> None:
    path = layer_root / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["phase2"].update(flags or {k: True for k in config["phase2"]})
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def execute(layer_root: Path, platform: str, sql: str) -> None:
    """Mutate a mock warehouse (drift / divergence scenarios)."""
    with duckdb.connect(str(layer_root / ".mock" / f"{platform}.duckdb")) as con:
        con.execute(sql)
