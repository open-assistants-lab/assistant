"""Staged v2 ingress refuses legacy exemptions before they can dispatch/read content."""

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.refactor.test_instance_authority import RED, STAFF, provision


@pytest.fixture
async def configured_server(tmp_path, monkeypatch):
    from src.auth.keys import KeyStore
    from src.auth.resolver import PerUserKeyResolver
    from src.http.authority import InstanceAuthorityGate

    state = await provision(tmp_path)
    await state.provision_member(
        issuer="assistant-api-key", subject="staff", user_id=STAFF, role="staff"
    )
    keys = KeyStore(str(tmp_path / "auth.db"))
    token = keys.generate("staff", instance_id=RED)
    monkeypatch.setattr("src.auth.keys.get_key_store", lambda: keys)
    monkeypatch.setattr("src.http.auth.get_resolver", lambda: PerUserKeyResolver())
    server = FastAPI()
    server.state.instance_state = state
    server.add_middleware(InstanceAuthorityGate, server=server)

    @server.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    def forbidden_legacy_handler(path: str):
        raise AssertionError("unqualified legacy route reached")

    server.mount("/mounted", FastAPI())
    return server, token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/message",
        "/v1/message",
        "/message/stream",
        "/settings",
        "/profile/reload",
        "/auth/keys/generate",
        "/webhooks/fire-id",
        "/governance/operations/op/complete",
        "/auth/callback",
        "/auth/oidc/callback",
        "/bootstrap",
        "/memories",
        "/files",
        "/mounted/legacy",
    ],
)
async def test_private_and_legacy_exempt_routes_fail_closed(configured_server, path):
    server, token = configured_server
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://test"
    ) as client:
        response = await client.post(
            path,
            headers={"Authorization": f"Bearer {token}"},
            json={"user_id": "different-user", "role": "admin", "instance_id": "forged"},
        )
    assert response.status_code == 503
    assert "different-user" not in response.text
    assert "synthetic-key" not in response.text


@pytest.mark.asyncio
async def test_loopback_bypass_is_not_qualified_identity(configured_server):
    server, _ = configured_server
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://localhost"
    ) as client:
        response = await client.get("/message")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_qualified_health_is_liveness_not_legacy_readiness(configured_server):
    server, _ = configured_server
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        ready = await client.get("/health/ready")
    assert response.json() == {"status": "initializing", "contract_version": 2}
    assert ready.status_code == 503


@pytest.mark.asyncio
async def test_websocket_does_not_reach_legacy_identity_bypass(configured_server):
    from starlette.websockets import WebSocketDisconnect

    server, _ = configured_server
    # Rejected before any database/thread access, so no cross-loop store use.
    with TestClient(server) as client:
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect("/ws/conversation"):
                pass
    assert failure.value.code in (4401, 4403)


@pytest.mark.asyncio
async def test_real_app_private_route_inventory_is_gated(configured_server, monkeypatch):
    from src.http.main import app

    server, token = configured_server
    monkeypatch.setattr(app.state, "instance_state", server.state.instance_state, raising=False)
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path and path not in ("/health", "/health/ready"):
            # The gate must catch aliases/exemptions before route matching; path
            # placeholders need not name or discover any private resource.
            paths.add(path)
    assert len(paths) > 50
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in paths:
            response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 503, path


@pytest.mark.asyncio
async def test_staged_instance_never_starts_legacy_migration(configured_server, monkeypatch):
    from src.http.main import app, lifespan

    server, _ = configured_server
    monkeypatch.setattr(app.state, "instance_state", server.state.instance_state, raising=False)

    def forbidden_legacy_startup(_settings):
        raise AssertionError("legacy startup reached")

    monkeypatch.setattr("src.http.main.warn_unknown_model_providers", forbidden_legacy_startup)
    with pytest.raises(RuntimeError, match="not yet qualified"):
        async with lifespan(app):
            pytest.fail("unqualified runtime admitted traffic")
