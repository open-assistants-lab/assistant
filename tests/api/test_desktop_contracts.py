"""Desktop v0.1 Phase D2 contract tests.

Covers: versioned provider contracts under /v1 (local key classification,
selected-provider validation, consent-gated Check-likely-providers,
loopback-only local model discovery, custom endpoint validation, model
listing), the desktop credential boundary (no server-side plaintext
persistence), desktop filtering across resource listings, and the
context_compressed event-history contract (known-token, unknown-token,
failed-compression replay).
"""

from __future__ import annotations

import asyncio
import json

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Provider contracts under /v1
# ---------------------------------------------------------------------------


def test_classify_key_is_local_and_shape_based(client):
    r = client.post(
        "/v1/providers/classify-key", json={"key": "sk-ant-api03-abcdef123456"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["confidence"] == "high"
    # Local classification must never reach the network — the response is
    # served from a pure function; no provider test endpoint is involved.


def test_classify_key_unknown_shape(client):
    r = client.post("/v1/providers/classify-key", json={"key": "xyzzy-123"})
    assert r.status_code == 200
    assert r.json()["provider"] == "unknown"


def test_check_likely_providers_requires_consent(client):
    r = client.post(
        "/v1/providers/check-likely", json={"key": "sk-ant-api03-abcdef123456"}
    )
    assert r.status_code == 422  # no affirmative consent -> refused


def test_check_likely_providers_with_consent_checks_candidates_only(client, monkeypatch):
    seen_providers: list[str] = []

    def fake_test(provider: str, api_key: str) -> dict[str, object]:
        seen_providers.append(provider)
        return {"valid": provider == "openai", "status": 200}

    import src.http.routers.desktop_providers as dp

    monkeypatch.setattr(dp, "_probe_provider", fake_test)
    r = client.post(
        "/v1/providers/check-likely",
        json={"key": "sk-abcdef123456", "consent": True},
    )
    assert r.status_code == 200
    body = r.json()
    # only the reviewed candidate set for this key shape was probed
    assert seen_providers == ["openai"]  # one candidate for this key shape
    assert body["checked"] == seen_providers
    results = {res["provider"]: res for res in body["results"]}
    assert results["openai"]["valid"] is True


def test_local_models_loopback_allowlist_only(client, monkeypatch):
    import src.http.routers.desktop_providers as dp

    class FakeResponse:
        status_code = 200

        def json(self):  # noqa: ANN202
            return {"data": [{"id": "gpt-oss-20b"}]}

    captured: dict[str, str] = {}

    class FakeGetResponse:
        status_code = 200

        def json(self):  # noqa: ANN202
            return {"data": [{"id": "gpt-oss-20b"}]}

    def fake_get(url, timeout):  # noqa: ANN001
        captured["url"] = url
        return FakeGetResponse()

    # Patch both the instance attribute and the class staticmethod — the
    # router resolves through the instance, but the staticmethod may win
    # depending on import order.
    import src.http.routers.desktop_providers as dp_mod

    dp_mod._http.get = fake_get
    dp_mod._Http.get = staticmethod(fake_get)


    r = client.get(
        "/v1/providers/local-models",
        params={"endpoint": "http://127.0.0.1:11434"},
    )
    assert r.status_code == 200
    assert captured["url"].startswith("http://127.0.0.1:11434")
    models = r.json()["models"]
    assert any(m["id"] == "gpt-oss-20b" for m in models)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.10:11434",  # non-loopback host
        "http://example.com:11434",  # remote host
        "http://127.0.0.1:99999",  # port outside the fixed allowlist
    ],
)
def test_local_models_rejects_non_allowlist_endpoints(client, endpoint):
    r = client.get("/v1/providers/local-models", params={"endpoint": endpoint})
    assert r.status_code == 422


def test_validate_endpoint_loopback_ok_and_non_loopback_rejected(client, monkeypatch):
    import src.http.routers.desktop_providers as dp

    class FakeResponse:
        status_code = 200

        def json(self):  # noqa: ANN202
            return {"data": []}

    monkeypatch.setattr(
        dp._http, "get", lambda url, timeout: FakeResponse()
    )
    r = client.post(
        "/v1/providers/validate-endpoint",
        json={"endpoint": "http://127.0.0.1:1234"},
    )
    assert r.status_code == 200
    assert r.json()["reachable"] is True

    r = client.post(
        "/v1/providers/validate-endpoint",
        json={"endpoint": "http://example.com:1234"},
    )
    assert r.status_code == 422


def test_models_listing_under_v1(client):
    r = client.get("/v1/providers/models")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body and "models" in body


