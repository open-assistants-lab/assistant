"""Shared HTTP stream event adapter for SDK StreamChunk values."""

from dataclasses import dataclass
from typing import Any

from src.sdk.messages import StreamChunk


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    content: str = ""
    tool: str | None = None
    call_id: str | None = None
    args: dict[str, Any] | None = None
    result_preview: str | None = None
    is_error: bool = False


def adapt_stream_chunk(chunk: StreamChunk) -> StreamEvent:
    """Normalize SDK stream chunks for HTTP routers using canonical event names."""
    return StreamEvent(
        kind=chunk.canonical_type,
        content=chunk.content or "",
        tool=chunk.tool,
        call_id=chunk.call_id,
        args=chunk.args,
        result_preview=chunk.result_preview,
        is_error=chunk.is_error,
    )
