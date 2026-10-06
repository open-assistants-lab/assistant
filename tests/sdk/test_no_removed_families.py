"""Removal must eliminate the APIs, not merely hide them in desktop mode."""

import importlib.util

import pytest


@pytest.mark.parametrize("mode", ["solo", "desktop-server", "multi-tenant"])
def test_removed_tools_unavailable_in_every_mode(monkeypatch, mode):
    from src.sdk.native_tools import get_native_tools, reset_native_tools

    monkeypatch.setenv("DEPLOYMENT_MODE", mode)
    reset_native_tools()
    try:
        names = {tool.name for tool in get_native_tools()}
        leaked = sorted(
            name for name in names
            if name.startswith(("email_", "contacts_", "todos_"))
            or name == "connector_gmail_send"
        )
        assert leaked == [], f"removed tools still available: {leaked}"
    finally:
        reset_native_tools()


@pytest.mark.parametrize("module", [
    "src.sdk.tools_core.email_draft",
    "src.sdk.tools_core.email_db",
    "src.sdk.tools_core.email_sync",
    "src.sdk.tools_core.connector_gmail",
    "src.sdk.tools_core.todos",
    "src.sdk.tools_core.todos_storage",
    "src.sdk.tools_core.contacts",
    "src.sdk.tools_core.contacts_storage",
    "src.storage.email_db",
    "src.storage.gmail_client",
    "src.storage.gmail_cache",
    "src.http.routers.email",
    "src.http.routers.contacts",
    "src.http.routers.todos",
])
def test_removed_module_is_not_available(module):
    # find_spec avoids treating an unrelated import failure as successful removal.
    assert importlib.util.find_spec(module) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/emails", "/contacts", "/todos", "/dev/gmail-demo"])
async def test_removed_http_api_returns_not_found(path):
    import httpx
    from src.http.main import app

    # No lifespan: exercise real routing without starting background services.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 404
