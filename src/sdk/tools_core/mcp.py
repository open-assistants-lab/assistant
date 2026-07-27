"""MCP tools — SDK-native implementation.

MCP (Model Context Protocol) server management: list, reload, and inspect tools.
All tools are now native async — no thread hack needed.
"""

from __future__ import annotations

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, ToolDefinition

logger = get_logger()


async def _mcp_list(user_id: str = "") -> str:
    if not user_id:
        return "Error: user_id is required."

    from src.sdk.tools_core.mcp_manager import get_mcp_manager

    manager = get_mcp_manager(user_id)
    await manager.initialize()
    servers = await manager.list_servers()

    if not servers:
        return "No MCP servers configured. Add .mcp.json to your user data directory."

    lines = ["MCP Servers:"]
    for name, info in servers.items():
        status = "running" if info["running"] else "stopped"
        lines.append(f"  - {name}: {status} ({info['tool_count']} tools)")

    return "\n".join(lines)


mcp_list = ToolDefinition(
    name="mcp_list",
    description="List configured MCP servers and their status.\n\nArgs:\n    user_id: User identifier (REQUIRED)",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "default": "", "title": "User Id"},
        },
    },
    annotations=ToolAnnotations(title="List MCP Servers", read_only=True, idempotent=True),
    function=_mcp_list,
)


async def _mcp_reload(user_id: str = "", session_id: str = "") -> str:
    if not user_id:
        return "Error: user_id is required."

    from src.sdk.tools_core.mcp_manager import get_mcp_manager

    manager = get_mcp_manager(user_id)
    await manager.initialize()
    result = await manager.reload()

    try:
        from src.sdk.capabilities import load_user_capabilities, resource_enabled
        from src.sdk.loop import get_current_agent_loop
        from src.sdk.runner import get_user_loop
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        loop = get_current_agent_loop() or get_user_loop(user_id, session_id=session_id or None)
        if loop is None:
            return f"{result} (no active conversation — tools will be picked up next conversation)"

        old_names = {
            t.name for t in loop._registry.list_tools()
            if t.name.startswith("mcp__")
        }

        bridge = getattr(loop, "_mcp_bridge", None)
        if bridge is None:
            bridge = MCPToolBridge(user_id=user_id)
            loop._mcp_bridge = bridge  # type: ignore[attr-defined]

        for name in old_names:
            loop.unregister_tool(name)
        bridge._tool_to_server = {}

        await bridge.discover()
        caps = load_user_capabilities(user_id)
        new_names: set[str] = set()
        for td in bridge.get_tool_definitions():
            if not resource_enabled(caps, "tools", td.name):
                continue
            loop.register_tool(td)
            new_names.add(td.name)

        removed = old_names - new_names
        parts = [f"{result} ({len(new_names)} MCP tools registered)"]
        if removed:
            parts.append(f"removed {len(removed)} stale tools")
        return " — ".join(parts)
    except Exception as e:
        logger.warning("mcp_reload.bridge_error", {"error": str(e)}, user_id=user_id)

    return result


mcp_reload = ToolDefinition(
    name="mcp_reload",
    description=(
        "Reload all MCP servers from configuration file.\n\n"
        "Use this when you've modified .mcp.json and want to apply changes.\n\n"
        "Args:\n    user_id: User identifier (REQUIRED)\n"
        "    session_id: Optional chat session identifier"
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "default": "", "title": "User Id"},
            "session_id": {"type": "string", "default": "", "title": "Session Id"},
        },
    },
    annotations=ToolAnnotations(title="Reload MCP Servers"),
    function=_mcp_reload,
)


async def _mcp_tools(user_id: str = "", server_name: str = "") -> str:
    if not user_id:
        return "Error: user_id is required."

    from src.sdk.tools_core.mcp_manager import get_mcp_manager

    manager = get_mcp_manager(user_id)
    await manager.initialize()
    tools = await manager.get_tools(server_name if server_name else None)

    if not tools:
        return "No MCP tools available."

    lines = ["Available MCP Tools:"]
    for t in tools:
        desc = t.description or "No description"
        lines.append(f"  - {t.name}: {desc[:80]}")

    return "\n".join(lines)


mcp_tools = ToolDefinition(
    name="mcp_tools",
    description=(
        "Get available tools from MCP servers.\n\n"
        "Args:\n    user_id: User identifier (REQUIRED)\n"
        "    server_name: Optional server name to filter tools"
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "default": "", "title": "User Id"},
            "server_name": {"type": "string", "default": "", "title": "Server Name"},
        },
    },
    annotations=ToolAnnotations(title="List MCP Tools", read_only=True, idempotent=True),
    function=_mcp_tools,
)
