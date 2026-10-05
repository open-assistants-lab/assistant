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
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

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


def _parse_server(namespaced: str) -> str:
    """Server name for a namespaced ``mcp__<server>__<tool>`` name."""
    parsed = _parse_mcp_tool_name(namespaced)
    return parsed[0] if parsed else ""


def _convert_tool_annotations(mcp_annotations: Any) -> ToolAnnotations:
    if mcp_annotations is None:
        # No annotations at all: unknown, not safe (#108).
        return ToolAnnotations(destructive=True)

    kwargs: dict[str, Any] = {}

    if hasattr(mcp_annotations, "title") and mcp_annotations.title:
        kwargs["title"] = mcp_annotations.title
    if hasattr(mcp_annotations, "readOnlyHint"):
        kwargs["read_only"] = bool(mcp_annotations.readOnlyHint)
    if hasattr(mcp_annotations, "destructiveHint"):
        # A hint the server left unset is UNKNOWN, not safe (#108): mapping
        # None -> False classified a tool that never declared itself safe as
        # parallel-safe. An unset hint on a tool that is not explicitly
        # read-only is treated as destructive (sequential/interrupt path);
        # an explicitly read-only tool stays non-destructive.
        read_only = bool(getattr(mcp_annotations, "readOnlyHint", None) or False)
        if mcp_annotations.destructiveHint is None:
            kwargs["destructive"] = not read_only
        else:
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

    def _resolve_direct_names(
        self, configured: set[str]
    ) -> tuple[set[str], set[str]]:
        manager = self._get_manager()
        cached_metadata = getattr(manager, "cached_metadata", None)
        records: list[dict[str, Any]] = (
            cast(list[dict[str, Any]], list(cached_metadata()))
            if callable(cached_metadata)
            else []
        )
        bare = {name for name in configured if "/" not in name and "__" not in name}
        bare_counts: dict[str, int] = {}
        for record in records:
            for metadata in record.get("tools", []) or []:
                name = str(metadata.get("name") or "")
                if name in bare:
                    bare_counts[name] = bare_counts.get(name, 0) + 1

        selected: set[str] = set()
        ambiguous: set[str] = set()
        for record in records:
            server_name = str(record.get("server_name") or "")
            for metadata in record.get("tools", []) or []:
                tool_name = str(metadata.get("name") or "")
                namespaced = _mcp_tool_name(server_name, tool_name)
                if (
                    namespaced in configured
                    or f"{server_name}/{tool_name}" in configured
                    or f"{server_name}__{tool_name}" in configured
                    or (tool_name in bare and bare_counts.get(tool_name) == 1)
                ):
                    selected.add(namespaced)
                elif tool_name in bare and bare_counts.get(tool_name, 0) > 1:
                    ambiguous.add(tool_name)
        return selected, ambiguous

    async def catalogue(self) -> list[Any]:
        """Return every available MCP tool, preferring live connections.

        Used by the exposure resolver, which needs the full catalogue before it
        can decide which tools may be direct. Falls back to the durable cache
        when no server is connected, so a decision can be made without starting
        anything.
        """
        connections = await self._get_manager().snapshot_connections()
        if connections:
            return [tool for conn in connections.values() for tool in conn.tools]
        return self.cached_catalogue()

    def cached_catalogue(self) -> list[Any]:
        """Build tool objects from the durable cache without connecting.

        This is what the ``auto`` policy measures (spec C1): deciding whether to
        defer a catalogue must not require opening the connections that create
        the cost we are trying to avoid.
        """
        from types import SimpleNamespace

        manager = self._get_manager()
        cached_metadata = getattr(manager, "cached_metadata", None)
        records = list(cached_metadata()) if callable(cached_metadata) else []
        catalogue: list[Any] = []
        for record in records:
            server_name = str(record.get("server_name") or "")
            for metadata in record.get("tools", []) or []:
                catalogue.append(
                    SimpleNamespace(
                        name=metadata.get("name", ""),
                        description=metadata.get("description", "") or "",
                        inputSchema=metadata.get("inputSchema", {}) or {},
                        annotations=metadata.get("annotations"),
                        meta={},
                        server_name=server_name,
                    )
                )
        return catalogue

    def has_cached_metadata(self) -> bool:
        """Whether the durable cache holds any server metadata (C2 gate)."""
        manager = self._get_manager()
        cached_metadata = getattr(manager, "cached_metadata", None)
        return bool(cached_metadata()) if callable(cached_metadata) else False

    async def build_definitions(self, names: Iterable[str]) -> list[ToolDefinition]:
        """Build ToolDefinitions for the given names from the live/cache catalogue.

        Used both to measure the cost of a candidate surface (Layer 3) and to
        register it, so what we measure is exactly what we would expose.
        """
        by_name = {str(t.name): t for t in await self.catalogue()}
        built: list[ToolDefinition] = []
        for name in sorted(set(names)):
            tool = by_name.get(name)
            if tool is None:
                continue
            server_name = getattr(tool, "server_name", None) or _parse_server(name)
            built.append(self._convert_mcp_tool(name, tool, server_name))
        return built

    async def resolve_exposure(
        self, *, settings: Any, caps: dict[str, Any], model: str
    ) -> Any:
        """Resolve this user's MCP exposure for one loop build.

        Centralised so the interactive path and the reload path cannot drift:
        both ask the same question through the same code, including the
        `auto` measurement (spec C1: measured from the durable cache, never
        from a live connection).
        """
        from src.sdk.mcp_exposure import measure_mode, parse_auto, resolve_exposure

        mcp_cfg = settings.mcp
        tools_cfg = getattr(settings, "tools", None)
        disabled_globs = tuple(getattr(tools_cfg, "disabled", []) or ())
        common: dict[str, Any] = {
            "include": list(getattr(mcp_cfg, "include_tools", []) or []),
            "exclude": list(getattr(mcp_cfg, "exclude_tools", []) or []),
            "disabled_globs": disabled_globs,
            "caps": caps,
            "operator_always_load": list(getattr(mcp_cfg, "always_load", []) or []),
            "server_trust": bool(getattr(mcp_cfg, "trust_server_exemptions", False)),
        }
        setting = str(mcp_cfg.exposure)
        auto_pct = parse_auto(setting)

        if auto_pct is None:
            return resolve_exposure(
                setting=setting, tools=await self.catalogue(), **common
            )

        catalogue = self.cached_catalogue()
        cache_warm = bool(catalogue) or self.has_cached_metadata()
        # Filters are mode-independent, so resolve once to learn the survivors,
        # measure their real definitions, then resolve again with the result.
        probe = resolve_exposure(setting="always", tools=catalogue, **common)
        candidates = await self.build_definitions(probe.survivors)
        measured, reason = measure_mode(
            candidates,
            model=model,
            threshold_pct=auto_pct,
            cache_warm=cache_warm,
            defer_when_unknown=bool(
                getattr(mcp_cfg, "defer_with_missing_metadata", True)
            ),
        )
        return resolve_exposure(
            setting=setting,
            tools=catalogue,
            measured_mode=measured,
            measurement_reason=reason,
            **common,
        )

    async def promote(self, decision: Any) -> tuple[list[ToolDefinition], list[ToolDefinition]]:
        """Build the tool sets for a resolved exposure decision.

        Returns ``(callable_tools, search_only_tools)``:

        * ``always`` — survivors are directly callable.
        * ``search`` — survivors carry their real schema so the tool index can
          match them, but they are NOT callable. That is precisely our existing
          ``tool_search`` contract: indexed and findable, absent from the loop's
          callable set, activated into the loop on demand.

        Stale promotions from a previous sync are dropped first.
        """
        wanted = set(decision.survivors)
        for name in [n for n, owner in self._tool_to_server.items() if n not in wanted]:
            self._registry.remove(name)
            self._tool_to_server.pop(name, None)

        callable_tools: list[ToolDefinition] = []
        search_only: list[ToolDefinition] = []
        for td in await self.build_definitions(wanted):
            if decision.mode == "search":
                search_only.append(td)
            else:
                self._registry.register(td)
                self._tool_to_server[td.name] = _parse_server(td.name)
                callable_tools.append(td)
        return callable_tools, search_only

    async def sync_direct_tools(self, direct_tools: set[str] | list[str]) -> dict[str, list[str]]:
        """Promote configured direct tools and remove definitions that are stale."""
        configured = set(direct_tools)
        allowed, ambiguous = self._resolve_direct_names(configured)
        old_names = set(self._tool_to_server)
        for name in old_names - allowed:
            self._registry.remove(name)
            self._tool_to_server.pop(name, None)
        await self.discover_cached(allowed)
        new_names = set(self._tool_to_server)

        from src.sdk.runner import refresh_user_tool_registries

        refresh_user_tool_registries(self.user_id, old_names | new_names)
        return {
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
            "ambiguous": sorted(ambiguous),
        }

    async def discover_cached(self, allowed_names: set[str] | None = None) -> int:
        """Promote allowlisted tools from durable metadata without starting MCP."""
        manager = self._get_manager()
        cached_metadata = getattr(manager, "cached_metadata", None)
        records: list[dict[str, Any]] = (
            cast(list[dict[str, Any]], list(cached_metadata()))
            if callable(cached_metadata)
            else []
        )
        total = 0
        for record in records:
            server_name = str(record.get("server_name") or "")
            for metadata in record.get("tools", []) or []:
                namespaced = _mcp_tool_name(server_name, str(metadata.get("name") or ""))
                if allowed_names is not None and namespaced not in allowed_names:
                    continue
                mcp_tool = SimpleNamespace(
                    name=metadata.get("name", ""),
                    description=metadata.get("description", "") or "",
                    inputSchema=metadata.get("inputSchema", {}) or {"type": "object"},
                    annotations=metadata.get("annotations"),
                )
                if self._registry.has(namespaced):
                    self._registry.remove(namespaced)
                self._registry.register(self._convert_mcp_tool(namespaced, mcp_tool, server_name))
                self._tool_to_server[namespaced] = server_name
                total += 1
        return total

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
