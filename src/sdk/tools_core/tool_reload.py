from __future__ import annotations

from pathlib import Path

from src.sdk.capabilities import load_user_capabilities
from src.sdk.deployment_tools import filter_denied_native_tools
from src.sdk.loop import get_current_agent_loop
from src.sdk.tool_index import (
    ToolIndex,
    compute_source_hashes,
    save_source_hashes,
)
from src.sdk.tools import ToolResult, tool
from src.storage.paths import DEFAULT_USER_ID


def _scan_custom_tool_names(tools_dir: Path) -> set[str]:
    names: set[str] = set()
    if not tools_dir.exists():
        return names
    for entry in tools_dir.iterdir():
        if entry.is_dir() and (entry / "TOOL.md").exists():
            names.add(entry.name)
    return names


@tool
def tool_reload() -> str:
    """Reload and re-index all tools from current sources. Use after creating, editing, or deleting a TOOL.md file.

    MCP servers must be reconnected via `mcp_reload()` first — this only re-indexes
    whatever MCP tools are already registered in the bridge.

    Shipped native tools are deployment-managed by `tools.native`; policy-excluded tools remain unavailable and do not need reloading.

    Returns:
        Summary of tools added, removed, or changed
    """
    loop = get_current_agent_loop()
    if loop is None:
        return "No active agent session."

    if not hasattr(loop, "_tool_index") or loop._tool_index is None:
        from src.storage.paths import get_paths
        paths = get_paths(user_id=loop.user_id or DEFAULT_USER_ID, workspace_id=loop.workspace_id or "personal")
        index_dir = paths.user_tools_dir() / ".index"
        index_dir.mkdir(parents=True, exist_ok=True)
        loop._tool_index = ToolIndex(index_dir)

    from src.storage.paths import get_paths

    paths = get_paths(user_id=loop.user_id or DEFAULT_USER_ID, workspace_id=loop.workspace_id or "personal")
    user_tools_dir = paths.user_tools_dir()
    workspace_tools_dir = paths.workspace_tools_dir()
    mcp_config = paths.user_mcp_config()
    index_dir = user_tools_dir / ".index"
    hashes_path = index_dir / ".index_hashes.json"

    try:
        prev_names = set(loop._tool_index.list_all_names())
        managed_names = {
            name
            for name in prev_names
            if loop._tool_index.get_tool_type(name) in {"custom", "mcp"}
        }
        loop._tool_index.clear()

        from src.config import get_settings
        from src.sdk.native_tools import get_native_tools
        from src.sdk.tool_index import desired_index_rows, index_rows
        from src.sdk.tools_custom import get_custom_tools

        user_id = loop.user_id or DEFAULT_USER_ID
        caps = load_user_capabilities(user_id)
        settings = get_settings()
        mcp_bridge = getattr(loop, "_mcp_bridge", None)

        # Build from the same definition the session runner uses, so the two
        # writers cannot disagree about which rows belong in the index (#28,
        # #29). Clearing first means every desired row must be written here —
        # including native ones, whose row is the only route to a non-core
        # native tool.
        rows = desired_index_rows(
            native_tools=filter_denied_native_tools(list(get_native_tools()), settings),
            custom_tools=get_custom_tools(
                user_id=user_id, workspace_id=loop.workspace_id or "personal"
            ),
            mcp_tools=(
                list(mcp_bridge.get_tool_definitions()) if mcp_bridge else []
            ),
            caps=caps,
            user_id=user_id,
            workspace_id=loop.workspace_id or "personal",
        )
        index_rows(loop._tool_index, rows)

        current_managed_names: set[str] = set()
        native_count = custom_count = mcp_count = 0
        for row in rows:
            if row.tool_type == "native":
                native_count += 1
            elif row.tool_type == "custom":
                custom_count += 1
                current_managed_names.add(row.name)
            elif row.tool_type == "mcp":
                mcp_count += 1
                current_managed_names.add(row.name)

        current_hashes = compute_source_hashes(
            user_tools_dir, workspace_tools_dir, mcp_config,
        )
        save_source_hashes(hashes_path, current_hashes)

        new_names = set(loop._tool_index.list_all_names())
        added = new_names - prev_names
        removed = prev_names - new_names
        # Refresh every cached/live loop for this user in place. Include all
        # custom names so newly-created tools become callable immediately.
        from src.sdk.runner import refresh_user_tool_registries

        refresh_user_tool_registries(user_id, managed_names | current_managed_names)

        lines = [f"Index rebuilt ({native_count} native, {custom_count} custom, {mcp_count} MCP)."]
        if added:
            lines.append(f"  Added: {', '.join(sorted(added))}")
        if removed:
            lines.append(f"  Removed: {', '.join(sorted(removed))}")
        if not added and not removed:
            lines.append("  No changes detected.")
        return "\n".join(lines)
    except Exception as e:
        return ToolResult(content=f'Error rebuilding tool index: {e}', is_error=True)
