"""Unit tests for src/config.py — the derived-path contract."""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from src import config, logger
from src.pipeline_config import (
    LLM_ENV_OVERRIDES,
    LLMConfig,
    load_pipeline_config,
    with_overrides,
)


def test_data_dirs_are_anchored_under_root() -> None:
    assert config.DATA_DIR == config.ROOT_DIR / "data"
    assert config.INPUT_DIR == config.DATA_DIR / "input"
    assert config.OUTPUT_DIR == config.DATA_DIR / "output"
    assert config.LOG_DIR == config.DATA_DIR / "logs"
    assert config.EXAMPLES_DIR == config.ROOT_DIR / "data" / "examples"


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
    assert config.resolve_under_root("data/output/custom") == config.ROOT_DIR / "data" / "output" / "custom"
    assert config.resolve_under_root(config.ROOT_DIR / "x") == config.ROOT_DIR / "x"


def test_llm_env_vars_override_the_committed_config(monkeypatch) -> None:
    """The committed JSON names every LLM field, so env vars only work as overrides on top of it."""
    monkeypatch.setenv("LLM_BASE_URL", "http://hub.example:9000")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "42")

    effective = load_pipeline_config().llm
    on_disk = load_pipeline_config(apply_env=False).llm

    assert (effective.base_url, effective.model, effective.timeout_seconds) == (
        "http://hub.example:9000",
        "env-model",
        42.0,
    )
    assert on_disk.base_url != effective.base_url
    assert on_disk.model != effective.model
    assert on_disk.timeout_seconds != effective.timeout_seconds


def test_unset_or_empty_llm_env_vars_leave_the_file_values(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "  ")
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)

    assert load_pipeline_config().llm == load_pipeline_config(apply_env=False).llm


def test_invalid_llm_timeout_env_var_names_the_variable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "soon")

    with pytest.raises(ValueError, match="LLM_TIMEOUT_SECONDS"):
        load_pipeline_config()


def test_every_llm_env_override_is_documented_in_the_readme() -> None:
    """The README promises each override; the loader's table and the README must list the same variables."""
    readme = (config.ROOT_DIR / "README.md").read_text(encoding="utf-8")
    llm_fields = {field.name for field in dataclasses.fields(LLMConfig)}

    for env_name, (field_name, _parse) in LLM_ENV_OVERRIDES.items():
        assert f"$env:{env_name}" in readme
        assert field_name in llm_fields


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


def test_only_config_anchors_relative_paths_under_the_repo_root() -> None:
    """A second `is_absolute() else ROOT_DIR / ...` copy would drift from `resolve_under_root`."""
    offenders = [
        str(path.relative_to(config.ROOT_DIR))
        for path in (config.ROOT_DIR / "src").rglob("*.py")
        if path.name != "config.py" and re.search(r"is_absolute\(\)\s+else\s+ROOT_DIR|is_absolute\(\):\s*\n\s*\w+\s*=\s*ROOT_DIR", path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"use config.resolve_under_root instead of re-implementing it in: {offenders}"
