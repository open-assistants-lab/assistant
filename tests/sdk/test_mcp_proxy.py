"""Governed MCP proxy contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.sdk.tools import ToolResult
from src.sdk.tools_core import mcp as mcp_module


class _Manager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.get_tools = AsyncMock(
            return_value=[
                SimpleNamespace(
                    name="read",
                    description="Read a record",
                    inputSchema={"type": "object"},
                    annotations=SimpleNamespace(readOnlyHint=True, idempotentHint=True),
                )
            ]
        )
        self.ensure_connection = AsyncMock(return_value=SimpleNamespace(session=AsyncMock()))
        self.call_tool = AsyncMock(side_effect=self._call)
        self.cached_metadata = lambda: [
            {
                "server_name": "demo",
                "source_path": "/project/.mcp.json",
                "source_type": "project",
                "last_refresh": "2026-09-25T12:00:00Z",
                "tools": [{"name": "read", "description": "Read a record"}],
            }
        ]

    async def _call(self, server_name: str, tool_name: str, arguments: dict):
        self.calls.append((server_name, tool_name, arguments))
        if tool_name == "read":
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")], isError=False
            )
        raise AssertionError("unexpected tool")


@pytest.mark.asyncio
async def test_proxy_search_and_describe_use_cached_metadata(monkeypatch) -> None:
    manager = _Manager()
    monkeypatch.setattr(mcp_module, "get_mcp_manager", lambda _user_id: manager, raising=False)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_mcp_manager", lambda _user_id: manager
    )

    result = await mcp_module.mcp_proxy.ainvoke(
        {"user_id": "alice", "action": "describe", "server": "demo", "tool": "read"}
    )

    assert isinstance(result, ToolResult)
    assert not result.is_error
    assert "read" in result.content
    assert manager.get_tools.await_count == 0


@pytest.mark.asyncio
async def test_proxy_call_returns_structured_governed_result(monkeypatch) -> None:
    manager = _Manager()
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.get_mcp_manager", lambda _user_id: manager)

    result = await mcp_module.mcp_proxy.ainvoke(
        {
            "user_id": "alice",
            "action": "call",
            "server": "demo",
            "tool": "read",
            "arguments": {"id": "42"},
        }
    )

    assert isinstance(result, ToolResult)
    assert result.content == "ok"
    assert result.structured_content["outcome"] == "succeeded"
    assert result.structured_content["receipt_class"] == "mcp_proxy"
    assert manager.calls == [("demo", "read", {"id": "42"})]


@pytest.mark.asyncio
async def test_proxy_rejects_unknown_tool_before_dispatch(monkeypatch) -> None:
    manager = _Manager()
    monkeypatch.setattr("src.sdk.tools_core.mcp_manager.get_mcp_manager", lambda _user_id: manager)

    result = await mcp_module.mcp_proxy.ainvoke(
        {"user_id": "alice", "action": "call", "server": "demo", "tool": "delete"}
    )

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert manager.calls == []
