from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.sdk.tools_core.mcp_bridge import MCPToolBridge
from src.sdk.tools_core.mcp_manager import MCPManager, MCPServerConnection


def _tool(name: str):
    return SimpleNamespace(
        name=name,
        description=f"Description for {name}",
        inputSchema={"type": "object", "properties": {}},
        annotations=None,
    )


@pytest.mark.asyncio
async def test_tool_call_reconnects_and_rediscovers_changed_live_catalog(monkeypatch):
    manager = MCPManager("reconnect-user")
    first_session = AsyncMock()
    first_session.call_tool.side_effect = ConnectionError("server stopped")
    first = MCPServerConnection("demo", first_session, AsyncExitStack())
    first.tools = [_tool("old")]
    manager._connections["demo"] = first

    new_session = AsyncMock()
    new_session.list_tools.return_value = SimpleNamespace(
        tools=[_tool("old"), *[_tool(f"added-{i}") for i in range(6)]]
    )
    new_session.call_tool.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="recovered")], isError=False
    )
    new_conn = MCPServerConnection("demo", new_session, AsyncExitStack())
    monkeypatch.setattr(manager, "_create_connection", AsyncMock(return_value=new_conn))
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config",
        lambda user_id: SimpleNamespace(mcpServers={"demo": SimpleNamespace()}),
    )

    bridge = MCPToolBridge("reconnect-user")
    bridge._manager = manager
    manager.add_refresh_listener(bridge._refresh_server)
    await bridge.discover()

    result = await bridge.get_tool_definition("mcp__demo__old").ainvoke({})

    assert result.content == "recovered"
    assert result.is_error is False
    assert len(bridge.get_tool_names()) == 7
    assert "mcp__demo__added-5" in bridge.get_tool_names()


@pytest.mark.asyncio
async def test_failed_reconnect_returns_clear_reconnecting_state(monkeypatch):
    manager = MCPManager("down-user")
    monkeypatch.setattr(manager, "_ensure_started", AsyncMock())
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config",
        lambda user_id: SimpleNamespace(mcpServers={"demo": SimpleNamespace()}),
    )
    monkeypatch.setattr(
        manager, "_create_connection", AsyncMock(side_effect=ConnectionError("still down"))
    )
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.asyncio.sleep", AsyncMock())
    bridge = MCPToolBridge("down-user")
    bridge._manager = manager
    manager.add_refresh_listener(bridge._refresh_server)
    bridge._registry.register(bridge._convert_mcp_tool("mcp__demo__old", _tool("old"), "demo"))

    result = await bridge.get_tool_definition("mcp__demo__old").ainvoke({})

    assert result.is_error is True
    assert "reconnecting" in result.content
    assert "not connected" not in result.content


@pytest.mark.asyncio
async def test_health_distinguishes_connected_stale_and_absent(monkeypatch):
    manager = MCPManager("health-user")
    healthy = MCPServerConnection("healthy", AsyncMock(), AsyncExitStack())
    healthy.tools = [_tool("one")]
    stale = MCPServerConnection("stale", AsyncMock(), AsyncExitStack())
    stale.tools = [_tool("one"), _tool("two")]
    stale.last_used -= 11
    manager._connections = {"healthy": healthy, "stale": stale}
    manager._last_errors["absent"] = "removed from configuration"
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config",
        lambda user_id: SimpleNamespace(
            mcpServers={"healthy": SimpleNamespace(), "stale": SimpleNamespace()}
        ),
    )
    monkeypatch.setattr(manager, "_get_idle_timeout", lambda: 10)

    health = await manager.health()
    assert health["servers"]["healthy"]["status"] == "connected"
    assert health["servers"]["healthy"]["tool_count"] == 1
    assert health["servers"]["stale"]["status"] == "stale"
    assert health["servers"]["absent"]["status"] == "absent"
    assert health["servers"]["stale"]["connected"] is True
