"""Mock-first connection validation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.pipeline_config import BigQueryConfig, GoogleCloudConfig, LLMConfig, OracleConfig, PipelineConfig
from src.secrets import get_env_secret, redact


@dataclass(slots=True)
class ConnectionTestResult:
    """Safe-to-display connection test outcome."""

    name: str
    ok: bool
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a redacted JSON-safe result."""
        return redact(asdict(self))


def test_all_connections(config: PipelineConfig) -> list[ConnectionTestResult]:
    """Run all configured mock connection tests."""
    return [
        test_llm_connection(config.llm),
        test_google_cloud_connection(config.google_cloud),
        test_bigquery_connection(config.bigquery),
        test_oracle_connection(config.oracle),
    ]


def test_llm_connection(config: LLMConfig) -> ConnectionTestResult:
    """Validate LLM endpoint configuration without sending secrets to logs."""
    missing = []
    if not config.base_url:
        missing.append("base_url")
    if not config.model:
        missing.append("model")
    if config.auth_mode == "api_key" and config.api_key_env_var and not get_env_secret(config.api_key_env_var):
        missing.append(config.api_key_env_var)
    ok = not missing
    return ConnectionTestResult(
        name="llm",
        ok=ok,
        message="LLM config is ready for mock use" if ok else f"Missing: {', '.join(missing)}",
        details={
            "base_url": config.base_url,
            "model": config.model,
            "request_format": config.request_format,
            "auth_mode": config.auth_mode,
            "api_key_env_var": config.api_key_env_var,
        },
    )


def test_google_cloud_connection(config: GoogleCloudConfig) -> ConnectionTestResult:
    """Validate Google Cloud project/OAuth metadata for mock use."""
    missing = [field for field, value in {"project_id": config.project_id, "location": config.location}.items() if not value]
    ok = not missing
    return ConnectionTestResult(
        name="google_cloud",
        ok=ok,
        message="Google Cloud config is ready for mock use" if ok else f"Missing: {', '.join(missing)}",
        details=asdict(config),
    )


def test_bigquery_connection(config: BigQueryConfig) -> ConnectionTestResult:
    """Validate BigQuery dataset metadata for mock use."""
    missing = [
        field
        for field, value in {
            "project_id": config.project_id,
            "dataset": config.dataset,
            "location": config.location,
        }.items()
        if not value
    ]
    ok = not missing
    return ConnectionTestResult(
        name="bigquery",
        ok=ok,
        message="BigQuery config is ready for mock use" if ok else f"Missing: {', '.join(missing)}",
        details=asdict(config),
    )


def test_oracle_connection(config: OracleConfig) -> ConnectionTestResult:
    """Validate Oracle connection metadata and environment references for mock use."""
    missing = [
        field
        for field, value in {
            "host": config.host,
            "port": config.port,
            "service_name": config.service_name,
            "default_schema": config.default_schema,
        }.items()
        if not value
    ]
    if config.username_env_var and not get_env_secret(config.username_env_var):
        missing.append(config.username_env_var)
    if config.password_env_var and not get_env_secret(config.password_env_var):
        missing.append(config.password_env_var)
    ok = not missing
    return ConnectionTestResult(
        name="oracle",
        ok=ok,
        message="Oracle config is ready for mock use" if ok else f"Missing: {', '.join(map(str, missing))}",
        details=asdict(config),
    )
