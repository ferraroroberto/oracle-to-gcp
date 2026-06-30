"""Local LLM hub client used as a stateless translation function."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.config import LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS
from src.pipeline_config import LLMConfig
from src.secrets import get_env_secret


@dataclass(slots=True)
class LLMResponse:
    """Result returned by a translation function call."""

    text: str
    provider: str
    error: str = ""
    endpoint: str = ""
    duration_ms: int = 0
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None


class LocalHubClient:
    """Small OpenAI-shape client for the local hub at 127.0.0.1:8000."""

    def __init__(
        self,
        base_url: str = LLM_BASE_URL,
        model: str = LLM_MODEL,
        timeout: float = LLM_TIMEOUT_SECONDS,
        temperature: float = 0,
        system_message: str | None = None,
        user_prompt_template: str | None = None,
        extra_parameters: dict[str, Any] | None = None,
        auth_mode: str = "bearer",
        api_key_env_var: str = "LLM_API_KEY",
        authorization_header_env_var: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.system_message = system_message or (
            "You are a deterministic SQL dialect translator. "
            "Do not invent tables. Return one SQL statement."
        )
        self.user_prompt_template = user_prompt_template or (
            "Translate this Oracle SQL statement to BigQuery Standard SQL. "
            "Return only SQL, no markdown. Use this lower-case table mapping: "
            "{mapping_json}\n\n{oracle_sql}"
        )
        self.extra_parameters = extra_parameters or {}
        self.auth_mode = auth_mode
        self.api_key_env_var = api_key_env_var
        self.authorization_header_env_var = authorization_header_env_var

    @classmethod
    def from_config(cls, config: LLMConfig) -> LocalHubClient:
        """Create a client from disk-backed pipeline config."""
        return cls(
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout_seconds,
            temperature=config.temperature,
            system_message=config.system_message,
            user_prompt_template=config.user_prompt_template,
            extra_parameters=config.extra_parameters,
            auth_mode=config.auth_mode,
            api_key_env_var=config.api_key_env_var,
            authorization_header_env_var=config.authorization_header_env_var,
        )

    def translate(self, oracle_sql: str, mapping: dict[str, str]) -> LLMResponse:
        """Call the local hub once and return the raw text response."""
        mapping_json = json.dumps(mapping, sort_keys=True)
        prompt = self.user_prompt_template.format(
            mapping_json=mapping_json,
            oracle_sql=oracle_sql,
        )
        endpoint = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": self.system_message,
                },
                {"role": "user", "content": prompt},
            ],
            **self.extra_parameters,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": self._authorization_header()},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return LLMResponse(
                text="",
                provider="local-hub",
                error=str(exc),
                endpoint=endpoint,
                duration_ms=_elapsed_ms(started),
                request_payload=payload,
            )

        try:
            text = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            return LLMResponse(
                text="",
                provider="local-hub",
                error=f"unexpected response: {exc}",
                endpoint=endpoint,
                duration_ms=_elapsed_ms(started),
                request_payload=payload,
                response_payload=body,
            )
        return LLMResponse(
            text=_strip_sql_fence(text),
            provider="local-hub",
            endpoint=endpoint,
            duration_ms=_elapsed_ms(started),
            request_payload=payload,
            response_payload=body,
        )

    def _authorization_header(self) -> str:
        if self.authorization_header_env_var:
            value = get_env_secret(self.authorization_header_env_var)
            if value:
                return value
        if self.api_key_env_var:
            value = get_env_secret(self.api_key_env_var)
            if value:
                return f"Bearer {value}"
        return "Bearer local-dummy"


def _strip_sql_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
