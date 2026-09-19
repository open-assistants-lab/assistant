"""Normalise a tool return value to display text.

Tools may now return a `ToolResult` (to carry an error flag) or the plain
string they always did. Router handlers only want the text.
"""

from __future__ import annotations

from typing import Any

from src.sdk.tools import ToolResult


def tool_text(value: Any) -> str:
    """Return the text of a tool result, whichever shape it has."""
    return ToolResult.from_raw(value).content
