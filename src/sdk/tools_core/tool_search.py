from __future__ import annotations

from src.config import get_settings
from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.deployment_tools import native_tool_is_denied
from src.sdk.loop import get_current_agent_loop
from src.sdk.tools import tool
from src.storage.paths import DEFAULT_USER_ID

_MAX_DISPLAY_RESULTS = 5
# HybridDB exposes ranked result prefixes via ``limit`` but has no result
# offset/cursor. Expand that prefix in this page-sized step until the public
# limit is filled or the finite persisted index is exhausted. This avoids an
# arbitrary candidate cap allowing stale, deployment-disabled native rows to
# hide custom/MCP results.
_SEARCH_PAGE_SIZE = 50


def _eligible_results(
    idx: object,
    description: str,
    settings: object,
    capabilities: dict[object, object],
) -> list[tuple[str, str, str]]:
    """Return allowed ranked results without truncating before index exhaustion."""
    max_candidates = idx.count()  # type: ignore[attr-defined]
    if max_candidates <= 0:
        return []

    limit = min(_SEARCH_PAGE_SIZE, max_candidates)
    while True:
        candidates = idx.search_with_tool_types(description, limit=limit)  # type: ignore[attr-defined]
        allowed = [
            (name, tool_description, tool_type)
            for name, tool_description, tool_type in candidates
            if (
                tool_type != "native"
                or not native_tool_is_denied(name, settings)
            )
            and resource_enabled(capabilities, "tools", name)
        ]
        if (
            len(allowed) >= _MAX_DISPLAY_RESULTS
            or len(candidates) < limit
            or limit >= max_candidates
        ):
            return allowed
        limit = min(limit + _SEARCH_PAGE_SIZE, max_candidates)


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

    # Deployment native-tool trimming (#16) is a hard ceiling. The persisted
    # index can be stale before tool_reload, so filter only rows whose stored
    # provenance proves they are built-in native tools. Custom TOOL.md and MCP
    # rows remain visible even when their names match a disallowed native pattern.
    settings = get_settings()

    # Audit E24-tools: never advertise capability-disabled tools — the stale
    # persisted index may still contain rows for scope=none tools.
    try:
        caps = load_user_capabilities(user_id)
    except Exception:
        caps = {}
    results = _eligible_results(idx, description, settings, caps)
    results = [(name, desc) for name, desc, _ in results]
    if not results:
        return f"No tools found matching '{description}'. Try different keywords."

    lines = []
    for name, desc in results[:_MAX_DISPLAY_RESULTS]:
        truncated = desc[:200] + "..." if len(desc) > 200 else desc
        lines.append(f"- **{name}**: {truncated}")
    return "Matching tools:\n" + "\n".join(lines)
