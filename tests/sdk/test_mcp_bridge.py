"""Tests for MCPToolBridge — converting MCP tools to SDK ToolDefinitions."""

from unittest.mock import AsyncMock, MagicMock

from src.sdk.loop import AgentLoop
from src.sdk.messages import Message
from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolResult


class TestMCPToolNameFormat:
    def test_mcp_tool_name(self):
        from src.sdk.tools_core.mcp_bridge import _mcp_tool_name

        assert _mcp_tool_name("math", "add") == "mcp__math__add"
        assert _mcp_tool_name("github", "create_issue") == "mcp__github__create_issue"

    def test_parse_mcp_tool_name(self):
        from src.sdk.tools_core.mcp_bridge import _parse_mcp_tool_name

        result = _parse_mcp_tool_name("mcp__math__add")
        assert result == ("math", "add")

    def test_parse_mcp_tool_name_invalid(self):
        from src.sdk.tools_core.mcp_bridge import _parse_mcp_tool_name

        assert _parse_mcp_tool_name("regular_tool") is None
        assert _parse_mcp_tool_name("mcp__onlyone") is None

    def test_roundtrip(self):
        from src.sdk.tools_core.mcp_bridge import _mcp_tool_name, _parse_mcp_tool_name

        name = _mcp_tool_name("server", "tool")
        assert _parse_mcp_tool_name(name) == ("server", "tool")


class TestConvertToolAnnotations:
    def test_none_annotations(self):
        from src.sdk.tools_core.mcp_bridge import _convert_tool_annotations

        result = _convert_tool_annotations(None)
        assert isinstance(result, ToolAnnotations)
        assert result.read_only is False
        assert result.destructive is False

    def test_mcp_annotations(self):
        from src.sdk.tools_core.mcp_bridge import _convert_tool_annotations

        mock_ann = MagicMock()
        mock_ann.title = "Test Tool"
        mock_ann.readOnlyHint = True
        mock_ann.destructiveHint = False
        mock_ann.idempotentHint = True
        mock_ann.openWorldHint = False

        result = _convert_tool_annotations(mock_ann)
        assert result.title == "Test Tool"
        assert result.read_only is True
        assert result.idempotent is True


