"""Unit tests for src/config.py — the derived-path contract."""

from __future__ import annotations

import json
import re

from src import config, logger
from src.pipeline_config import load_pipeline_config, resolve_output_dir, with_overrides


def test_data_dirs_are_anchored_under_root() -> None:
    assert config.DATA_DIR == config.ROOT_DIR / "data"
    assert config.INPUT_DIR == config.DATA_DIR / "input"
    assert config.OUTPUT_DIR == config.DATA_DIR / "output"
    assert config.LOG_DIR == config.DATA_DIR / "logs"
    assert config.EXAMPLES_DIR == config.ROOT_DIR / "config" / "examples"


def test_root_dir_is_the_repo_root() -> None:
    # config.py lives in src/; ROOT_DIR is its parent's parent.
    assert (config.ROOT_DIR / "src" / "config.py").is_file()


def test_app_name_has_sane_default() -> None:
    assert config.APP_NAME == "Oracle to GCP"


def test_logger_writes_under_the_configured_log_dir() -> None:
    assert logger._LOG_FILE.parent == config.LOG_DIR


def test_pipeline_config_loads_json_and_applies_overrides(tmp_path) -> None:
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(
        json.dumps(
            {
                "run": {"use_local_hub": False, "repair_limit": 1, "output_dir": "data/output/custom"},
                "execution": {"default_input_dir": "data/input/custom_sql", "result_suffix": "_translated"},
                "llm": {"model": "test-model", "temperature": 0.2},
                "trace": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_pipeline_config(config_path)
    overridden = with_overrides(
        loaded,
        use_local_hub=True,
        repair_limit=4,
        trace_enabled=True,
        llm_temperature=0.7,
    )

    assert loaded.run.use_local_hub is False
    assert loaded.run.repair_limit == 1
    assert loaded.execution.default_input_dir == "data/input/custom_sql"
    assert loaded.execution.result_suffix == "_translated"
    assert loaded.llm.model == "test-model"
    assert overridden.run.use_local_hub is True
    assert overridden.run.repair_limit == 4
    assert overridden.trace.enabled is True
    assert overridden.llm.temperature == 0.7
    assert resolve_output_dir("data/output/custom") == config.ROOT_DIR / "data" / "output" / "custom"


def test_every_config_constant_is_read_by_product_code() -> None:
    """A setting only its own definition mentions looks live but does nothing (tests do not count)."""
    product_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in ("src", "app")
        for path in (config.ROOT_DIR / directory).rglob("*.py")
    )
    unused = [
        name
        for name in vars(config)
        if name.isupper() and len(re.findall(rf"\b{name}\b", product_sources)) < 2
    ]

    assert unused == [], f"src/config.py constants no product code reads: {unused}"
