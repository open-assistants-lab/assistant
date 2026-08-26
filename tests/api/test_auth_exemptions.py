"""Auth exemption tests for webhook + OAuth routes (audit E24-auth).

With API_KEY set, external webhook callers have no Bearer token and
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

    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()
    yield "secret"
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
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
    """No API_KEY → localhost behaviour unchanged (no secret needed)."""
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
        params={"service": "gmail", "user_id": "oauth_user"},
        follow_redirects=False,
    )
    assert r.status_code != 401


def test_auth_callback_not_401_when_api_key_set(client, monkeypatch, api_key_mode):
    r = client.get("/auth/callback", params={"service": "gmail"})
    assert r.status_code != 401


def test_auth_login_binds_flow_to_deployment_owner(
    client, monkeypatch, api_key_mode
):
    """Login-CSRF/token-planting guard (audit E24 fix round): the PUBLIC
    /auth/login must not let an attacker bind the OAuth flow (and the
    resulting provider tokens) into an arbitrary user's credential vault.
    The client-supplied user_id is ignored; the flow binds to the
    deployment owner (DEFAULT_USER_ID).
    """
    seen_users: list[str] = []

    class _FakeVault:
        def create_oauth_state(self, service, user_id):
            seen_users.append(user_id)
            return "stub-state"

        def get_token(self, service):
            # Configured connector so the in-app guard lets the request pass.
            return {"client_id": "cid", "client_secret": "csecret"}

    class _FakeBridge:
        def __init__(self, user_id: str):
            seen_users.append(f"bridge:{user_id}")

        @property
        def vault(self):
            return _FakeVault()

    monkeypatch.setattr("src.http.main.ConnectKitBridge", _FakeBridge)

    r = client.get(
        "/auth/login",
        params={
            "service": "gmail",
            "user_id": "attacker_chosen_user",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "state=stub-state" in r.headers["location"]

    from src.storage.paths import DEFAULT_USER_ID

    # _oauth_config probes use ConnectKitBridge("") — filter to real flows.
    flow_ids = [
        u.removeprefix("bridge:")
        for u in seen_users
        if u.startswith("bridge:") and u != "bridge:"
    ]
    assert flow_ids and all(uid == DEFAULT_USER_ID for uid in flow_ids), (
        f"OAuth flow must bind only to the deployment owner {DEFAULT_USER_ID!r}, "
        f"got {flow_ids!r}"
    )
