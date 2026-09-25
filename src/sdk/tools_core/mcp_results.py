"""Bounded result conversion for MCP proxy calls."""

from __future__ import annotations

from typing import Any

from src.sdk.tools import ToolResult


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if hasattr(block, "text"):
            parts.append(str(block.text))
        else:
            parts.append(str(block))
    return "\n".join(parts)


def mcp_result_to_tool_result(
    result: Any,
    *,
    server_name: str,
    tool_name: str,
    max_chars: int,
) -> ToolResult:
    """Convert an MCP result into bounded human and structured output."""
    is_error = bool(getattr(result, "isError", False))
    text = _content_text(result)
    truncated = len(text) > max_chars
    if truncated:
        marker = f"\n\n[truncated {len(text) - max_chars} characters]"
        text = text[:max_chars] + marker

    outcome = "failed" if is_error else "succeeded"
    return ToolResult(
        content=text,
        is_error=is_error,
        structured_content={
            "server": server_name,
            "tool": tool_name,
            "outcome": outcome,
            "receipt_class": "mcp_proxy",
            "truncated": truncated,
            "original_chars": len(_content_text(result)),
        },
    )


def mcp_error_result(
    error: Exception | str,
    *,
    server_name: str,
    tool_name: str,
    outcome: str = "uncertain",
) -> ToolResult:
    """Return a truthful error without claiming a failed mutation succeeded."""
    message = str(error)
    return ToolResult(
        content=message,
        is_error=True,
        structured_content={
            "server": server_name,
            "tool": tool_name,
            "outcome": outcome,
            "receipt_class": "mcp_proxy",
            "truncated": False,
        },
    )
