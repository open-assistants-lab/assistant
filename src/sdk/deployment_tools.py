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


def filter_denied_native_tools(
    tools: list[ToolDefinition], settings: Any | None = None
) -> list[ToolDefinition]:
    """Filter shipped native definitions denied by deployment policy."""
    return [tool for tool in tools if native_tool_is_allowed(tool.name, settings)]


def filter_native_tools(
    tools: list[ToolDefinition], settings: Any | None = None
) -> list[ToolDefinition]:
    """Filter only shipped native definitions; custom and MCP callers must not use this."""
    return [tool for tool in tools if native_tool_is_allowed(tool.name, settings)]
