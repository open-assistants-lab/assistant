from __future__ import annotations

from pathlib import Path

from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.loop import get_current_agent_loop
from src.sdk.tool_index import (
    ToolIndex,
    compute_source_hashes,
    save_source_hashes,
)
from src.sdk.tools import tool
from src.sdk.tools_custom import is_core_tool
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

    Native tools (built-in) are always available and don't need reloading.

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

        # Index custom (TOOL.md) tools
        from src.sdk.tools_custom import find_tool_file, get_custom_tools, load_tool_meta

        custom_count = 0
        current_managed_names: set[str] = set()
        user_id = loop.user_id or DEFAULT_USER_ID
        caps = load_user_capabilities(user_id)
        for td in get_custom_tools(user_id=user_id, workspace_id=loop.workspace_id or "personal"):
            if not is_core_tool(td.name) and resource_enabled(caps, "tools", td.name):
                tool_file = find_tool_file(td.name, user_tools_dir, workspace_tools_dir)
                reconstruct_data = {"command": "", "install": [], "tool_dir": ""}
                if tool_file:
                    meta = load_tool_meta(tool_file)
                    if meta:
                        reconstruct_data = {
                            "command": meta.get("command", ""),
                            "install": meta.get("install", []),
                            "tool_dir": str(tool_file.parent),
                        }
                loop._tool_index.index_tool(td, tool_type="custom", namespace="custom", reconstruct=reconstruct_data)
                current_managed_names.add(td.name)
                custom_count += 1

        # Index MCP tools from the bridge
        mcp_bridge = getattr(loop, "_mcp_bridge", None)
        mcp_count = 0
        if mcp_bridge:
            for td in mcp_bridge.get_tool_definitions():
                if not is_core_tool(td.name) and resource_enabled(caps, "tools", td.name):
                    parts = td.name.split("__", 2)
                    server_name = parts[1] if len(parts) == 3 else ""
                    reconstruct = {"server_name": server_name, "mcp_tool_name": td.name}
                    loop._tool_index.index_tool(td, tool_type="mcp", namespace=f"mcp__{server_name}", reconstruct=reconstruct)
                    current_managed_names.add(td.name)
                    mcp_count += 1

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

        lines = [f"Index rebuilt ({custom_count} custom, {mcp_count} MCP)."]
        if added:
            lines.append(f"  Added: {', '.join(sorted(added))}")
        if removed:
            lines.append(f"  Removed: {', '.join(sorted(removed))}")
        if not added and not removed:
            lines.append("  No changes detected.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error rebuilding tool index: {e}"
