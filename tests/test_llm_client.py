"""Unit tests for src/llm_client.py — request-format dispatch."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest

from src import llm_client as llm_client_module
from src.llm_client import LocalHubClient


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> dict[str, Any]:
    """Patch urlopen to return ``body`` and capture the outgoing request."""
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ARG001
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        yield _FakeResponse(body)

    monkeypatch.setattr(llm_client_module.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_openai_chat_is_the_default_request_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": "SELECT 1"}}]},
    )
    client = LocalHubClient(base_url="http://127.0.0.1:8000", model="test-model")

    result = client.complete("system", "user")

    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert "system" not in captured["payload"]
    assert result.text == "SELECT 1"
    assert result.error == ""


def test_anthropic_messages_request_format_hits_v1_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(
        monkeypatch,
        {"content": [{"type": "text", "text": "SELECT 1"}]},
    )
    client = LocalHubClient(
        base_url="http://127.0.0.1:8000",
        model="test-model",
        request_format="anthropic_messages",
    )

    result = client.complete("system", "user")

    assert captured["url"] == "http://127.0.0.1:8000/v1/messages"
    assert captured["payload"]["system"] == "system"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "user"}]
    assert captured["payload"]["max_tokens"] == llm_client_module.LLM_MAX_TOKENS
    assert result.text == "SELECT 1"
    assert result.error == ""


def test_anthropic_messages_extra_parameters_override_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(
        monkeypatch,
        {"content": [{"type": "text", "text": "ok"}]},
    )
    client = LocalHubClient(
        base_url="http://127.0.0.1:8000",
        request_format="anthropic_messages",
        extra_parameters={"max_tokens": 128},
    )

    client.complete("system", "user")

    assert captured["payload"]["max_tokens"] == 128


def test_anthropic_messages_unexpected_response_shape_is_reported_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, {"unexpected": "shape"})
    client = LocalHubClient(base_url="http://127.0.0.1:8000", request_format="anthropic_messages")

    result = client.complete("system", "user")

    assert result.text == ""
    assert "unexpected response" in result.error


def test_from_config_propagates_request_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(
        monkeypatch,
        {"content": [{"type": "text", "text": "ok"}]},
    )
    from src.pipeline_config import LLMConfig

    config = LLMConfig(request_format="anthropic_messages")
    client = LocalHubClient.from_config(config)

    client.complete("system", "user")

    assert captured["url"].endswith("/v1/messages")
