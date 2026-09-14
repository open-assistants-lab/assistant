"""Deployment policy for shipped native tools (#16)."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from src.config import get_settings
from src.sdk.tools import ToolDefinition


def native_tool_is_allowed(name: str, settings: Any | None = None) -> bool:
    """Return whether a shipped native tool may enter this deployment's registry."""
    if settings is None:
        settings = get_settings()
    native = getattr(getattr(settings, "tools", None), "native", None)
    mode = getattr(native, "mode", "all")
    if mode == "none":
        return False
    if mode != "selected":
        return True
    patterns = getattr(native, "enabled", [])
    return isinstance(patterns, list) and any(
        isinstance(pattern, str) and fnmatchcase(name, pattern)
        for pattern in patterns
    )


def native_tool_is_denied(name: str, settings: Any | None = None) -> bool:
    """Whether policy denies a shipped native tool (internal convenience)."""
    return not native_tool_is_allowed(name, settings)


def is_shipped_native_definition(tool_def: ToolDefinition) -> bool:
    """Whether this exact definition is shipped native code, including runner meta-tools.

    Identity rather than name is deliberate: a custom or MCP definition may
    share a native name and must remain outside the deployment-native policy.
    """
    from src.sdk.native_tools import get_native_tools
    from src.sdk.tools_core.tool_reload import tool_reload
    from src.sdk.tools_core.tool_search import tool_search

    return tool_def is tool_search or tool_def is tool_reload or any(
        tool_def is native for native in get_native_tools()
    )


def filter_denied_native_tools(
    tools: list[ToolDefinition], settings: Any | None = None
) -> list[ToolDefinition]:
    """Filter only denied shipped definitions; preserve custom/MCP collisions."""
    return [
        tool
        for tool in tools
        if not is_shipped_native_definition(tool)
        or native_tool_is_allowed(tool.name, settings)
    ]


def filter_native_tools(
    tools: list[ToolDefinition], settings: Any | None = None
) -> list[ToolDefinition]:
    """Backward-compatible alias for provenance-aware native filtering."""
    return filter_denied_native_tools(tools, settings)
