"""Deployment-wide native tool visibility policy (#16)."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from src.config import get_settings
from src.sdk.tools import ToolDefinition


def disabled_native_tool_patterns(settings: Any | None = None) -> tuple[str, ...]:
    """Return configured exact/glob patterns, ignoring malformed values."""
    if settings is None:
        settings = get_settings()
    patterns = getattr(getattr(settings, "tools", None), "disabled", [])
    if not isinstance(patterns, list):
        return ()
    return tuple(pattern for pattern in patterns if isinstance(pattern, str) and pattern)


def native_tool_is_deployment_disabled(name: str, settings: Any | None = None) -> bool:
    """Whether a built-in native tool name is disabled for this deployment."""
    return any(fnmatchcase(name, pattern) for pattern in disabled_native_tool_patterns(settings))


def filter_deployment_disabled_native_tools(
    tools: list[ToolDefinition], settings: Any | None = None
) -> list[ToolDefinition]:
    """Filter native definitions; callers must never pass custom or MCP tools."""
    return [tool for tool in tools if not native_tool_is_deployment_disabled(tool.name, settings)]
