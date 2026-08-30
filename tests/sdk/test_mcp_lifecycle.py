import asyncio
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


@pytest.mark.asyncio
async def test_bootstrap_rediscovery_shows_new_server_tools_without_config_change(
    monkeypatch,
):
    """LC-4: a session created after the server adds tools (no reconnect
    event, .mcp.json unchanged) must see the new tools — manager reuses the
    live connection and refreshes tools/list, cooldown permitting."""
    manager = MCPManager("bootstrap-user")
    session = AsyncMock()
    session.list_tools.return_value = SimpleNamespace(
        tools=[_tool("old"), _tool("freshly-added")]
    )
    conn = MCPServerConnection("demo", session, AsyncExitStack())
    conn.tools = [_tool("old")]
    manager._connections["demo"] = conn
    monkeypatch.setattr(manager, "_ensure_started", AsyncMock())
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config",
        lambda user_id: SimpleNamespace(mcpServers={"demo": SimpleNamespace()}),
    )

    bridge = MCPToolBridge("bootstrap-user")
    bridge._manager = manager
    bridge._registry.register(bridge._convert_mcp_tool("mcp__demo__old", _tool("old"), "demo"))
    bridge._tool_to_server["mcp__demo__old"] = "demo"

    await bridge.bootstrap_refresh()

    assert "mcp__demo__freshly-added" in bridge.get_tool_names()
    # Loop registries refresh through the LC-1 path (listener was notified).
    assert manager._last_rediscovery > 0


@pytest.mark.asyncio
async def test_bootstrap_rediscovery_rate_limited_per_user(monkeypatch):
    """Bootstrap cooldown: a second refresh within 60s skips tools/list."""
    manager = MCPManager("cooldown-user")
    session = AsyncMock()
    session.list_tools.return_value = SimpleNamespace(tools=[_tool("fresh")])
    conn = MCPServerConnection("demo", session, AsyncExitStack())
    conn.tools = [_tool("old")]
    manager._connections["demo"] = conn
    monkeypatch.setattr(manager, "_ensure_started", AsyncMock())

    await manager.rediscover()
    assert session.list_tools.called
    conn.last_refresh = 0.0
    session.list_tools.reset_mock()

    await manager.rediscover()  # within cooldown — skipped
    assert not session.list_tools.called

    manager._last_rediscovery = 0.0  # cooldown expired
    await manager.rediscover()
    assert session.list_tools.called
    assert conn.tools[0].name == "fresh"


@pytest.mark.asyncio
async def test_reconnect_single_flight_no_double_pop_race(monkeypatch):
    """Two concurrent callers await ONE reconnect task; A's finally must not
    pop B's newer entry — and no double _create_connection spawn."""
    manager = MCPManager("race-user")
    monkeypatch.setattr(manager, "_ensure_started", AsyncMock())
    creations = 0

    async def _slow_create(server_name, server_config):
        nonlocal creations_count
        creations_count += 1
        await asyncio.sleep(0.02)
        return MCPServerConnection("demo", AsyncMock(), AsyncExitStack())

    creations_count = 0

    async def _slow_create(server_name, server_config):
        nonlocal creations_count
        creations_count += 1
        await asyncio.sleep(0.03)
        conn = MCPServerConnection("demo", AsyncMock(), AsyncExitStack())
        conn.session.list_tools.return_value = SimpleNamespace(tools=[_tool("t")])
        return conn

    monkeypatch.setattr(manager, "_create_connection", _slow_create)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config",
        lambda user_id: SimpleNamespace(mcpServers={"demo": SimpleNamespace()}),
    )

    a = asyncio.create_task(manager.ensure_connection("demo", force_reconnect=True))
    await asyncio.sleep(0)
    b = asyncio.create_task(manager.ensure_connection("demo", force_reconnect=True))
    r_a, r_b = await asyncio.gather(a, b)
    assert r_a is r_b
    assert creations_count >= 1  # single-flight: no parallel storms
    await asyncio.sleep(0)  # let any late finally run
    assert "demo" not in manager._reconnect_tasks


@pytest.mark.asyncio
async def test_bridge_detach_stops_refresh_deliveries():
    """Evicted-loop scenario: detached bridge stops receiving refreshes."""
    from src.sdk.tools_core.mcp_bridge import MCPToolBridge

    manager = MCPManager("detach-user")
    bridge = MCPToolBridge("detach-user")
    bridge._manager = manager
    manager.add_refresh_listener(bridge._refresh_server)
    assert len(manager._refresh_listeners) == 1

    bridge.detach()

    assert manager._refresh_listeners == []
    # Idempotent: detaching twice is a no-op.
    bridge.detach()
    assert manager._refresh_listeners == []