class TestMCPToolBridgeDiscover:
    async def test_discover_no_servers(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()
        mock_manager.snapshot_connections = AsyncMock(return_value={})
        bridge._manager = mock_manager

        count = await bridge.discover()
        assert count == 0
        assert bridge.get_tool_definitions() == []

    async def test_discover_with_tools(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "add"
        mcp_tool.description = "Add numbers"
        mcp_tool.inputSchema = {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
        mcp_tool.annotations = None

        conn = MagicMock()
        conn.tools = [mcp_tool]
        mock_manager.snapshot_connections = AsyncMock(return_value={"math": conn})
        bridge._manager = mock_manager

        count = await bridge.discover()
        assert count == 1

        tool_defs = bridge.get_tool_definitions()
        assert len(tool_defs) == 1
        assert tool_defs[0].name == "mcp__math__add"
        assert "[math]" in tool_defs[0].description
        assert tool_defs[0].parameters["properties"]["a"]["type"] == "integer"

    async def test_discover_multiple_servers(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        math_tool = MagicMock()
        math_tool.name = "add"
        math_tool.description = "Add"
        math_tool.inputSchema = {"type": "object", "properties": {}}
        math_tool.annotations = None

        db_tool = MagicMock()
        db_tool.name = "query"
        db_tool.description = "Query DB"
        db_tool.inputSchema = {"type": "object", "properties": {}}
        db_tool.annotations = None

        math_conn = MagicMock()
        math_conn.tools = [math_tool]
        db_conn = MagicMock()
        db_conn.tools = [db_tool]

        mock_manager.snapshot_connections = AsyncMock(return_value={"math": math_conn, "db": db_conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"math": math_conn, "db": db_conn}: _d.get(name))
        bridge._manager = mock_manager

        count = await bridge.discover()
        assert count == 2

        names = bridge.get_tool_names()
        assert "mcp__math__add" in names
        assert "mcp__db__query" in names


class TestMCPToolInvocation:
    async def test_invoke_mcp_tool(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "greet"
        mcp_tool.description = "Say hello"
        mcp_tool.inputSchema = {"type": "object", "properties": {"name": {"type": "string"}}}
        mcp_tool.annotations = None

        mock_result = MagicMock()
        text_content = MagicMock()
        text_content.text = "Hello, World!"
        mock_result.content = [text_content]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MagicMock()
        conn.tools = [mcp_tool]
        conn.session = mock_session
        mock_manager.snapshot_connections = AsyncMock(return_value={"greeter": conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"greeter": conn}: _d.get(name))
        bridge._manager = mock_manager

        await bridge.discover()
        tool_def = bridge._registry.get("mcp__greeter__greet")
        assert tool_def is not None

        result = await tool_def.ainvoke({"name": "World"})
        assert isinstance(result, ToolResult)
        assert "Hello, World!" in result.content
        assert result.is_error is False
        mock_session.call_tool.assert_called_once_with("greet", {"name": "World"})

    async def test_invoke_server_not_connected(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "greet"
        mcp_tool.description = "Say hello"
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.annotations = None

        conn = MagicMock()
        conn.tools = [mcp_tool]
        mock_manager.snapshot_connections = AsyncMock(return_value={"greeter": conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"greeter": conn}: _d.get(name))
        bridge._manager = mock_manager

        await bridge.discover()

        mock_manager.snapshot_connections = AsyncMock(return_value={})
        mock_manager.get_connection = AsyncMock(return_value=None)

        tool_def = bridge._registry.get("mcp__greeter__greet")
        result = await tool_def.ainvoke({})
        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "reconnecting" in result.content
        assert "not connected" not in result.content

    async def test_invoke_tool_error(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "fail"
        mcp_tool.description = "Always fails"
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.annotations = None

        mock_result = MagicMock()
        error_content = MagicMock()
        error_content.text = "Something went wrong"
        mock_result.content = [error_content]
        mock_result.isError = True

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MagicMock()
        conn.tools = [mcp_tool]
        conn.session = mock_session
        mock_manager.snapshot_connections = AsyncMock(return_value={"breaker": conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"breaker": conn}: _d.get(name))
        bridge._manager = mock_manager

        await bridge.discover()
        tool_def = bridge._registry.get("mcp__breaker__fail")
        result = await tool_def.ainvoke({})
        assert result.is_error is True

    async def test_invoke_exception(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "boom"
        mcp_tool.description = "Crashes"
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.annotations = None

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))

        conn = MagicMock()
        conn.tools = [mcp_tool]
        conn.session = mock_session
        mock_manager.snapshot_connections = AsyncMock(return_value={"crasher": conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"crasher": conn}: _d.get(name))
        bridge._manager = mock_manager

        await bridge.discover()
        tool_def = bridge._registry.get("mcp__crasher__boom")
        result = await tool_def.ainvoke({})
        assert result.is_error is True
        assert "connection lost" in result.content


class TestMCPToolBridgeReload:
    async def test_reload_rediscovers(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()
        mock_manager.reload = AsyncMock(return_value="MCP servers reloaded")

        mcp_tool = MagicMock()
        mcp_tool.name = "add"
        mcp_tool.description = "Add"
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.annotations = None

        conn = MagicMock()
        conn.tools = [mcp_tool]
        mock_manager.snapshot_connections = AsyncMock(return_value={"math": conn})
        bridge._manager = mock_manager

        await bridge.discover()
        assert len(bridge.get_tool_names()) == 1

        count = await bridge.reload()
        assert count == 1
        mock_manager.reload.assert_called_once()


class TestMCPToolBridgeRemove:
    async def test_remove_tools(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        mcp_tool = MagicMock()
        mcp_tool.name = "add"
        mcp_tool.description = "Add"
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.annotations = None

        conn = MagicMock()
        conn.tools = [mcp_tool]
        mock_manager.snapshot_connections = AsyncMock(return_value={"math": conn})
        bridge._manager = mock_manager

        await bridge.discover()
        assert len(bridge.get_tool_names()) == 1

        bridge.remove_tools()
        assert len(bridge.get_tool_names()) == 0


class TestAgentLoopDynamicRegistration:
    async def test_register_tool(self):
        from src.sdk.providers.base import LLMProvider, ModelInfo

        class SimpleProvider(LLMProvider):
            @property
            def provider_id(self):
                return "test"

            async def chat(self, messages, tools=None, model=None, **kwargs):
                return Message.assistant(content="ok")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                async def gen():
                    yield Message.assistant(content="ok")

                return gen()

            def count_tokens(self, text, model=None):
                return 1

            def get_model_info(self, model):
                return ModelInfo(id=model)

        td = ToolDefinition(
            name="dynamic_tool",
            description="A dynamically registered tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            function=lambda x: f"got {x}",
        )

        loop = AgentLoop(provider=SimpleProvider(), tools=[])
        assert not loop._registry.has("dynamic_tool")

        loop.register_tool(td)
        assert loop._registry.has("dynamic_tool")

    async def test_unregister_tool(self):
        from src.sdk.providers.base import LLMProvider, ModelInfo

        class SimpleProvider(LLMProvider):
            @property
            def provider_id(self):
                return "test"

            async def chat(self, messages, tools=None, model=None, **kwargs):
                return Message.assistant(content="ok")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                async def gen():
                    yield Message.assistant(content="ok")

                return gen()

            def count_tokens(self, text, model=None):
                return 1

            def get_model_info(self, model):
                return ModelInfo(id=model)

        td = ToolDefinition(
            name="temp_tool",
            description="Temporary",
            parameters={"type": "object", "properties": {}},
            function=lambda: "ok",
        )

        loop = AgentLoop(provider=SimpleProvider(), tools=[td])
        assert loop._registry.has("temp_tool")

        result = loop.unregister_tool("temp_tool")
        assert result is True
        assert not loop._registry.has("temp_tool")

    async def test_register_tool_overwrites(self):
        from src.sdk.providers.base import LLMProvider, ModelInfo

        class SimpleProvider(LLMProvider):
            @property
            def provider_id(self):
                return "test"

            async def chat(self, messages, tools=None, model=None, **kwargs):
                return Message.assistant(content="ok")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                async def gen():
                    yield Message.assistant(content="ok")

                return gen()

            def count_tokens(self, text, model=None):
                return 1

            def get_model_info(self, model):
                return ModelInfo(id=model)

        td1 = ToolDefinition(
            name="my_tool",
            description="V1",
            parameters={"type": "object", "properties": {}},
            function=lambda: "v1",
        )
        td2 = ToolDefinition(
            name="my_tool",
            description="V2",
            parameters={"type": "object", "properties": {}},
            function=lambda: "v2",
        )

        loop = AgentLoop(provider=SimpleProvider(), tools=[td1])
        loop.register_tool(td2)

        tool = loop._registry.get("my_tool")
        assert tool.description == "V2"


class TestDegradedMode:
    async def test_partial_server_failure(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()

        good_tool = MagicMock()
        good_tool.name = "add"
        good_tool.description = "Add"
        good_tool.inputSchema = {"type": "object", "properties": {}}
        good_tool.annotations = None

        good_conn = MagicMock()
        good_conn.tools = [good_tool]

        bad_conn = MagicMock()
        bad_conn.tools = []

        mock_manager.snapshot_connections = AsyncMock(return_value={"math": good_conn, "broken": bad_conn})
        mock_manager.get_connection = AsyncMock(side_effect=lambda name, _d={"math": good_conn, "broken": bad_conn}: _d.get(name))
        bridge._manager = mock_manager

        count = await bridge.discover()
        assert count == 1
        assert "mcp__math__add" in bridge.get_tool_names()

    async def test_server_start_failure_is_skipped(self):
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        bridge = MCPToolBridge(user_id="test")
        mock_manager = MagicMock()
        mock_manager._ensure_started = AsyncMock()
        mock_manager.snapshot_connections = AsyncMock(return_value={})
        mock_manager.get_connection = AsyncMock(return_value=None)

        bridge._manager = mock_manager

        count = await bridge.discover()
        assert count == 0
        assert bridge.get_tool_definitions() == []
