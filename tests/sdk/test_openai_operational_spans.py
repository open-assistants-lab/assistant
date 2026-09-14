"""OB-1: OpenAI-compatible requests use safe operational telemetry only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.sdk.messages import Message

CONTENT_MARKER = "OPENAI-SECRET-CONTENT-7f3a"


@pytest.fixture()
def obs_env(monkeypatch):
    monkeypatch.setenv("OTEL_ENDPOINT", "http://127.0.0.1:9/not-real")
    from src.config.settings import AppConfig
    from src.sdk import observability as obs

    obs._reset_for_tests()
    assert obs.configure_observability(AppConfig()) is not None
    spans: list[Any] = []

    class Span:
        def __init__(self, name: str, attributes: dict[str, Any]) -> None:
            self.name = name
            self.attributes = attributes

        def set_attribute(self, key: str, value: Any) -> None:
            self.attributes[key] = value

    class Context:
        def __init__(self, name: str, attributes: dict[str, Any]) -> None:
            self._span = Span(name, attributes)

        def __enter__(self):
            spans.append(self._span)
            return self._span

        def __exit__(self, *args: Any) -> bool:
            return False

    class Tracer:
        def start_as_current_span(self, name: str, attributes=None):
            return Context(name, dict(attributes or {}))

    operational_provider = obs._state["operational_telemetry_provider"]
    assert operational_provider is not None
    operational_provider._tracer_override = Tracer()
    yield spans, obs
    obs.shutdown_observability()
    obs._reset_for_tests()


def _fake_openai(monkeypatch, *, status_code: int = 201):
    """Patch the SDK client with a real httpx transport at its request seam."""
    import src.sdk.providers.openai as openai_provider

    class FakeCompletions:
        def __init__(self, client: httpx.AsyncClient) -> None:
            self._client = client

        async def create(self, **params: Any) -> Any:
            response = await self._client.post(
                "/v1/chat/completions?api_key=QUERY-SECRET",
                json={"prompt": CONTENT_MARKER},
            )
            if params.get("stream"):
                async def stream():
                    yield SimpleNamespace(choices=[])

                return stream()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
                status_code=response.status_code,
                usage=None,
            )

    class FakeAsyncOpenAI:
        instances: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                assert CONTENT_MARKER in request.content.decode()
                return httpx.Response(status_code, request=request, content=CONTENT_MARKER)

            self._client = httpx.AsyncClient(
                base_url="https://user:password@api.openai-example.test",
                transport=httpx.MockTransport(handler),
            )
            self.chat = SimpleNamespace(completions=FakeCompletions(self._client))
            type(self).instances.append(self)

        async def close(self) -> None:
            await self._client.aclose()

    monkeypatch.setattr(openai_provider, "AsyncOpenAI", FakeAsyncOpenAI)
    return FakeAsyncOpenAI


def _assert_safe_http_span(spans: list[Any], status_code: int) -> None:
    attrs = next(span.attributes for span in spans if span.name == "http.client")
    assert attrs == {
        "http.request.method": "POST",
        "server.address": "api.openai-example.test",
        "http.response.status_code": status_code,
        "duration_ms": pytest.approx(attrs["duration_ms"]),
    }
    rendered = json.dumps(attrs, default=str)
    for forbidden in (
        "user",
        "password",
        "QUERY-SECRET",
        "/v1/chat/completions",
        CONTENT_MARKER,
        "error.type",
    ):
        assert forbidden not in rendered


def test_openai_chat_production_wiring_emits_safe_operational_span(obs_env, monkeypatch):
    spans, _obs = obs_env
    _fake_openai(monkeypatch)
    from src.sdk.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url="https://ignored.example.test")
    result = asyncio.run(provider.chat([Message.user(CONTENT_MARKER)]))

    assert result.content == "ok"
    _assert_safe_http_span(spans, 201)
    asyncio.run(provider.aclose())


def test_openai_stream_production_wiring_emits_safe_operational_span(obs_env, monkeypatch):
    spans, _obs = obs_env
    _fake_openai(monkeypatch, status_code=202)
    from src.sdk.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url="https://ignored.example.test")

    async def collect() -> list[Any]:
        return [chunk async for chunk in provider.chat_stream([Message.user(CONTENT_MARKER)])]

    asyncio.run(collect())
    _assert_safe_http_span(spans, 202)
    asyncio.run(provider.aclose())


def test_openai_production_wiring_is_inert_without_endpoint(monkeypatch):
    from src.sdk import observability as obs

    obs._reset_for_tests()
    fake_openai = _fake_openai(monkeypatch)
    from src.sdk.providers.openai import OpenAIProvider

    provider = OpenAIProvider()
    asyncio.run(provider.chat([Message.user("hello")]))

    raw_client = fake_openai.instances[0]._client
    assert raw_client.event_hooks["request"] == []
    assert raw_client.event_hooks["response"] == []
    asyncio.run(provider.aclose())
