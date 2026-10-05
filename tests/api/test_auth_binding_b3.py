"""B3: authentication identity binding (#117, #119, #120, #121)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _solo_client(monkeypatch) -> TestClient:
    """A TestClient over the real app in solo mode (no API key)."""
    from src.http.main import app

    return TestClient(app)


# --------------------------------------------------------------------------
# #117: subagent schedule routes must resolve identity, not trust the query
# --------------------------------------------------------------------------


def test_schedule_routes_call_resolve_user_id(monkeypatch):
    """Every /subagent-schedules route resolves the request's identity."""
    import inspect

    import src.http.routers.subagent_schedules as mod

    source = inspect.getsource(mod)
    assert "resolve_user_id(request" in source, (
        "schedule routes trust the client-supplied user_id query verbatim"
    )


@pytest.fixture()
def schedules_client(monkeypatch, tmp_path):
    from src.http.main import app
    return TestClient(app)


# --------------------------------------------------------------------------
# #119: webhook firing credentials are bound to a server-side user
# --------------------------------------------------------------------------


@pytest.fixture()
def webhook_client(monkeypatch, tmp_path):
    import src.http.routers.webhooks as hooks
    from src.http.main import app

    hooks._secret_store._path = tmp_path / "secrets.json"
    events: list = []

    class FakeRegistry:
        async def fire(self, event):
            events.append(event)

    monkeypatch.setattr(hooks, "get_trigger_registry", lambda: FakeRegistry())
    client = TestClient(app)
    yield client, events
    hooks._secret_store._path = None


def test_registered_webhook_fires_in_the_registered_user_context(webhook_client):
    """The firing user comes from the binding, not the body (#119)."""
    client, events = webhook_client
    created = client.post("/webhooks/t1/secret", params={"user_id": "owner"})
    assert created.status_code == 200
    secret = created.json()["secret"]

    fired = client.post(
        "/webhooks/t1",
        headers={"X-Webhook-Secret": secret},
        json={"user_id": "victim", "message": "hello"},
    )
    assert fired.status_code == 200
    assert len(events) == 1
    assert events[0].user_id == "owner", (
        f"a trigger secret holder chose the firing context: {events[0].user_id}"
    )


def test_webhook_body_identity_matching_the_binding_is_accepted(webhook_client):
    client, events = webhook_client
    secret = client.post("/webhooks/t2/secret", params={"user_id": "owner"}).json()["secret"]
    fired = client.post(
        "/webhooks/t2",
        headers={"X-Webhook-Secret": secret},
        json={"user_id": "owner", "message": "hello"},
    )
    assert fired.status_code == 200
    assert events and events[0].user_id == "owner"


def test_secret_registration_persists_the_binding(webhook_client):
    """The stored credential must carry its owner."""
    import src.http.routers.webhooks as hooks

    client, _ = webhook_client
    client.post("/webhooks/t3/secret", params={"user_id": "owner"})
    raw = json.loads(hooks._secret_store._file().read_text())
    record = raw.get("t3")
    assert isinstance(record, dict), f"secret stored without a binding: {raw}"
    assert record.get("user_id") == "owner"


# --------------------------------------------------------------------------
# #120: two distinct IdP accounts must never merge into one user scope
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_oidc_bindings(tmp_path, monkeypatch):
    import src.http.routers.auth_oidc as oidc

    monkeypatch.setattr(
        oidc, "_claim_bindings_path", lambda: tmp_path / "cfg" / "oidc_user_bindings.json"
    )
    yield


def test_claims_map_to_user_ids_collision_is_refused():
    """A second IdP subject claiming a bound name is refused, not merged."""
    from src.http.routers.auth_oidc import OidcError, claims_bind_user

    first = claims_bind_user({"sub": "sub-a", "preferred_username": "bob"})
    assert first == "bob"
    with pytest.raises(OidcError):
        claims_bind_user({"sub": "sub-b", "preferred_username": "bob"})


def test_same_subject_keeps_the_same_user_id():
    from src.http.routers.auth_oidc import claims_bind_user

    first = claims_bind_user({"sub": "sub-a", "preferred_username": "bob"})
    second = claims_bind_user({"sub": "sub-a", "preferred_username": "bob"})
    assert first == second


def test_same_subject_new_username_keeps_the_stable_id():
    """A subject that renames itself keeps its user scope (stable identity)."""
    from src.http.routers.auth_oidc import claims_bind_user

    first = claims_bind_user({"sub": "sub-a", "preferred_username": "bob"})
    second = claims_bind_user({"sub": "sub-a", "preferred_username": "robert"})
    assert first == second, "renaming the username changed the user scope"


# --------------------------------------------------------------------------
# #121: the session cookie must be Secure by default
# --------------------------------------------------------------------------


def test_session_cookie_is_secure(monkeypatch):
    import inspect

    import src.http.routers.auth_oidc as oidc

    source = inspect.getsource(oidc)
    callback = source[source.rindex("SESSION_COOKIE,"):]
    segment = callback[:300]
    assert "secure" in segment, (
        "the OIDC session cookie is set without the Secure attribute"
    )
