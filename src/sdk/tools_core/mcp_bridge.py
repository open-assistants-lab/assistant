"""MCP Tool Bridge — converts MCP server tools into SDK ToolDefinitions.

The bridge discovers tools from MCP servers via MCPManager and creates
SDK-native ToolDefinition instances with namespaced names (mcp__{server}__{tool}).
When invoked, the ToolDefinition routes the call back through the MCP session.

This replaces the meta-tools approach (mcp_list/mcp_tools) which only let
the LLM *inspect* MCP tools but not *invoke* them.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolRegistry, ToolResult
from src.sdk.tools_core.mcp_manager import MCPManager, get_mcp_manager

logger = logging.getLogger(__name__)


def _mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp__{server_name}__{tool_name}"


def _parse_mcp_tool_name(namespaced: str) -> tuple[str, str] | None:
    parts = namespaced.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp":
        return None
    return parts[1], parts[2]


def _convert_tool_annotations(mcp_annotations: Any) -> ToolAnnotations:
    if mcp_annotations is None:
        return ToolAnnotations()

    kwargs: dict[str, Any] = {}

    if hasattr(mcp_annotations, "title") and mcp_annotations.title:
        kwargs["title"] = mcp_annotations.title
    if hasattr(mcp_annotations, "readOnlyHint"):
        kwargs["read_only"] = bool(mcp_annotations.readOnlyHint)
    if hasattr(mcp_annotations, "destructiveHint"):
        kwargs["destructive"] = bool(mcp_annotations.destructiveHint)
    if hasattr(mcp_annotations, "idempotentHint"):
        kwargs["idempotent"] = bool(mcp_annotations.idempotentHint)
    if hasattr(mcp_annotations, "openWorldHint"):
        kwargs["open_world"] = bool(mcp_annotations.openWorldHint)

    return ToolAnnotations(**kwargs)


class MCPToolBridge:
    """Converts MCP server tools into SDK ToolDefinitions and routes invocations.

    Usage:
        bridge = MCPToolBridge(user_id="alice")
        await bridge.discover()
        tool_defs = bridge.get_tool_definitions()
        # tool_defs can be passed to AgentLoop(tools=...)

        # Later, when the agent calls an MCP tool:
        result = await tool_def.ainvoke({"query": "hello"})
        # The bridge routes through MCPManager → MCP session → call_tool()
    """

    def __init__(self, user_id: str, registry: ToolRegistry | None = None) -> None:
        self.user_id = user_id
        self._registry = registry or ToolRegistry()
        self._tool_to_server: dict[str, str] = {}
        self._manager: MCPManager | None = None

    def _get_manager(self) -> MCPManager:
        if self._manager is None:
            self._manager = get_mcp_manager(self.user_id)
            self._manager.add_refresh_listener(self._refresh_server)
        return self._manager

    def detach(self) -> None:
        """Stop receiving manager refresh notifications (loop evicted/reset).

        Idempotent; safe on bridges whose manager was never created.
        """
        if self._manager is not None:
            self._manager.remove_refresh_listener(self._refresh_server)
            self._manager = None

    async def bootstrap_refresh(self) -> None:
        """Re-discover every configured server's live tool list at loop
        creation (LC-4) — a session born after the server changed its catalog
        picks up the new tools without any .mcp.json byte change."""
        manager = self._get_manager()
        await manager.rediscover()
        # Apply refreshed catalogs to this bridge's own registry (the bridge
        # may not be a live refresh listener — it may have been detached).
        for server_name in await manager.snapshot_connections():
            await self._refresh_server(server_name)

    async def _refresh_server(self, server_name: str) -> None:
        """Replace one server's definitions and refresh all live loop registries."""
        manager = self._get_manager()
        conn = await manager.get_connection(server_name)
        if conn is None:
            return
        old_names = {
            name for name, owner in self._tool_to_server.items() if owner == server_name
        }
        for name in old_names:
            self._registry.remove(name)
            self._tool_to_server.pop(name, None)
        new_names: set[str] = set()
        for mcp_tool in conn.tools:
            namespaced = _mcp_tool_name(server_name, mcp_tool.name)
            self._registry.register(self._convert_mcp_tool(namespaced, mcp_tool, server_name))
            self._tool_to_server[namespaced] = server_name
            new_names.add(namespaced)

        from src.sdk.runner import refresh_user_tool_registries

        refresh_user_tool_registries(self.user_id, old_names | new_names)

    async def _ensure_connection(
        self, manager: MCPManager, server_name: str, *, force_reconnect: bool = False
    ) -> Any:
        """Use lifecycle-aware managers while retaining lightweight test/legacy adapters."""
        ensure = getattr(manager, "ensure_connection", None)
        if ensure is not None and inspect.iscoroutinefunction(ensure):
            return await ensure(server_name, force_reconnect=force_reconnect)
        return await manager.get_connection(server_name)

    async def discover(self) -> int:
        """Discover tools from all MCP servers and convert to ToolDefinitions.

        Returns the number of tools discovered.
        """
        manager = self._get_manager()
        await manager._ensure_started()

        connections = await manager.snapshot_connections()
        if not connections:
            return 0

        total = 0
        for server_name, conn in connections.items():
            for mcp_tool in conn.tools:
                namespaced = _mcp_tool_name(server_name, mcp_tool.name)
                td = self._convert_mcp_tool(namespaced, mcp_tool, server_name)

                if self._registry.has(namespaced):
                    self._registry.remove(namespaced)

                self._registry.register(td)
                self._tool_to_server[namespaced] = server_name
                total += 1

        logger.info(
            f"mcp_bridge.discovered tools={total} servers={len(connections)}",
            extra={"user_id": self.user_id},
        )
        return total

    def _convert_mcp_tool(
        self, namespaced_name: str, mcp_tool: Any, server_name: str
    ) -> ToolDefinition:
        parameters = getattr(mcp_tool, "inputSchema", {}) or {
            "type": "object",
            "properties": {},
        }

        annotations = _convert_tool_annotations(getattr(mcp_tool, "annotations", None))

        if not annotations.title and mcp_tool.name:
            display_name = mcp_tool.name.replace("-", " ").replace("_", " ").title()
            annotations = ToolAnnotations(
                title=display_name,
                read_only=annotations.read_only,
                destructive=annotations.destructive,
                idempotent=annotations.idempotent,
                open_world=annotations.open_world,
            )

        description = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
        description = f"[{server_name}] {description}"

        async def _invoke(**kwargs: Any) -> ToolResult:
            manager = self._get_manager()
            conn = await self._ensure_connection(manager, server_name)
            if conn is None:
                return ToolResult(
                    content=f"MCP server '{server_name}' is reconnecting; retry shortly",
                    is_error=True,
                )

            try:
                result = await conn.session.call_tool(mcp_tool.name, kwargs)
                text_parts = []
                for content_block in result.content:
                    if hasattr(content_block, "text"):
                        text_parts.append(content_block.text)
                    else:
                        text_parts.append(str(content_block))

                content = "\n".join(text_parts) if text_parts else ""
                is_error = getattr(result, "isError", False) or False
                return ToolResult(content=content, is_error=is_error)
            except Exception as e:
                logger.error(
                    f"mcp_bridge.call_error tool={namespaced_name}: {e}",
                    extra={"user_id": self.user_id},
                )
                conn = await self._ensure_connection(manager, server_name, force_reconnect=True)
                if conn is None:
                    return ToolResult(
                        content=f"MCP server '{server_name}' is reconnecting; retry shortly",
                        is_error=True,
                    )
                try:
                    result = await conn.session.call_tool(mcp_tool.name, kwargs)
                    text_parts = [
                        block.text if hasattr(block, "text") else str(block)
                        for block in result.content
                    ]
                    return ToolResult(
                        content="\n".join(text_parts),
                        is_error=bool(getattr(result, "isError", False)),
                    )
                except Exception as retry_error:
                    return ToolResult(content=str(retry_error), is_error=True)

        return ToolDefinition(
            name=namespaced_name,
            description=description,
            parameters=parameters,
            annotations=annotations,
            function=_invoke,
        )

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return self._registry.list_tools()

    def get_tool_definition(self, name: str) -> ToolDefinition | None:
        """Get a single tool definition by namespaced name (mcp__{server}__{tool})."""
        return self._registry.get(name)

    def get_tool_names(self) -> list[str]:
        return self._registry.list_names()

    async def reload(self) -> int:
        """Reload MCP servers and re-discover tools."""
        manager = self._get_manager()
        await manager.reload()
        self._registry = ToolRegistry()
        self._tool_to_server = {}
        return await self.discover()

    def remove_tools(self) -> None:
        """Remove all MCP tools from the registry."""
        for name in self._tool_to_server:
            self._registry.remove(name)
        self._tool_to_server = {}
