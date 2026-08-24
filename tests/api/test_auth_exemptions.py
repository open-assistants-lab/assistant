"""Auth exemption tests for webhook + OAuth routes (audit E24-auth).

With EA_API_KEY set, external webhook callers have no Bearer token and
browser OAuth redirects can't carry one — both previously 401'd, killing
the features in exactly the WAN deployments they exist for.

Contract after the fix:
- POST /webhooks/{id} skips Bearer auth but requires a per-trigger
  ``X-Webhook-Secret`` matching a server-generated secret (registered via
  the Bearer-protected ``/webhooks/{id}/secret`` endpoint).
- Unregistered triggers fail closed while an API key is configured.
- GET /auth/login and /auth/callback are exact-path public (the in-app
  connector guard still rejects unconfigured services with 400).
"""

from __future__ import annotations

import pytest

from src.sdk.messages import Message


@pytest.fixture
def api_key_mode(client, monkeypatch):
    """Configure the app as a Solo-WAN deployment: API key set, bypass off."""
    from src.config import reload_settings

    monkeypatch.setenv("EA_API_KEY", "secret")
    monkeypatch.setenv("EA_SOLO_BYPASS", "false")
    reload_settings()
    yield "secret"
    monkeypatch.delenv("EA_API_KEY", raising=False)
    monkeypatch.delenv("EA_SOLO_BYPASS", raising=False)
    reload_settings()


@pytest.fixture
def fake_agent_run(monkeypatch):
    """Stub the agent run so webhook fires never start a real agent.

    The app lifespan registers default_trigger_handler on the global
    registry; that handler function-locally imports run_sdk_agent from
    src.sdk.runner, so the stub patches the source module.
    """
    calls: list[dict] = []

    async def _fake_run(**kwargs):
        calls.append(kwargs)
        return [Message.assistant(content="webhook ok")]

    monkeypatch.setattr("src.sdk.runner.run_sdk_agent", _fake_run)
    return calls


def _register_secret(client, bearer: str, trigger_id: str) -> str:
    r = client.post(
        f"/webhooks/{trigger_id}/secret",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def test_webhook_with_valid_secret_runs_without_bearer(
    client, monkeypatch, api_key_mode, fake_agent_run
):
    secret = _register_secret(client, api_key_mode, "wh_valid")

    r = client.post(
        "/webhooks/wh_valid",
        json={"user_id": "hook_user", "message": "ping"},
        headers={"X-Webhook-Secret": secret},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert len(fake_agent_run) == 1
    assert fake_agent_run[0]["user_id"] == "hook_user"


def test_webhook_wrong_secret_rejected(client, monkeypatch, api_key_mode, fake_agent_run):
    _register_secret(client, api_key_mode, "wh_wrong")

    r = client.post(
        "/webhooks/wh_wrong",
        json={"user_id": "hook_user", "message": "ping"},
        headers={"X-Webhook-Secret": "not-the-secret"},
    )
    assert r.status_code == 401
    assert fake_agent_run == []


def test_webhook_missing_secret_rejected(client, monkeypatch, api_key_mode, fake_agent_run):
    _register_secret(client, api_key_mode, "wh_missing")

    r = client.post(
        "/webhooks/wh_missing",
        json={"user_id": "hook_user", "message": "ping"},
    )
    assert r.status_code == 401
    assert fake_agent_run == []


def test_unregistered_trigger_fails_closed_when_api_key_set(
    client, monkeypatch, api_key_mode, fake_agent_run
):
    r = client.post(
        "/webhooks/never_registered",
        json={"user_id": "hook_user", "message": "ping"},
        headers={"X-Webhook-Secret": "anything"},
    )
    assert r.status_code == 401
    assert fake_agent_run == []


def test_secret_registration_requires_bearer(client, monkeypatch, api_key_mode):
    r = client.post("/webhooks/wh_noauth/secret")
    assert r.status_code == 401


def test_local_mode_allows_unregistered_webhook(client, fake_agent_run):
    """No EA_API_KEY → localhost behaviour unchanged (no secret needed)."""
    r = client.post(
        "/webhooks/wh_local",
        json={"user_id": "hook_user", "message": "ping"},
    )
    assert r.status_code == 200, r.text
    assert len(fake_agent_run) == 1


def test_auth_login_not_401_when_api_key_set(client, monkeypatch, api_key_mode):
    """Browser-initiated OAuth redirect carries no Bearer — must reach the
    in-app connector guard (400 for unconfigured), not the auth middleware."""
    r = client.get(
        "/auth/login",
        params={"service": "acuity-scheduling", "user_id": "oauth_user"},
    )
    assert r.status_code != 401


def test_auth_callback_not_401_when_api_key_set(client, monkeypatch, api_key_mode):
    r = client.get("/auth/callback", params={"service": "acuity-scheduling"})
    assert r.status_code != 401
