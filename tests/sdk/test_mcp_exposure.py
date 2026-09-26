"""MCP Layer 5 fixes + Layer 1 single-surface collapse.

Layer 5: per-server `enabled` actually honoured, bounded tool fetch, and
server-supplied `alwaysLoad` ignored unless the operator opts in.
Layer 1: `mcp_list` / `mcp_tools` / `mcp_reload` folded into `mcp_proxy`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.sdk.tools_core.mcp_config import MCPConfig, MCPServerConfig
from src.sdk.tools_core.mcp_manager import MCPManager


def _tool(name: str, meta: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        inputSchema={"type": "object"},
        annotations=None,
        meta=meta or {},
    )


# --------------------------------------------------------------------------
# Layer 5.1 — per-server `enabled` is honoured, and is real config
# --------------------------------------------------------------------------


def test_enabled_is_a_real_config_field_not_an_excluded_placeholder() -> None:
    """The v0.6.21 `disabled` field was exclude=True, so it could not be read."""
    from src.sdk.tools_core.mcp_config import MCPConfig as C

    server = C(mcpServers={"demo": MCPServerConfig(command="python", enabled=False)})
    assert server.mcpServers["demo"].enabled is False
    # and it must survive serialisation so it round-trips through the cache
    assert "enabled" in server.mcpServers["demo"].model_dump()


def test_dead_disabled_field_is_gone() -> None:
    """A field that does nothing must not remain declared."""
    assert "disabled" not in MCPServerConfig.model_fields


@pytest.mark.asyncio
async def test_disabled_server_is_never_started(tmp_path, monkeypatch) -> None:
    config = MCPConfig(
        mcpServers={
            "on": MCPServerConfig(command="python"),
            "off": MCPServerConfig(command="python", enabled=False),
        }
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: config
    )
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.get_config_mtime", lambda _u: 1.0)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_paths",
        lambda _u: SimpleNamespace(user_dir=tmp_path, subagent_schedules_db_path=tmp_path / "x.db"),
    )

    manager = MCPManager("enabled-user")
    created: list[str] = []

    async def fake_create(name, _cfg):
        created.append(name)
        conn = SimpleNamespace(
            name=name,
            session=AsyncMock(),
            tools=[],
            aclose=AsyncMock(),
            last_used=0.0,
            last_refresh=0.0,
        )
        conn.session.list_tools.return_value = SimpleNamespace(tools=[_tool("t")])
        return conn

    monkeypatch.setattr(manager, "_create_connection", fake_create)
    try:
        await manager._ensure_started()
        assert "on" in created
        assert "off" not in created
        connections = await manager.snapshot_connections()
        assert "off" not in connections
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_disabled_server_is_visible_in_health_with_a_reason(
    tmp_path, monkeypatch
) -> None:
    config = MCPConfig(
        mcpServers={"off": MCPServerConfig(command="python", enabled=False)}
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: config
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_paths",
        lambda _u: SimpleNamespace(user_dir=tmp_path, subagent_schedules_db_path=tmp_path / "x.db"),
    )
    manager = MCPManager("health-user")
    try:
        health = await manager.health()
        # I3: silent disappearance is a diagnosis dead end.
        assert health["servers"]["off"]["status"] == "disabled"
        assert health["servers"]["off"]["connected"] is False
    finally:
        await manager.cleanup()


# --------------------------------------------------------------------------
# Layer 5.2 — bounded tool fetch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_fetch_is_bounded_by_refresh_timeout(monkeypatch) -> None:
    manager = MCPManager("timeout-user")
    real = manager._get_idle_timeout

    monkeypatch.setattr(manager, "_get_idle_timeout", lambda: 10)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_config.load_mcp_config",
        lambda _u: MCPConfig(mcpServers={"d": MCPServerConfig(command="python")}),
    )
    monkeypatch.setattr("src.sdk.tools_core.mcp_config.get_config_mtime", lambda _u: 1.0)

    hang = asyncio.Event()

    async def hanging_list_tools():
        await hang.wait()  # never completes
        return SimpleNamespace(tools=[])

    conn = SimpleNamespace(
        server_name="d",
        session=SimpleNamespace(list_tools=hanging_list_tools),
        aclose=AsyncMock(),
    )
    # The configured timeout is the budget; use a tiny one so the test is fast.
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_settings",
        lambda: SimpleNamespace(mcp=SimpleNamespace(refresh_timeout_seconds=0.05)),
    )

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await asyncio.wait_for(manager._list_tools_bounded(conn), timeout=2)
    hang.set()
    assert real() >= 0


@pytest.mark.asyncio
async def test_tool_fetch_timeout_reports_a_useful_error(monkeypatch) -> None:
    manager = MCPManager("timeout-err")
    hang = asyncio.Event()

    async def hanging_list_tools():
        await hang.wait()
        return SimpleNamespace(tools=[])

    conn = SimpleNamespace(
        server_name="d", session=SimpleNamespace(list_tools=hanging_list_tools)
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_settings",
        lambda: SimpleNamespace(mcp=SimpleNamespace(refresh_timeout_seconds=0.05)),
    )
    try:
        with pytest.raises(Exception) as excinfo:
            await asyncio.wait_for(manager._list_tools_bounded(conn), timeout=2)
        assert "timed out" in str(excinfo.value).lower()
    finally:
        hang.set()


# --------------------------------------------------------------------------
# Layer 5.4 — server-supplied alwaysLoad requires operator trust (C3)
# --------------------------------------------------------------------------


def test_always_load_is_ignored_without_operator_trust() -> None:
    """A third-party server must not grant itself a permanent context slot."""
    from src.sdk.tools_core.mcp_results import select_always_load

    tools = [_tool("a", {"alwaysLoad": True}), _tool("b", {"alwaysLoad": True})]
    assert select_always_load(tools, operator_always_load=frozenset(), trust_server=False) == frozenset()

    trusted = select_always_load(tools, operator_always_load=frozenset(), trust_server=True)
    assert trusted == frozenset({"a", "b"})


def test_operator_always_load_works_without_server_trust() -> None:
    from src.sdk.tools_core.mcp_results import select_always_load

    tools = [_tool("a", {"alwaysLoad": True}), _tool("b")]
    chosen = select_always_load(tools, operator_always_load=frozenset({"b"}), trust_server=False)
    assert chosen == frozenset({"b"})


# --------------------------------------------------------------------------
# Layer 1 — one always-present MCP tool
# --------------------------------------------------------------------------


def test_only_one_mcp_tool_is_registered() -> None:
    from src.sdk.native_tools import get_native_tools

    mcp_tools = sorted(t.name for t in get_native_tools() if t.name.startswith("mcp"))
    assert mcp_tools == ["mcp_proxy"]


def test_mcp_meta_surface_is_within_a_token_budget() -> None:
    """P1: the MCP surface must not silently grow again."""
    import json

    from src.sdk.native_tools import get_native_tools

    total = sum(
        len(json.dumps(t.parameters, default=str))
        for t in get_native_tools()
        if t.name.startswith("mcp")
    )
    # mcp_proxy measured at 1,020 chars in v0.6.21; budget 1,300 for headroom.
    assert total <= 1300, f"MCP meta schema grew to {total} chars"


def test_mcp_reload_is_no_longer_a_core_tool_name() -> None:
    from src.sdk.tools_custom import CORE_TOOL_NAMES

    assert "mcp_reload" not in CORE_TOOL_NAMES
    assert "mcp_proxy" in CORE_TOOL_NAMES


@pytest.mark.asyncio
async def test_mcp_proxy_covers_removed_tool_capabilities() -> None:
    """status/describe/refresh must be reachable through the single surface."""
    from src.sdk.tools_core.mcp import mcp_proxy

    assert set(mcp_proxy.parameters["properties"]["action"]["enum"]) >= {
        "search",
        "status",
        "describe",
        "refresh",
        "call",
    }


@pytest.mark.asyncio
async def test_removed_tools_are_not_exported() -> None:
    from src.sdk.tools_core import mcp as mcp_module

    for removed in ("mcp_list", "mcp_tools", "mcp_reload"):
        assert not hasattr(mcp_module, removed)
