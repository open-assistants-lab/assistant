from types import SimpleNamespace
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

    async def catalogue(self):
        return [
            SimpleNamespace(
                name=td.name,
                description=td.description,
                inputSchema={},
                annotations=None,
                meta={},
                server_name="math",
            )
            for td in self.get_tool_definitions()
        ]

    async def resolve_exposure(self, *, settings, caps, model):
        from src.sdk.mcp_exposure import measure_mode, parse_auto, resolve_exposure
        from src.sdk.tools import ToolDefinition

        mcp_cfg = settings.mcp
        setting = str(mcp_cfg.exposure)
        catalogue = await self.catalogue()
        measured_mode = None
        auto_pct = parse_auto(setting)
        if auto_pct is not None:
            # Mirror the real bridge: measure real definitions, warm cache.
            defs = [
                ToolDefinition(name=t.name, description=t.description, parameters={})
                for t in catalogue
            ]
            measured_mode, _reason = measure_mode(
                defs, model=model or "openai:gpt-4o", threshold_pct=auto_pct,
                cache_warm=bool(defs),
            )
        return resolve_exposure(
            setting=setting,
            measured_mode=measured_mode,
            tools=catalogue,
            caps=caps,
            include=list(getattr(mcp_cfg, "include_tools", []) or []),
            exclude=list(getattr(mcp_cfg, "exclude_tools", []) or []),
            disabled_globs=tuple(getattr(settings.tools, "disabled", []) or ()),
            operator_always_load=list(getattr(mcp_cfg, "always_load", []) or []),
            server_trust=bool(getattr(mcp_cfg, "trust_server_exemptions", False)),
        )

    async def promote(self, decision):
        """Return (callable, search_only) per the resolved exposure decision."""
        from src.sdk.tools import ToolDefinition

        callable_tools, search_only = [], []
        wanted = set(decision.survivors)
        for td in self.get_tool_definitions():
            if td.name not in wanted:
                continue
            if decision.mode == "search":
                search_only.append(td)
            else:
                callable_tools.append(
                    ToolDefinition(
                        name=td.name,
                        description=td.description,
                        parameters=td.parameters,
                        annotations=td.annotations,
                        function=td.function,
                    )
                )
        return callable_tools, search_only

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


async def test_refresh_uses_current_loop_with_multiple_active_sessions():
    from src.sdk import runner
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_proxy

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
            result = await mcp_proxy.ainvoke({"user_id": "u", "action": "refresh"})
    finally:
        _current_agent_loop.reset(token)
        runner._user_loops.clear()

    assert "1 MCP tools registered" in result.content
    assert current_loop.registered == ["mcp__math__add"]
    assert other_loop.registered == []


async def test_refresh_does_not_register_disabled_mcp_tool(monkeypatch, tmp_path):
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_proxy

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
            result = await mcp_proxy.ainvoke({"user_id": "u", "action": "refresh"})
    finally:
        _current_agent_loop.reset(token)

    assert "0 MCP tools registered" in result.content
    assert loop.registered == []


async def test_refresh_registers_unconfigured_destructive_mcp_tool(monkeypatch, tmp_path):
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.mcp import mcp_proxy

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
            result = await mcp_proxy.ainvoke({"user_id": "u", "action": "refresh"})
    finally:
        _current_agent_loop.reset(token)

    assert "1 MCP tools registered" in result.content
    assert loop.registered == ["mcp__fs__delete"]


class FailingBridge(FakeBridge):
    """Cataloguing fails after the session's mcp__* tools were unregistered."""

    async def catalogue(self):
        raise RuntimeError("server handshake failed")


async def test_refresh_failure_is_reported_not_swallowed(monkeypatch):
    """Issue #30: a failed reload must not report a clean success.

    By the time discover() runs, the session's mcp__* tools have already been
    unregistered, so falling through to a success string claimed a reload that
    silently cost the session its MCP tools.
    """
    from src.sdk import runner
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools import ToolResult
    from src.sdk.tools_core.mcp import mcp_proxy

    loop = FakeLoop()
    runner._user_loops.clear()
    runner.register_user_loop("u", loop, session_id="chat-1")
    token = _current_agent_loop.set(loop)

    try:
        with (
            patch("src.sdk.tools_core.mcp_manager.get_mcp_manager", return_value=FakeManager()),
            patch("src.sdk.tools_core.mcp_bridge.MCPToolBridge", FailingBridge),
        ):
            result = await mcp_proxy.ainvoke({"user_id": "u", "action": "refresh"})
    finally:
        _current_agent_loop.reset(token)

    assert isinstance(result, ToolResult), result
    assert result.is_error is True, result
    assert "MCP reload failed" in result.content, result
    assert "handshake" in result.content, result
