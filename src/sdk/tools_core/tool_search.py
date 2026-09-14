from __future__ import annotations

from fnmatch import fnmatchcase

from src.config import get_settings
from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.loop import get_current_agent_loop
from src.sdk.tools import tool
from src.storage.paths import DEFAULT_USER_ID


@tool
def tool_search(description: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Search for a tool by describing what you need. Returns 3-5 matching tool names with descriptions.

    After finding the right tool, call it directly by name — it will be loaded for subsequent turns.

    Args:
        description: Describe the capability you need in detail. Use specific keywords about what the tool should do.
        user_id: User identifier (automatically provided)

    Returns:
        Name and truncated description of matching tools
    """
    loop = get_current_agent_loop()
    if loop is None or not hasattr(loop, "_tool_index") or loop._tool_index is None:
        return "No tool index available. Tools are not configured for this session."

    idx = loop._tool_index
    results = idx.search_with_tool_types(description, limit=5)
    if not results:
        return f"No tools found matching '{description}'. Try different keywords."

    # Deployment native-tool trimming (#16) is a hard ceiling. The persisted
    # index can be stale before tool_reload, so filter only rows whose stored
    # provenance proves they are built-in native tools. Custom TOOL.md and MCP
    # rows remain visible even when their names match a disabled glob.
    disabled_patterns = getattr(getattr(get_settings(), "tools", None), "disabled", [])
    if isinstance(disabled_patterns, list):
        results = [
            (name, tool_description, tool_type)
            for name, tool_description, tool_type in results
            if tool_type != "native"
            or not any(
                isinstance(pattern, str) and pattern and fnmatchcase(name, pattern)
                for pattern in disabled_patterns
            )
        ]

    # Audit E24-tools: never advertise capability-disabled tools — the stale
    # persisted index may still contain rows for scope=none tools.
    try:
        caps = load_user_capabilities(user_id)
    except Exception:
        caps = {}
    results = [
        (name, desc)
        for name, desc, _ in results
        if resource_enabled(caps, "tools", name)
    ]
    if not results:
        return f"No tools found matching '{description}'. Try different keywords."

    lines = []
    for name, desc in results:
        truncated = desc[:200] + "..." if len(desc) > 200 else desc
        lines.append(f"- **{name}**: {truncated}")
    return "Matching tools:\n" + "\n".join(lines)
