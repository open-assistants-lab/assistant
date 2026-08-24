from __future__ import annotations

from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.loop import get_current_agent_loop
from src.sdk.tools import tool


@tool
def tool_search(description: str, user_id: str = "default_user") -> str:
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
    results = idx.search(description, limit=5)
    if not results:
        return f"No tools found matching '{description}'. Try different keywords."

    # Audit E24-tools: never advertise capability-disabled tools — the stale
    # persisted index may still contain rows for scope=none tools.
    try:
        caps = load_user_capabilities(user_id)
    except Exception:
        caps = {}
    results = [
        (name, desc)
        for name, desc in results
        if resource_enabled(caps, "tools", name)
    ]
    if not results:
        return f"No tools found matching '{description}'. Try different keywords."

    lines = []
    for name, desc in results:
        truncated = desc[:200] + "..." if len(desc) > 200 else desc
        lines.append(f"- **{name}**: {truncated}")
    return "Matching tools:\n" + "\n".join(lines)
