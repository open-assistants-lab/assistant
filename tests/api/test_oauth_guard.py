"""OAuth guard tests — /auth/login must not emit broken authorize URLs.

Regression: an unconfigured connector (no client_id in the vault, no
DEFAULT_GWS_CLIENT_ID env) previously redirected to the provider with an
empty client_id — a broken URL an agent could stumble into (observed during
the 140-round stress test). The guard in src/http/main.py now rejects those
with 400.

Note: /auth/login 404s via TestClient (pre-existing quirk — the route works
on uvicorn), so the redirect behavior is covered by unit tests of the guard
decision plus manual uvicorn verification.
"""

from __future__ import annotations

from src.http.main import _oauth_login_error


def _config(**overrides):
    cfg = {"client_id": "", "client_secret": ""}
    cfg.update(overrides)
    return lambda service: cfg


def test_guard_blocks_unconfigured_connector():
    error = _oauth_login_error("acuity-scheduling", _config())
    assert error is not None
    assert "not configured" in error
    assert "client_id" in error


def test_guard_allows_configured_connector():
    error = _oauth_login_error("acuity-scheduling", _config(client_id="abc123"))
    assert error is None


def test_guard_blocks_when_config_raises():
    def boom(service):
        raise RuntimeError("vault unavailable")

    error = _oauth_login_error("acuity-scheduling", boom)
    assert error is not None


def test_oauth_login_endpoint_returns_400_for_unconfigured(client, test_user_id):
    # The guard middleware intercepts before routing, so this works even
    # though /auth/login itself 404s via TestClient.
    r = client.get(
        "/auth/login",
        params={"service": "acuity-scheduling", "user_id": test_user_id},
    )
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]


def test_oauth_login_unknown_service_returns_400(client, test_user_id):
    r = client.get(
        "/auth/login",
        params={"service": "no-such-service", "user_id": test_user_id},
    )
    assert r.status_code == 400
