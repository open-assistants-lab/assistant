"""Issue #10: per-request provider_options (reasoning controls) via /v1/message.

- Request provider_options reach RunConfig/provider.chat for the matching provider
- Unknown option keys -> 422 (provider allowlist; no arbitrary JSON to providers)
- Absent field -> unchanged behavior; title defaults still work
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def capture(monkeypatch):
    """Fake cached provider capturing chat kwargs; loop-level pass-through."""
    from src.sdk.messages import Message

    captured: dict[str, object] = {}

    class FakeProvider:
        async def chat(self, **kwargs):
            captured.update(kwargs)
            return Message.assistant("ok")

    monkeypatch.setattr(
        "src.sdk.providers.factory.get_cached_model_provider",
        lambda *a, **k: FakeProvider(),
    )
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings",
        lambda user_id: None,
    )
    return captured


def _post(client, **extra):
    body = {"message": "hi", "model": "ollama-cloud:deepseek-v4-flash:0731"}
    body.update(extra)
    return client.post("/v1/message", json=body)


def test_provider_options_reach_provider_chat(client, monkeypatch, capture):
    # Loop-level: RunConfig.provider_options must carry the request options.
    seen: dict[str, object] = {}
    from src.sdk import loop as loop_mod

    orig = loop_mod.AgentLoop._run_impl

    async def spy_run_impl(self, messages):
        seen["po"] = dict(self.run_config.provider_options or {})
        return await orig(self, messages)

    monkeypatch.setattr(loop_mod.AgentLoop, "_run_impl", spy_run_impl)

    resp = _post(
        client,
        provider_options={"ollama-cloud": {"think": False}},
    )
    assert resp.status_code == 200
    assert seen["po"] == {"ollama-cloud": {"think": False}}


def test_unknown_provider_option_keys_rejected_422(client):
    resp = _post(
        client,
        provider_options={"ollama-cloud": {"bogus_key": 1}},
    )
    assert resp.status_code == 422
    assert "think" in str(resp.json())  # allowed keys listed in the error


def test_absent_provider_options_unchanged(client, capture):
    resp = _post(client)
    assert resp.status_code == 200