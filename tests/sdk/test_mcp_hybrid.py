"""Hybrid MCP direct-tool promotion contract."""

from __future__ import annotations

import pytest

from src.sdk.tools_core.mcp_bridge import MCPToolBridge


class _Manager:
    def __init__(self) -> None:
        self.started = False
        self.cached_metadata = lambda: [
            {
                "server_name": "demo",
                "tools": [
                    {
                        "name": "safe",
                        "description": "Safe tool",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True},
                    },
                    {
                        "name": "write",
                        "description": "Write tool",
                        "inputSchema": {"type": "object"},
                        "annotations": {"destructiveHint": True},
                    },
                ],
            }
        ]

    async def _ensure_started(self):
        self.started = True

    async def get_connection(self, server_name):
        return None


@pytest.mark.asyncio
async def test_hybrid_promotes_only_allowlisted_cached_tools() -> None:
    manager = _Manager()
    bridge = MCPToolBridge("alice")
    bridge._manager = manager  # type: ignore[assignment]

    count = await bridge.discover_cached({"mcp__demo__safe"})

    assert count == 1
    assert bridge.get_tool_names() == ["mcp__demo__safe"]
    assert manager.started is False
