"""MCP tools — SDK-native implementation.

MCP (Model Context Protocol) server management: list, reload, and inspect tools.
All tools are now native async — no thread hack needed.
"""

from __future__ import annotations

import json
from typing import Any

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolResult
from src.sdk.tools_core.mcp_results import mcp_error_result, mcp_result_to_tool_result

logger = get_logger()


def _cached_records(manager: Any) -> list[dict[str, Any]]:
    getter = getattr(manager, "cached_metadata", None)
    if callable(getter):
        return list(getter())
    return []


def _annotation_value(annotations: Any, name: str) -> bool:
    if annotations is None:
        return False
    if isinstance(annotations, dict):
        return bool(annotations.get(name, False))
    return bool(getattr(annotations, name, False))


async def _mcp_proxy(
    user_id: str = "",
    action: str = "search",
    server: str = "",
    tool: str = "",
    arguments: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    session_id: str = "",
) -> ToolResult:
    if not user_id:
        return mcp_error_result("user_id is required", server_name=server, tool_name=tool, outcome="failed")

    from src.sdk.tools_core.mcp_manager import get_mcp_manager

    manager = get_mcp_manager(user_id)
    action = action.strip().lower()
    if action in {"search", "list", "status"}:
        records = _cached_records(manager)
        if query:
            needle = query.casefold()
            records = [
                record
                for record in records
                if needle in json.dumps(record, ensure_ascii=False).casefold()
            ]
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        records = records[bounded_offset : bounded_offset + bounded_limit]
        lines = [
            f"{record.get('server_name', '')}: "
            f"{len(record.get('tools', []) or [])} cached tools "
            f"({record.get('source_type', 'unknown')})"
            for record in records
        ]
        return ToolResult(
            content="\n".join(lines) or "No cached MCP servers.",
            structured_content={"outcome": "succeeded", "servers": records},
        )

    if action == "refresh":
        from src.sdk.tools_core.mcp import reload_for_loop

        try:
            summary = await reload_for_loop(user_id, session_id=session_id)
        except Exception as exc:
            # Issue #30: by the time discovery runs, the session's mcp__* tools
            # have already been unregistered, so a bare success string would
            # claim a reload that silently cost the session its MCP tools.
            return ToolResult(
                content=(
                    f"MCP reload failed: {type(exc).__name__}: {exc}. The session's MCP "
                    "tool list may be stale or incomplete until the next successful reload."
                ),
                is_error=True,
                structured_content={
                    "outcome": "uncertain",
                    "receipt_class": "mcp_proxy",
                    "action": "refresh",
                },
            )
        return ToolResult(
            content=summary,
            structured_content={
                "outcome": "succeeded",
                "receipt_class": "mcp_proxy",
                "action": "refresh",
            },
        )

    if not server or not tool:
        return mcp_error_result(
            "server and tool are required for this action",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    if action == "describe":
        for record in _cached_records(manager):
            if record.get("server_name") != server:
                continue
            for metadata in record.get("tools", []) or []:
                if metadata.get("name") == tool:
                    return ToolResult(
                        content=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        structured_content={
                            "outcome": "succeeded",
                            "server": server,
                            "tool": tool,
                            "source_path": record.get("source_path"),
                            "source_type": record.get("source_type"),
                        },
                    )
        return mcp_error_result(
            f"MCP tool '{server}/{tool}' is not in the metadata cache",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    if action != "call":
        return mcp_error_result(
            f"Unsupported MCP proxy action: {action}",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    try:
        tools = await manager.get_tools(server)
    except Exception as exc:
        return mcp_error_result(exc, server_name=server, tool_name=tool)
    selected = next((item for item in tools if getattr(item, "name", None) == tool), None)
    if selected is None:
        return mcp_error_result(
            f"MCP tool '{server}/{tool}' is not available",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    annotations = getattr(selected, "annotations", None)
    read_only = _annotation_value(annotations, "readOnlyHint")
    idempotent = _annotation_value(annotations, "idempotentHint")
    namespaced = f"mcp__{server}__{tool}"
    from src.sdk.capabilities import load_user_capabilities, tool_enabled

    capabilities = load_user_capabilities(user_id)
    if not tool_enabled(capabilities, namespaced):
        return mcp_error_result(
            f"MCP tool '{namespaced}' is disabled by user capabilities",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    from src.sdk.governance import get_governance_service

    permission = get_governance_service(user_id).resolve_permission_for_call(
        user_id, namespaced, dict(arguments or args or {})
    )
    if permission == "deny":
        return mcp_error_result(
            f"MCP tool '{namespaced}' is denied by policy",
            server_name=server,
            tool_name=tool,
            outcome="failed",
        )

    call_args = dict(arguments if arguments is not None else args or {})

    async def call_once(force_reconnect: bool = False) -> Any:
        connection = await manager.ensure_connection(server, force_reconnect=force_reconnect)
        if connection is None:
            raise ConnectionError(f"MCP server '{server}' is reconnecting")
        caller = getattr(manager, "call_tool", None)
        if callable(caller):
            return await caller(server, tool, call_args)
        return await connection.session.call_tool(tool, call_args)

    try:
        result = await call_once()
    except Exception as first_error:
        if not (read_only or idempotent):
            return mcp_error_result(first_error, server_name=server, tool_name=tool)
        try:
            result = await call_once(force_reconnect=True)
        except Exception as retry_error:
            return mcp_error_result(retry_error, server_name=server, tool_name=tool)

    return mcp_result_to_tool_result(
        result,
        server_name=server,
        tool_name=tool,
        max_chars=get_settings().mcp.max_result_chars,
    )


mcp_proxy = ToolDefinition(
    name="mcp_proxy",
    description=(
        "The single governed MCP surface. Actions: 'search' (cached metadata, "
        "no connection), 'status', 'describe', 'refresh' (reload servers and "
        "re-sync this conversation's tools), and 'call' (invoke one tool, "
        "re-validating capability and permission policy first). Calls are "
        "audited and may require approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "default": "", "title": "User Id"},
            "action": {
                "type": "string",
                "enum": ["search", "status", "describe", "refresh", "call"],
                "default": "search",
                "title": "Action",
            },
            "server": {"type": "string", "default": "", "title": "Server Name"},
            "tool": {"type": "string", "default": "", "title": "Tool Name"},
            "arguments": {"type": "object", "default": {}, "title": "Tool Arguments"},
            "args": {"type": "object", "default": {}, "title": "Tool Arguments"},
            "query": {"type": "string", "default": "", "title": "Search Query"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 20,
                "title": "Result Limit",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "title": "Result Offset",
            },
            "session_id": {"type": "string", "default": "", "title": "Session Id"},
        },
        "required": ["user_id", "action"],
    },
    annotations=ToolAnnotations(
        title="MCP Proxy",
        read_only=False,
        destructive=True,
        open_world=True,
    ),
    function=_mcp_proxy,
)


async def reload_for_loop(
    user_id: str,
    loop: Any | None = None,
    *,
    session_id: str = "",
) -> str:
    """Reload MCP servers and re-sync the loop's direct tool surface.

    Layer 1 of the exposure redesign: this was the body of the `mcp_reload`
    tool, which is removed. It is now an entry point used by the
    `mcp_proxy(action="refresh")` action and by the HTTP route.
    """
    from src.sdk.capabilities import load_user_capabilities, resource_enabled
    from src.sdk.loop import get_current_agent_loop
    from src.sdk.runner import get_user_loop
    from src.sdk.tools_core.mcp_bridge import MCPToolBridge
    from src.sdk.tools_core.mcp_manager import get_mcp_manager

    manager = get_mcp_manager(user_id)
    result = await manager.reload()

    try:
        if loop is None:
            loop = get_current_agent_loop() or get_user_loop(
                user_id, session_id=session_id or None
            )
        if loop is None:
            return f"{result} (no active conversation — tools will be picked up next conversation)"

        old_names = {t.name for t in loop._registry.list_tools() if t.name.startswith("mcp__")}

        bridge = getattr(loop, "_mcp_bridge", None)
        if bridge is None:
            bridge = MCPToolBridge(user_id=user_id)
            loop._mcp_bridge = bridge  # type: ignore[union-attr]

        for name in old_names:
            loop.unregister_tool(name)
        bridge._tool_to_server = {}

        mcp_cfg = get_settings().mcp
        from src.sdk.mcp_exposure import resolve_exposure

        decision = resolve_exposure(
            setting=str(mcp_cfg.exposure),
            tools=await bridge.catalogue(),
            include=list(mcp_cfg.include_tools or []),
            exclude=list(mcp_cfg.exclude_tools or []),
            disabled_globs=tuple(getattr(get_settings().tools, "disabled", []) or ()),
            caps=load_user_capabilities(user_id),
            operator_always_load=list(mcp_cfg.always_load or []),
            server_trust=bool(getattr(mcp_cfg, "trust_server_exemptions", False)),
        )
        if decision.mode == "never":
            return f"{result} ({mcp_cfg.exposure} exposure active; direct MCP tools remain hidden)"

        callable_tools, _search_only = await bridge.promote(decision)

        caps = load_user_capabilities(user_id)
        new_names: set[str] = set()
        for td in callable_tools:
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
        # The session's mcp__* tools have already been unregistered by this
        # point, so falling through to a success string reported a clean reload
        # while the session had silently lost its MCP tools (issue #30).
        raise
