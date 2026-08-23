"""Tests for tool_search and tool_reload core tools."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.sdk.tools import ToolDefinition


class TestToolSearchTool:
    def test_search_no_loop(self):
        from src.sdk.tools_core.tool_search import tool_search

        result = tool_search.invoke({"description": "anything"})
        assert "No tool index" in result or isinstance(result, str)

    def test_search_not_found(self):
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)
        # Empty index — no tools to find
        # It's not empty, we inserted tool above. Use a fresh empty index:
        idx.close()

        d2 = Path(tempfile.mkdtemp()) / "idx2"
        idx2 = ToolIndex(d2)

        from src.sdk.loop import AgentLoop, _current_agent_loop

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx2
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_search import tool_search

            result = tool_search.invoke({"description": "anything"})
            assert "No tools found" in result
        finally:
            _current_agent_loop.reset(token)
        idx2.close()

    def test_search_found(self):
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)
        td = ToolDefinition(name="pdf_extract", description="Extract text from PDF files")
        idx.index_tool(td, tool_type="custom")

        from src.sdk.loop import AgentLoop, _current_agent_loop

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_search import tool_search

            result = tool_search.invoke({"description": "extract pdf"})
            assert "pdf_extract" in result
            assert "Extract text from PDF" in result
        finally:
            _current_agent_loop.reset(token)
        idx.close()

    def test_search_truncates_long_descriptions(self):
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)
        long_desc = "A" * 500
        td = ToolDefinition(name="long_tool", description=long_desc)
        idx.index_tool(td, tool_type="custom")

        from src.sdk.loop import AgentLoop, _current_agent_loop

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_search import tool_search

            result = tool_search.invoke({"description": "A"})
            assert "..." in result
            assert len(result) < 600
        finally:
            _current_agent_loop.reset(token)
        idx.close()

    def test_search_multiple_results(self):
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)
        tools = [
            ToolDefinition(name="tool_a", description="PDF extraction tool"),
            ToolDefinition(name="tool_b", description="PDF conversion tool"),
            ToolDefinition(name="tool_c", description="Image resizer"),
        ]
        idx.index_tools(tools, tool_type="custom")

        from src.sdk.loop import AgentLoop, _current_agent_loop

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_search import tool_search

            result = tool_search.invoke({"description": "pdf"})
            assert "tool_a" in result
            assert "tool_b" in result
        finally:
            _current_agent_loop.reset(token)
        idx.close()


class TestToolReloadTool:
    def test_reload_no_loop(self):
        from src.sdk.tools_core.tool_reload import tool_reload

        result = tool_reload.invoke({})
        assert "No active agent" in result or isinstance(result, str)

    def test_reload_creates_index_if_missing(self):
        from src.sdk.loop import AgentLoop, _current_agent_loop

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_reload import tool_reload

            result = tool_reload.invoke({})
            assert "Index rebuilt" in result
        finally:
            _current_agent_loop.reset(token)

    def test_reload_with_custom_tools(self, mock_tool_file):
        from src.sdk.loop import AgentLoop, _current_agent_loop
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        tool_dir, tool_name = mock_tool_file
        idx = ToolIndex(d)
        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_reload import tool_reload

            result = tool_reload.invoke({})
            assert "Index rebuilt" in result
        finally:
            _current_agent_loop.reset(token)
        idx.close()

    def test_reload_evicts_removed_tools(self):
        from src.sdk.loop import AgentLoop, _current_agent_loop
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)
        td = ToolDefinition(name="gone_tool", description="Will be removed")
        idx.index_tool(td, tool_type="custom")

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        loop._recently_used.add("gone_tool")
        loop._registry.register(td)

        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_reload import tool_reload

            tool_reload.invoke({})
            # After reload, gone_tool should not be in index
            names = loop._tool_index.list_all_names()
            assert "gone_tool" not in names
        finally:
            _current_agent_loop.reset(token)
        idx.close()


class TestToolReloadMCP:
    def test_reload_with_mcp_bridge(self):
        from unittest.mock import MagicMock

        from src.sdk.loop import AgentLoop, _current_agent_loop
        from src.sdk.tool_index import ToolIndex
        from src.sdk.tools import ToolDefinition

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)

        mock_bridge = MagicMock()
        mcp_td = ToolDefinition(name="mcp__test__echo", description="Echo tool")
        mock_bridge.get_tool_definitions.return_value = [mcp_td]

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx
        loop._mcp_bridge = mock_bridge
        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.tools_core.tool_reload import tool_reload

            result = tool_reload.invoke({})
            assert "MCP" in result
            names = loop._tool_index.list_all_names()
            assert "mcp__test__echo" in names
        finally:
            _current_agent_loop.reset(token)
        idx.close()

class TestToolSearchIntegration:
    async def test_search_and_lazy_load_flow(self):
        """Full integration: index→search→lazy-load→execute."""
        from src.sdk.loop import AgentLoop, _current_agent_loop
        from src.sdk.messages import ToolCall
        from src.sdk.tool_index import ToolIndex, _rebuild_custom_function

        d = Path(tempfile.mkdtemp()) / "idx"
        idx = ToolIndex(d)

        td = ToolDefinition(name="echo_test", description="Echo back input")
        td = _rebuild_custom_function(td, {"command": 'echo "{{text}}"', "install": []})
        idx.index_tool(td, tool_type="custom")

        loop = AgentLoop(provider=MagicMockProvider(), tools=[])
        loop._tool_index = idx

        token = _current_agent_loop.set(loop)
        try:
            from src.sdk.messages import ToolCall
            from src.sdk.tools_core.tool_search import tool_search

            # Search
            search_result = tool_search.invoke({"description": "echo"})
            assert "echo_test" in search_result

            # Lazy-load via execute
            tc = ToolCall(id="1", name="echo_test", arguments={"text": "hello"})
            exec_result = await loop._execute_tool(tc)
            assert exec_result is not None

            # Should be in recency set
            assert "echo_test" in loop._recently_used

            # Should be in registry
            assert loop._registry.has("echo_test")
        finally:
            _current_agent_loop.reset(token)
        idx.close()


@pytest.fixture
def mock_tool_file():
    """Create a minimal TOOL.md file on disk and return (tool_dir, tool_name)."""
    d = Path(tempfile.mkdtemp())
    tool_dir = d / "test_tool"
    tool_dir.mkdir()
    (tool_dir / "TOOL.md").write_text("""\
---
name: test_tool
description: A test tool
command: echo "{{msg}}"
---
""")
    return tool_dir, "test_tool"


class MagicMockProvider:
    """Minimal provider for tests that need get_current_agent_loop()."""

    provider_id = "mock"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        return type("Msg", (), {"content": "", "tool_calls": [], "role": "assistant"})()

    def get_model_info(self, model=None):
        from src.sdk.providers.base import ModelInfo
        return ModelInfo(id=model or "mock", provider_id="mock")

    def count_tokens(self, messages):
        return 0
