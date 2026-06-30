"""Local LLM hub client used as a stateless translation function."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.config import LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS


@dataclass(slots=True)
class LLMResponse:
    """Result returned by a translation function call."""

    text: str
    provider: str
    error: str = ""


class LocalHubClient:
    """Small OpenAI-shape client for the local hub at 127.0.0.1:8000."""

    def __init__(
        self,
        base_url: str = LLM_BASE_URL,
        model: str = LLM_MODEL,
        timeout: float = LLM_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def translate(self, oracle_sql: str, mapping: dict[str, str]) -> LLMResponse:
        """Call the local hub once and return the raw text response."""
        prompt = (
            "Translate this Oracle SQL statement to BigQuery Standard SQL. "
            "Return only SQL, no markdown. Use this lower-case table mapping: "
            f"{json.dumps(mapping, sort_keys=True)}\n\n{oracle_sql}"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a deterministic SQL dialect translator. "
                        "Do not invent tables. Return one SQL statement."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer local-dummy"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return LLMResponse(text="", provider="local-hub", error=str(exc))

        try:
            text = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            return LLMResponse(text="", provider="local-hub", error=f"unexpected response: {exc}")
        return LLMResponse(text=_strip_sql_fence(text), provider="local-hub")


def _strip_sql_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped
