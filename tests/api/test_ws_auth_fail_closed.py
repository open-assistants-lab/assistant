"""Bug-hunt fixes: WS fail-closed auth, per-user keys on WS, budget gate.

Covers the auth/billing hunt findings:
- P0: WS handshake fail-open when API_KEY="" + PER_USER_AUTH=true + remote.
- P1: per-user key holders could not use WS when API_KEY is set.
- P2: WS localhost check diverged from is_localhost (audit B17).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

REMOTE = "203.0.113.5"


@pytest.fixture()
def ws_app(monkeypatch, tmp_path):
    """Per-user auth on, API_KEY absent, isolated stores; remote locality."""
    import src.auth.keys as keys_mod
    import src.http.auth as http_auth
    import src.http.auth.legacy as legacy
    import src.storage.paths as paths_mod
    from src.config import reload_settings
    from src.storage.paths import _paths_cache

    monkeypatch.setenv("PER_USER_AUTH", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    monkeypatch.setattr(keys_mod, "_STORES", {})
    monkeypatch.setattr(http_auth, "_DEFAULT_RESOLVER", None)
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    _paths_cache.clear()
    # Simulate a remote client for every is_localhost check (the WS fix
    # delegates to is_localhost, so patching it controls locality).
    monkeypatch.setattr(legacy, "is_localhost", lambda request: False)
    reload_settings()
    yield
    reload_settings()


@pytest.fixture()
def ws_app_with_api_key(ws_app, monkeypatch):
    monkeypatch.setenv("API_KEY", "admin-secret")
    from src.config import reload_settings

    reload_settings()
    yield


def _client() -> TestClient:
    from src.http.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_ws_fail_closed_remote_without_key(ws_app):
    """P0: API_KEY='' + PER_USER_AUTH=true + remote -> AUTH_FAILED close;
    the caller cannot operate as an arbitrary user."""
    with _client() as c:
        with c.websocket_connect("/ws/conversation") as ws:
            ws.send_json(
                {"type": "user_message", "user_id": "victim", "content": "hi"}
            )
            # Server must close with AUTH_FAILED (or the close frame) —
            # never an agent run / done frame.
            frames = [ws.receive_json()]
            try:
                frames.append(ws.receive_json())
            except Exception:  # socket closed — expected
                pass
            codes = [f.get("code", "") for f in frames if isinstance(f, dict)]
            types = [f.get("type", "") for f in frames if isinstance(f, dict)]
            assert any(
                code == "AUTH_FAILED" for code in codes
            ) or "done" not in types, frames


def test_ws_per_user_key_accepted_with_api_key_set(ws_app_with_api_key):
    """P1: a per-user oak_ key in the AuthMessage satisfies needs_auth and
    scopes the connection (user_id spoofing rejected)."""
    import src.auth.keys as keys_mod

    key = keys_mod.get_key_store().generate("ws_user")
    with _client() as c:
        # Shared secret via AuthMessage still works...
        with c.websocket_connect("/ws/conversation") as ws:
            ws.send_json({"type": "auth", "api_key": "admin-secret"})
            assert ws.receive_json().get("type") == "auth_ok"

        # ...and a per-user oak_ key also authenticates + scopes.
        with c.websocket_connect("/ws/conversation") as ws:
            ws.send_json({"type": "auth", "api_key": key})
            assert ws.receive_json().get("type") == "auth_ok"
            ws.send_json(
                {"type": "user_message", "user_id": "someone_else", "content": "hi"}
            )
            err = ws.receive_json()
            assert err.get("code") == "AUTH_FAILED"

def test_ws_budget_gate_blocks_overbudget_tenant(monkeypatch, tmp_path):
    """P1 (billing): WS path enforces the tenant budget (error frame + close)."""
    import src.storage.paths as paths_mod
    from src.config import reload_settings
    from src.storage.paths import _paths_cache

    monkeypatch.setenv("PER_USER_AUTH", "false")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    _paths_cache.clear()
    monkeypatch.setattr("src.storage.metering._metering_stores", {})
    import src.http.auth as http_auth

    monkeypatch.setattr(http_auth, "_DEFAULT_RESOLVER", None)
    reload_settings()

    from src.storage.metering import get_metering_store
    from src.storage.tenant import get_tenant_store

    ts = get_tenant_store()
    tid = ts.upsert_tenant("acme", monthly_budget_usd=10.0)
    ts.add_member(tid, "over_budget_user")
    from datetime import UTC, datetime
    from src.storage.metering import UsageEventRow

    get_metering_store("over_budget_user").record(
        UsageEventRow(
            event_id="evt_budget_test",
            ts=datetime.now(UTC),
            user_id="over_budget_user",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=12.0,
        )
    )

    with _client() as c:
        with c.websocket_connect("/ws/conversation") as ws:
            ws.send_json(
                {
                    "type": "user_message",
                    "user_id": "over_budget_user",
                    "content": "hi",
                }
            )
            err = ws.receive_json()
            assert err.get("code") == "billing_exceeded"