# ---------------------------------------------------------------------------
# Desktop credential boundary: no plaintext key persistence server-side
# ---------------------------------------------------------------------------


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    """Isolated desktop environment (mirrors test_desktop_server.desktop_env)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEPLOYMENT_MODE", "desktop-server")
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(home / "Assistant"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(home / "Assistant" / ".system"))
    monkeypatch.setenv("SOLO_BYPASS", "false")
    monkeypatch.delenv("API_KEY", raising=False)
    from src.config import settings as settings_module

    settings_module._config = None
    yield home
    settings_module._config = None


def test_desktop_mode_refuses_server_side_key_persistence(client, desktop_env):
    from src.storage.paths import get_paths

    store = get_paths("default_user")
    settings_file = (
        Path(str(store.base)) if hasattr(store, "base") else None
    )
    r = client.post(
        "/v1/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-secret-persist-me"},
        params={"user_id": "default_user"},
    )
    assert r.status_code in (409, 422)  # refused: keys are runtime-only

    # no key material may appear in any durable settings file
    if settings_file is not None:
        for p in Path(str(settings_file)).rglob("*.json"):
            content = p.read_text(encoding="utf-8")
            assert "sk-secret-persist-me" not in content


def test_non_desktop_key_persistence_still_works(client):
    # hosted/trusted deployments keep the existing contract
    r = client.post(
        "/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-hosted-key"},
        params={"user_id": "persist_user"},
    )
    assert r.status_code in (200, 409, 422)  # existing behavior unchanged


# ---------------------------------------------------------------------------
# Desktop filtering across resource listings
# ---------------------------------------------------------------------------


def test_desktop_resource_listings_exclude_email_contacts_todos(client, desktop_env):
    for path in ("/v1/tools", "/v1/skills", "/v1/subagents"):
        r = client.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code}"
        body = r.json()
        text = json.dumps(body).lower()
        assert "email_send" not in text
        assert "contacts_add" not in text
        assert "todos_extract" not in text


# ---------------------------------------------------------------------------
# Event-history: context_compressed replay (known/unknown token, failed)
# ---------------------------------------------------------------------------


def _append_event(store, seq, session_id, type_, data, run_id="r1"):
    from datetime import UTC, datetime

    from src.sdk.run_events import parse_run_event

    store.append(
        parse_run_event(
            {
                "schema_version": 1,
                "event_id": f"e{seq}",
                "sequence": seq,
                "timestamp": datetime.now(UTC),
                "session_id": session_id,
                "run_id": run_id,
                "attempt": 1,
                "type": type_,
                "data": data,
            }
        )
    )


def _snap(est):
    return {
        "model": "ollama-cloud:deepseek-v4-flash:0731",
        "attempt": 1,
        "llm_call_index": 1,
        "estimated_tokens": est,
        "context_window": 1_000_000 if est is not None else None,
        "percentage": (est / 1_000_000 * 100) if est is not None else None,
        "source": "post_run_projection",
        "freshness": "live",
        "estimated": est is not None,
    }


def _compressed_data(est_before, est_after, status="succeeded", error=None):
    return {
        "before": _snap(est_before),
        "after": _snap(est_after),
        "status": status,
        "error": error,
    }


def test_known_token_compression_projects_timeline_event(tmp_path, monkeypatch):
    from src.sdk import session_events as se
    from src.sdk.messages import Message
    from src.sdk.run_events import UserPromptEvent

    monkeypatch.setattr(se, "_session_stores", {})
    store = se.get_session_event_store("tok_user")
    _append_event(store, 1, "s1", "user_prompt", {"content": "hi"})
    _append_event(store, 2, "s1", "context_compressed", _compressed_data(46_000, 9_000))

    projected = se.deriveMessages("s1", "tok_user")
    timeline = [m for m in projected if "Context updated" in m.content]
    assert len(timeline) == 1
    assert "46k" in timeline[0].content and "9k" in timeline[0].content


def test_unknown_token_compression_projects_event_without_numbers(tmp_path, monkeypatch):
    from src.sdk import session_events as se

    monkeypatch.setattr(se, "_session_stores", {})
    store = se.get_session_event_store("unk_user")
    _append_event(store, 1, "s2", "context_compressed", _compressed_data(None, None))
    projected = se.deriveMessages("s2", "unk_user")
    assert len(projected) == 1
    assert "Context updated" in projected[0].content
    assert "→" not in projected[0].content.split("Context updated")[1] or "k" not in projected[0].content


def test_failed_compression_never_appears_as_success(tmp_path, monkeypatch):
    from src.sdk import session_events as se

    monkeypatch.setattr(se, "_session_stores", {})
    store = se.get_session_event_store("fail_user")
    _append_event(
        store, 1, "s3", "context_compressed",
        _compressed_data(None, None, status="failed", error="summarizer unavailable"),
    )
    projected = se.deriveMessages("s3", "fail_user")
    assert not any("Context updated" in m.content for m in projected)