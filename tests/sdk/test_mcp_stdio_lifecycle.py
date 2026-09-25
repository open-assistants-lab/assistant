"""Real stdio subprocess coverage for the MCP lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.sdk.tools_core.mcp_config import MCPConfig, MCPServerConfig
from src.sdk.tools_core.mcp_manager import MCPManager
from src.storage.paths import DataPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_stdio_server.py"


@pytest.mark.asyncio
async def test_real_stdio_server_discovers_calls_and_caches(tmp_path, monkeypatch) -> None:
    paths = DataPaths(data_root=str(tmp_path), user_id="stdio-user")
    config = MCPConfig(
        mcpServers={
            "fixture": MCPServerConfig(command=sys.executable, args=[str(FIXTURE)])
        }
    )
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.get_paths", lambda _user_id: paths)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _user_id: config
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_config_mtime", lambda _user_id: 1.0
    )

    manager = MCPManager("stdio-user")
    try:
        await manager._ensure_started()
        connections = await manager.snapshot_connections()
        assert "fixture" in connections
        assert {tool.name for tool in connections["fixture"].tools} == {"echo"}

        result = await manager.call_tool("fixture", "echo", {"text": "hello"})
        text = "\n".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        assert text == "hello"

        cached = manager.cached_metadata()
        assert cached[0]["server_name"] == "fixture"
        assert cached[0]["tools"][0]["name"] == "echo"
        assert "headers" not in cached[0]
    finally:
        await manager.cleanup()

    assert await manager.snapshot_connections() == {}
