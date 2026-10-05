"""B2: MCP start lifecycle — deadlock (#104), single-flight (#105), disabled servers (#106)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from src.sdk.tools_core.mcp_config import MCPConfig, MCPServerConfig
from src.sdk.tools_core.mcp_manager import MCPManager
from src.storage.paths import DataPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_stdio_server.py"


def _install(tmp_path, monkeypatch, *, server_enabled: bool = True):
    paths = DataPaths(data_root=str(tmp_path), user_id="stdio-user")
    config = MCPConfig(
        mcpServers={
            "fixture": MCPServerConfig(
                command=sys.executable, args=[str(FIXTURE)], enabled=server_enabled
            )
        }
    )
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.get_paths", lambda _u: paths)
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: config)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_config_mtime", lambda _u: 1.0
    )
    return config


@pytest.mark.asyncio
async def test_cold_get_tools_returns_without_deadlock(tmp_path, monkeypatch):
    """#104: get_tools holds the lock while _ensure_started takes it itself."""
    _install(tmp_path, monkeypatch)
    manager = MCPManager("stdio-user")
    try:
        tools = await asyncio.wait_for(manager.get_tools(), timeout=30)
        assert [t.name for t in tools] == ["echo"]
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_concurrent_first_use_starts_a_server_once(tmp_path, monkeypatch):
    """#105: two cold callers must not both start every server."""
    _install(tmp_path, monkeypatch)
    manager = MCPManager("stdio-user")
    starts: list[str] = []
    real_create = manager._create_connection

    async def counting(name, cfg):
        starts.append(name)
        return await real_create(name, cfg)

    monkeypatch.setattr(manager, "_create_connection", counting)
    try:
        await asyncio.gather(
            manager.ensure_connection("fixture"),
            manager.ensure_connection("fixture"),
        )
        assert starts == ["fixture"], f"server started {len(starts)}x: {starts}"
        conns = await manager.snapshot_connections()
        assert "fixture" in conns
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_a_disabled_server_is_never_connected_on_demand(tmp_path, monkeypatch):
    """#106: _reconnect honoured enabled=False only in the start path."""
    _install(tmp_path, monkeypatch, server_enabled=False)
    manager = MCPManager("stdio-user")
    try:
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(manager.call_tool("fixture", "echo", {"text": "x"}), timeout=30)
        conns = await manager.snapshot_connections()
        assert "fixture" not in conns, "a disabled server was connected on demand"
    finally:
        await manager.cleanup()
