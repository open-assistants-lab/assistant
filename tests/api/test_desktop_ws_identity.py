"""Desktop WS message identity: server owns user_id and workspace_id.

Regression for D1 review P1: after a desktop upgrade authenticates, message
handling still took user_id/workspace_id from the client payload. A desktop
client must never be able to select another user or workspace.

The test replaces the external agent stream with an explicit completion
response so assertions run only after the store and stream boundaries.
"""

import importlib
import os
from unittest.mock import patch

import pytest


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    original_env = dict(os.environ)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEPLOYMENT_MODE", "desktop-server")
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(home / "Assistant"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(home / "Assistant" / ".system"))
    monkeypatch.setenv("SOLO_BYPASS", "false")
    monkeypatch.setenv("DESKTOP_LAUNCH_TOKEN", "desktop-test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    from src.config import settings as settings_module

    settings_module._config = None
    yield home
    for key in tuple(os.environ):
        if key not in original_env:
            os.environ.pop(key)
    os.environ.update(original_env)
    settings_module._config = None


def test_desktop_ws_message_uses_server_owned_identity(desktop_env, monkeypatch):
    """A desktop client-supplied user_id/workspace_id must be discarded."""
    from fastapi.testclient import TestClient

    import src.http.main as main_mod
    import src.http.routers.ws as ws_mod

    calls: list[tuple[str, str]] = []
    real_aget = ws_mod.aget_message_store

    async def spy_store(user_id, workspace_id, *args, **kwargs):
        calls.append((user_id, workspace_id))
        return await real_aget(user_id, workspace_id, *args, **kwargs)

    async def stub_stream(websocket, user_id, *args, **kwargs):
        calls.append((user_id, kwargs["workspace_id"]))
        await websocket.send_json({"type": "done", "response": "test complete"})

    monkeypatch.setattr(ws_mod, "aget_message_store", spy_store)
    monkeypatch.setattr(ws_mod, "_run_agent_stream", stub_stream)
    importlib.reload(main_mod)
    try:
        with TestClient(main_mod.app, raise_server_exceptions=False) as client:
            with client.websocket_connect(
                "/ws/conversation",
                headers={"Authorization": "Bearer desktop-test-token"},
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "user_message",
                        "content": "hi",
                        "user_id": "attacker",
                        "workspace_id": "foreign-ws",
                    }
                )
                assert websocket.receive_json()["type"] == "done"
    finally:
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "solo"}):
            importlib.reload(main_mod)

    assert len(calls) == 2, "expected both store and stream boundaries"
    for user_id, workspace_id in calls:
        assert user_id == "default_user", f"store called with client user_id {user_id!r}"
        assert workspace_id == "personal", f"store called with client workspace {workspace_id!r}"
