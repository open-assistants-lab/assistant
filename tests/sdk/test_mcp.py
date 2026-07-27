from unittest.mock import AsyncMock, MagicMock, patch


class FakeLoop:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self._mcp_bridge = None
        self._registry = MagicMock()
        self._registry.list_tools.return_value = []

    def register_tool(self, tool_def):
        self.registered.append(tool_def.name)

    def unregister_tool(self, name):
        self.unregistered.append(name)


class FakeManager:
    def __init__(self):
        self.initialize = AsyncMock()
        self.reload = AsyncMock(return_value="MCP reloaded")


class FakeBridge:
    def __init__(self, user_id):
        self.user_id = user_id
        self._tool_to_server = {"stale": "server"}

    async def discover(self):
        return 1

    def get_tool_definitions(self):
        from src.sdk.tools import ToolDefinition

        return [
            ToolDefinition(
                name="mcp__math__add",
                description="Add",
                parameters={},
                function=lambda: "ok",
            )
        ]


class DestructiveFakeBridge(FakeBridge):
    def get_tool_definitions(self):
        from src.sdk.tools import ToolAnnotations, ToolDefinition

        return [
            ToolDefinition(
                name="mcp__fs__delete",
                description="Delete",
                parameters={},
                annotations=ToolAnnotations(destructive=True),
                function=lambda: "ok",
            )
        ]


async def test_mcp_reload_uses_current_loop_with_multiple_active_sessions():
    from src.sdk import runner
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_reload

    current_loop = FakeLoop()
    other_loop = FakeLoop()
    runner._user_loops.clear()
    runner.register_user_loop("u", current_loop, session_id="chat-1")
    runner.register_user_loop("u", other_loop, session_id="chat-2")
    token = _current_agent_loop.set(current_loop)

    try:
        with (
            patch("src.sdk.tools_core.mcp_manager.get_mcp_manager", return_value=FakeManager()),
            patch("src.sdk.tools_core.mcp_bridge.MCPToolBridge", FakeBridge),
        ):
            result = await mcp_reload.ainvoke({"user_id": "u"})
    finally:
        _current_agent_loop.reset(token)
        runner._user_loops.clear()

    assert "1 MCP tools registered" in result
    assert current_loop.registered == ["mcp__math__add"]
    assert other_loop.registered == []


async def test_mcp_reload_does_not_register_disabled_mcp_tool(monkeypatch, tmp_path):
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_reload

    loop = FakeLoop()
    token = _current_agent_loop.set(loop)
    caps_root = tmp_path / "caps"
    caps_root.mkdir()
    (caps_root / "capabilities.yaml").write_text(
        "tools:\n  mcp__math__add: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.sdk.capabilities.user_capabilities_root",
        lambda user_id: caps_root,
    )

    try:
        with (
            patch("src.sdk.tools_core.mcp_manager.get_mcp_manager", return_value=FakeManager()),
            patch("src.sdk.tools_core.mcp_bridge.MCPToolBridge", FakeBridge),
        ):
            result = await mcp_reload.ainvoke({"user_id": "u"})
    finally:
        _current_agent_loop.reset(token)

    assert "0 MCP tools registered" in result
    assert loop.registered == []


async def test_mcp_reload_registers_unconfigured_destructive_mcp_tool(monkeypatch, tmp_path):
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_reload

    loop = FakeLoop()
    token = _current_agent_loop.set(loop)
    caps_root = tmp_path / "caps"
    caps_root.mkdir()
    (caps_root / "capabilities.yaml").write_text("tools: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.sdk.capabilities.user_capabilities_root",
        lambda user_id: caps_root,
    )

    try:
        with (
            patch("src.sdk.tools_core.mcp_manager.get_mcp_manager", return_value=FakeManager()),
            patch("src.sdk.tools_core.mcp_bridge.MCPToolBridge", DestructiveFakeBridge),
        ):
            result = await mcp_reload.ainvoke({"user_id": "u"})
    finally:
        _current_agent_loop.reset(token)

    assert "1 MCP tools registered" in result
    assert loop.registered == ["mcp__fs__delete"]
