"""Tests for tool lazy-load from HybridDB index via _try_lazy_load."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.sdk.loop import AgentLoop
from src.sdk.messages import ToolCall
from src.sdk.tools import ToolDefinition


class _MockProvider:
    """Minimal mock LLM provider for AgentLoop tests."""

    provider_id = "mock"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        return MagicMock()

    def get_model_info(self, model=None):
        from src.sdk.providers.base import ModelInfo
        return ModelInfo(id=model or "mock", provider_id="mock")

    def count_tokens(self, messages):
        return 0


@pytest.fixture
def mock_loop():
    loop = AgentLoop(
        provider=_MockProvider(),
        tools=[],
        user_id="test_user",
        workspace_id="personal",
    )
    return loop


@pytest.fixture
def tool_index():
    from src.sdk.tool_index import ToolIndex

    d = Path(tempfile.mkdtemp()) / "tool_index"
    idx = ToolIndex(d)
    yield idx
    idx.close()


class TestLazyLoadNoIndex:
    async def test_no_index_returns_none(self, mock_loop):
        mock_loop._tool_index = None
        tc = ToolCall(id="1", name="any_tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is None

    async def test_no_registry_returns_none(self, mock_loop):
        mock_loop._tool_index = MagicMock()
        mock_loop._tool_index.get_definition.return_value = None
        tc = ToolCall(id="1", name="missing_tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is None


class TestLazyLoadCustomTool:
    async def test_load_and_execute_custom(self, mock_loop, tool_index):
        from src.sdk.tool_index import _rebuild_custom_function

        td = ToolDefinition(name="greeter", description="A test tool", parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
        })
        reconstruct = {"command": 'echo "hello {{name}}"', "install": []}
        td = _rebuild_custom_function(td, reconstruct)
        tool_index.index_tool(td, tool_type="custom", reconstruct=reconstruct)
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="greeter", arguments={"name": "World"})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert not result.is_error
        # Should have been registered in the loop's registry
        assert mock_loop._registry.has("greeter")
        assert "greeter" in mock_loop._recently_used

    async def test_load_custom_unknown_tool(self, mock_loop, tool_index):
        mock_loop._tool_index = tool_index
        tc = ToolCall(id="1", name="nonexistent", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is None

    async def test_load_custom_with_tool_dir(self, mock_loop, tool_index):
        from src.sdk.tool_index import _rebuild_custom_function

        td = ToolDefinition(name="script_tool", description="Uses tool_dir", parameters={
            "type": "object",
            "properties": {"input": {"type": "string"}},
        })
        tool_dir = "/tmp/test_tool_dir"
        reconstruct = {"command": 'uv run "{{tool_dir}}/script.py" "{{input}}"', "install": [], "tool_dir": tool_dir}
        td = _rebuild_custom_function(td, reconstruct)
        tool_index.index_tool(td, tool_type="custom", reconstruct=reconstruct)
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="script_tool", arguments={"input": "data.csv"})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        # The command should have tool_dir rendered
        assert mock_loop._registry.has("script_tool")


class TestLazyLoadMCPTool:
    async def test_load_mcp_from_bridge(self, mock_loop, tool_index):
        mcp_td = ToolDefinition(
            name="mcp__math__add",
            description="Add numbers",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            },
        )
        reconstruct = {"server_name": "math", "mcp_tool_name": "add"}
        tool_index.index_tool(mcp_td, tool_type="mcp", namespace="mcp__math", reconstruct=reconstruct)

        mock_bridge = MagicMock()
        mock_bridge.get_tool_definition.return_value = mcp_td
        mock_loop._mcp_bridge = mock_bridge
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="mcp__math__add", arguments={"a": 1, "b": 2})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        mock_bridge.get_tool_definition.assert_called_once_with("mcp__math__add")

    async def test_load_mcp_bridge_dead(self, mock_loop, tool_index):
        td = ToolDefinition(name="mcp__dead__tool", description="Dead server")
        reconstruct = {"server_name": "dead", "mcp_tool_name": "tool"}
        tool_index.index_tool(td, tool_type="mcp", namespace="mcp__dead", reconstruct=reconstruct)

        mock_bridge = MagicMock()
        mock_bridge.get_tool_definition.return_value = None
        mock_loop._mcp_bridge = mock_bridge
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="mcp__dead__tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert result.is_error
        assert "not connected" in result.content or "mcp_reload" in result.content

    async def test_load_mcp_no_bridge(self, mock_loop, tool_index):
        td = ToolDefinition(name="mcp__orphan__tool", description="No bridge")
        tool_index.index_tool(td, tool_type="mcp")
        mock_loop._mcp_bridge = None
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="mcp__orphan__tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert result.is_error


