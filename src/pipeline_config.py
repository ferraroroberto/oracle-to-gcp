"""Disk-backed pipeline configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from src.config import LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS, ROOT_DIR

DEFAULT_PIPELINE_CONFIG_PATH = ROOT_DIR / "config" / "pipeline.json"


@dataclass(slots=True)
class RunOptions:
    """Configurable defaults for one pipeline run."""

    use_local_hub: bool = True
    simulate_repair_path: bool = True
    repair_limit: int = 3
    output_dir: str = "data/output/mock_run"


@dataclass(slots=True)
class LLMConfig:
    """Configurable OpenAI-shape local hub request settings."""

    base_url: str = LLM_BASE_URL
    model: str = LLM_MODEL
    timeout_seconds: float = LLM_TIMEOUT_SECONDS
    temperature: float = 0
    system_message: str = (
        "You are a deterministic SQL dialect translator. "
        "Do not invent tables. Return one SQL statement."
    )
    user_prompt_template: str = (
        "Translate this Oracle SQL statement to BigQuery Standard SQL. "
        "Return only SQL, no markdown. Use this lower-case table mapping: "
        "{mapping_json}\n\n{oracle_sql}"
    )
    extra_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TraceConfig:
    """Configurable trace capture behavior."""

    enabled: bool = True
    verbose: bool = True
    capture_llm_payloads: bool = True
    capture_query_results: bool = True
    max_query_rows: int = 50


@dataclass(slots=True)
class PipelineConfig:
    """Complete pipeline configuration loaded from JSON."""

    run: RunOptions = field(default_factory=RunOptions)
    llm: LLMConfig = field(default_factory=LLMConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    path: str = ""


def load_pipeline_config(path: Path | str | None = None) -> PipelineConfig:
    """Load pipeline configuration from JSON, falling back to defaults."""
    config_path = Path(path) if path is not None else DEFAULT_PIPELINE_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = ROOT_DIR / config_path
    if not config_path.exists():
        return PipelineConfig(path=str(config_path))

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return PipelineConfig(
        run=RunOptions(**raw.get("run", {})),
        llm=LLMConfig(**raw.get("llm", {})),
        trace=TraceConfig(**raw.get("trace", {})),
        path=str(config_path),
    )


def config_to_dict(config: PipelineConfig) -> dict[str, Any]:
    """Return a JSON-serializable config dictionary."""
    data = asdict(config)
    if not data["path"]:
        data.pop("path")
    return data


def resolve_output_dir(output_dir: str) -> Path:
    """Resolve an output directory from config relative to the repo root."""
    path = Path(output_dir)
    return path if path.is_absolute() else ROOT_DIR / path


def with_overrides(
    config: PipelineConfig,
    *,
    use_local_hub: bool | None = None,
    simulate_repair_path: bool | None = None,
    repair_limit: int | None = None,
    output_dir: str | None = None,
    trace_enabled: bool | None = None,
    trace_verbose: bool | None = None,
    trace_capture_llm_payloads: bool | None = None,
    trace_capture_query_results: bool | None = None,
    trace_max_query_rows: int | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    llm_timeout_seconds: float | None = None,
    llm_temperature: float | None = None,
    llm_system_message: str | None = None,
    llm_user_prompt_template: str | None = None,
    llm_extra_parameters: dict[str, Any] | None = None,
) -> PipelineConfig:
    """Return a copy of ``config`` with one-run overrides applied."""
    run_updates: dict[str, Any] = {}
    if use_local_hub is not None:
        run_updates["use_local_hub"] = use_local_hub
    if simulate_repair_path is not None:
        run_updates["simulate_repair_path"] = simulate_repair_path
    if repair_limit is not None:
        run_updates["repair_limit"] = repair_limit
    if output_dir is not None:
        run_updates["output_dir"] = output_dir

    trace_updates: dict[str, Any] = {}
    if trace_enabled is not None:
        trace_updates["enabled"] = trace_enabled
    if trace_verbose is not None:
        trace_updates["verbose"] = trace_verbose
    if trace_capture_llm_payloads is not None:
        trace_updates["capture_llm_payloads"] = trace_capture_llm_payloads
    if trace_capture_query_results is not None:
        trace_updates["capture_query_results"] = trace_capture_query_results
    if trace_max_query_rows is not None:
        trace_updates["max_query_rows"] = trace_max_query_rows

    llm_updates: dict[str, Any] = {}
    if llm_base_url is not None:
        llm_updates["base_url"] = llm_base_url
    if llm_model is not None:
        llm_updates["model"] = llm_model
    if llm_timeout_seconds is not None:
        llm_updates["timeout_seconds"] = llm_timeout_seconds
    if llm_temperature is not None:
        llm_updates["temperature"] = llm_temperature
    if llm_system_message is not None:
        llm_updates["system_message"] = llm_system_message
    if llm_user_prompt_template is not None:
        llm_updates["user_prompt_template"] = llm_user_prompt_template
    if llm_extra_parameters is not None:
        llm_updates["extra_parameters"] = llm_extra_parameters

    return replace(
        config,
        run=replace(config.run, **run_updates),
        trace=replace(config.trace, **trace_updates),
        llm=replace(config.llm, **llm_updates),
    )
