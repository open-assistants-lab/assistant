"""Conversation persistence helpers shared by REST, SSE, and WebSocket routes."""

from typing import Any, cast


def persist_assistant_message(
    conversation: Any,
    content: str,
    *,
    session_id: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    return cast(
        str,
        conversation.add_message(
            "assistant", content, metadata=metadata or {}, session_id=session_id
        ),
    )


def persist_reasoning_message(
    conversation: Any,
    content: str,
    *,
    session_id: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    return cast(
        str,
        conversation.add_message(
            "reasoning", content, metadata=metadata or {}, session_id=session_id
        ),
    )


def persist_tool_message(
    conversation: Any,
    content: str,
    *,
    session_id: str,
    tool_name: str = "unknown",
    tool_call_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> str:
    tool_metadata = {"tool_name": tool_name, "tool_call_id": tool_call_id}
    if metadata:
        tool_metadata.update(metadata)
    return cast(
        str,
        conversation.add_message("tool", content, metadata=tool_metadata, session_id=session_id),
    )
