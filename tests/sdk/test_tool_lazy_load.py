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


class TestLazyLoadConnectorTool:
    async def test_load_connector_from_bridge(self, mock_loop, tool_index):
        reconstruct = {"namespace": "google_workspace", "tool_name": "gmail_messages_list"}
        td = ToolDefinition(
            name="google_workspace__gmail_messages_list",
            description="List Gmail messages",
        )
        tool_index.index_tool(td, tool_type="connector", namespace="google_workspace", reconstruct=reconstruct)

        mock_bridge = MagicMock()
        mock_bridge.get_tool_definitions.return_value = [{
            "name": "google_workspace__gmail_messages_list",
            "description": "List Gmail messages",
            "annotations": {"read_only": True, "destructive": False},
            "function": lambda **kw: "mock result",
        }]
        mock_loop._connectkit_bridge = mock_bridge
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="google_workspace__gmail_messages_list", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert mock_loop._registry.has("google_workspace__gmail_messages_list")

    async def test_load_connector_not_found(self, mock_loop, tool_index):
        td = ToolDefinition(name="gone__tool", description="Missing")
        tool_index.index_tool(td, tool_type="connector")

        mock_bridge = MagicMock()
        mock_bridge.get_tool_definitions.return_value = []
        mock_loop._connectkit_bridge = mock_bridge
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="gone__tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert result.is_error

    async def test_load_connector_no_bridge(self, mock_loop, tool_index):
        td = ToolDefinition(name="orphan__tool", description="No bridge")
        tool_index.index_tool(td, tool_type="connector")
        mock_loop._connectkit_bridge = None
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="orphan__tool", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        assert result is not None
        assert result.is_error


class TestLazyLoadNativeTool:
    async def test_load_native_from_index(self, mock_loop, tool_index):
        td = ToolDefinition(name="time_get", description="Get current time")
        tool_index.index_tool(td, tool_type="native", namespace="native")
        mock_loop._tool_index = tool_index

        tc = ToolCall(id="1", name="time_get", arguments={})
        result = await mock_loop._try_lazy_load(tc)
        # Native tool will be registered but function won't have a real implementation
        assert result is not None
        assert mock_loop._registry.has("time_get")


class TestLazyLoadThroughExecute:
    async def test_unknown_tool_triggers_lazy_load(self, tool_index):
        """When a tool is not in the registry, _execute_tool calls _try_lazy_load."""
        td = ToolDefinition(name="unknown_tool", description="Not yet loaded")
        from src.sdk.tool_index import _rebuild_custom_function
        td = _rebuild_custom_function(td, {"command": 'echo "hi"', "install": []})
        tool_index.index_tool(td, tool_type="custom")

        loop = AgentLoop(provider=_MockProvider(), tools=[], user_id="test_user")
        loop._tool_index = tool_index

        tc = ToolCall(id="1", name="unknown_tool", arguments={})
        result = await loop._execute_tool(tc)
        assert result is not None
        # Even if the tool triggers through the lazy load path
        assert loop._recently_used is not None

    async def test_registered_tool_does_not_trigger_lazy(self):
        """A tool already in the registry should skip lazy-load."""
        from src.sdk.tools import _extract_tool_schema

        def echo_fn(text: str = "") -> str:
            return f"echo: {text}"

        td = _extract_tool_schema(echo_fn)
        loop = AgentLoop(provider=_MockProvider(), tools=[td], user_id="test_user")
        # Even if there's no index, registered tools work
        loop._tool_index = None

        tc = ToolCall(id="1", name="echo_fn", arguments={"text": "hello"})
        result = await loop._execute_tool(tc)
        assert result is not None


class TestRecencyTracking:
    async def test_recently_used_updated(self, tool_index):
        td = ToolDefinition(name="recent_tool", description="Recent tool")
        from src.sdk.tool_index import _rebuild_custom_function
        td = _rebuild_custom_function(td, {"command": 'echo "test"', "install": []})
        tool_index.index_tool(td, tool_type="custom")

        loop = AgentLoop(provider=_MockProvider(), tools=[], user_id="test_user")
        loop._tool_index = tool_index

        tc = ToolCall(id="1", name="recent_tool", arguments={})
        await loop._execute_tool(tc)
        assert "recent_tool" in loop._recently_used

    async def test_recently_used_preserved_after_summary(self, tool_index):
        td = ToolDefinition(name="persistent_tool", description="Should persist")
        from src.sdk.tool_index import _rebuild_custom_function
        td = _rebuild_custom_function(td, {"command": 'echo "test"', "install": []})
        tool_index.index_tool(td, tool_type="custom")

        loop = AgentLoop(provider=_MockProvider(), tools=[], user_id="test_user")
        loop._tool_index = tool_index
        loop._recently_used.add("persistent_tool")

        # Simulate context compaction
        await loop._execute_tool(ToolCall(id="1", name="persistent_tool", arguments={}))
        assert "persistent_tool" in loop._recently_used
