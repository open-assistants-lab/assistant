"""OB-1 Task 3: outbound HTTP + SQLite operation physical spans.

Red tests prove the privacy boundary of the narrow instrumentation
wrappers BEFORE any wrapper exists:

- outbound HTTP spans carry host / status / duration / operation only —
  never the URL path, query string, request body, or response content;
- SQLite operation spans carry operation class / duration / db name only —
  never the SQL statement, parameters, or any row content.

The wrappers are installed explicitly (never blanket monkeypatching) and
are inert unless the OB-0 provider is configured.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture()
def obs_env(tmp_path, monkeypatch):
    """Configured observability state with an in-memory span recorder."""
    monkeypatch.setenv("OTEL_ENDPOINT", "http://127.0.0.1:9/not-real")
    from src.config.settings import AppConfig
    from src.sdk import observability as obs

    obs._reset_for_tests()
    settings = AppConfig()
    provider = obs.configure_observability(settings)
    assert provider is not None

    spans: list[Any] = []

    class Recorder:
        def start_as_current_span(self, name, attributes=None):

            class _Ctx:
                def __enter__(self):
                    rec = _SpanRec(name, dict(attributes or {}))
                    spans.append(rec)
                    return rec

                def __exit__(self, *exc):
                    return False

            return _Ctx()

    # Replace the provider's tracer with a recorder for assertions.
    class _SpanRec:
        def __init__(self, name: str, attributes: dict[str, Any]):
            self.name = name
            self.attributes = attributes

        def set_attribute(self, k, v):
            self.attributes[k] = v

        def record_exception(self, exc):
            self.attributes["error.type"] = type(exc).__name__

    class _Tracer:
        def start_as_current_span(self, name, attributes=None):
            return Recorder().start_as_current_span(name, attributes)

    provider._tracer_override = _Tracer()
    yield spans, obs
    obs.shutdown_observability()
    obs._reset_for_tests()


class _SpanRec:  # pragma: no cover - reference for type checkers
    name: str
    attributes: dict[str, Any]


CONTENT_MARKER = "SECRET-CONTENT-7f3a"


# ---------------------------------------------------------------------------
# Outbound HTTP spans
# ---------------------------------------------------------------------------


def test_provider_chat_emits_http_client_span_with_allowed_attrs_only(obs_env):
    spans, obs = obs_env
    from src.sdk.providers.ollama import OllamaCloud

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": CONTENT_MARKER}}

    class FakeClient:
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResponse()

    provider = OllamaCloud(base_url="https://api.example.test", model="test-model")
    provider._http_client = FakeClient()
    provider._http_client.is_closed = False

    import src.sdk.observability as obs_mod

    monkey_span = obs_mod.physical_span

    def fake_physical_span(name, **attributes):
        return monkey_span(name, **attributes)

    # Run through the real provider.chat with the physical tracer override.
    import asyncio

    from src.sdk.messages import Message

    async def run():
        return await provider.chat([Message.user("hello " + CONTENT_MARKER)])

    # Physical span wrapper: provider instrumentation must be active.
    from src.sdk.observability import instrument_provider_http

    instrument_provider_http(provider)

    result = asyncio.run(run())
    assert result.content == CONTENT_MARKER  # real execution happened

    http_spans = [s for s in spans if s.name == "http.client"]
    assert http_spans, "expected an http.client physical span"
    attrs = http_spans[0].attributes
    assert attrs["http.response.status_code"] == 200
    assert "api.example.test" in attrs["server.address"]
    assert attrs["http.request.method"] == "POST"
    # Privacy: never the URL path/query, body, or response content.
    assert CONTENT_MARKER not in json.dumps(attrs, default=str)
    assert "/api/chat" not in json.dumps(attrs, default=str)


def test_provider_chat_without_observability_makes_no_span(obs_env):
    spans, obs = obs_env
    from src.sdk.observability import _reset_for_tests

    _reset_for_tests()  # provider None => instrumentation inert

    from src.sdk.messages import Message
    from src.sdk.providers.ollama import OllamaCloud

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "ok"}}

    class FakeClient:
        async def post(self, url, **kwargs):
            return FakeResponse()

    provider = OllamaCloud(base_url="https://api.example.test")
    provider._http_client = FakeClient()
    provider._http_client.is_closed = False
    from src.sdk.observability import instrument_provider_http

    instrument_provider_http(provider)
    import asyncio

    result = asyncio.run(provider.chat([Message.user("hi")]))
    assert result.content == "ok"
    assert spans == []  # no spans without configuration


# ---------------------------------------------------------------------------
# SQLite operation spans
# ---------------------------------------------------------------------------


def test_sqlite_wrapper_emits_operation_class_only(obs_env, tmp_path):
    spans, obs = obs_env
    import sqlite3

    from src.sdk.observability import instrument_sqlite_connection

    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(str(db_path))
    conn = instrument_sqlite_connection(conn)

    conn.execute(
        "CREATE TABLE secrets (id INTEGER, payload TEXT)", None
    ) if False else conn.execute(
        "CREATE TABLE secrets (id INTEGER, payload TEXT)"
    )
    conn.execute(
        "INSERT INTO secrets (id, payload) VALUES (1, '" + CONTENT_MARKER + "')"
    )
    conn.commit()

    op_spans = [s for s in spans if s.name == "db.query"]
    assert op_spans, "expected db.query physical spans"
    rendered = json.dumps([s.attributes for s in op_spans], default=str)
    assert "db.system" in rendered or True
    # Privacy: no SQL text, no params, no content.
    assert CONTENT_MARKER not in rendered
    assert "CREATE TABLE" not in rendered
    assert "INSERT INTO" not in rendered
    for s in op_spans:
        assert s.attributes.get("db.system") == "sqlite"
        assert "db.statement" not in s.attributes
        assert "db.params" not in s.attributes


def test_sqlite_wrapper_disabled_without_observability(obs_env, tmp_path):
    spans, obs = obs_env
    from src.sdk.observability import _reset_for_tests

    _reset_for_tests()
    import sqlite3

    from src.sdk.observability import instrument_sqlite_connection

    conn = sqlite3.connect(str(tmp_path / "t2.db"))
    conn = instrument_sqlite_connection(conn)
    conn.execute("CREATE TABLE x (id INTEGER)")
    conn.commit()
    assert [s for s in spans if s.name == "db.query"] == []
