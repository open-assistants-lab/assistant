"""Message types for the agent SDK.

Unified message types: Message, ToolCall, StreamChunk, Usage.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A single tool call within an assistant message."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.arguments,
        }

    def to_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }

    @classmethod
    def from_openai(cls, data: dict[str, Any]) -> ToolCall:
        func = data.get("function", {})
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            args = json.loads(args)
        return cls(id=data["id"], name=func["name"], arguments=args)

    @classmethod
    def from_anthropic(cls, data: dict[str, Any]) -> ToolCall:
        return cls(id=data["id"], name=data["name"], arguments=data.get("input", {}))


Role = Literal["system", "user", "assistant", "tool"]


class Usage(BaseModel):
    """Token usage from a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class Message(BaseModel):
    """Unified message type for the agent SDK."""

    role: Role
    content: str | list[dict[str, Any]] = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    reasoning: str | None = None
    provider_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    usage: Usage | None = None
    # Internal provenance used by compression and rubric middleware. These fields
    # are not part of provider payloads or the public serialized message contract.
    source: str | None = Field(default=None, exclude=True)
    storage_id: str | None = Field(default=None, exclude=True)
    storage_ts: str | None = Field(default=None, exclude=True)
    storage_session_id: str | None = Field(default=None, exclude=True)

    model_config = {"extra": "allow"}

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
        reasoning: str | None = None,
        provider_metadata: dict[str, dict[str, Any]] | None = None,
        usage: Usage | None = None,
    ) -> Message:
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
            reasoning=reasoning,
            provider_metadata=provider_metadata or {},
            usage=usage,
        )

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, name: str | None = None) -> Message:
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

    def to_openai(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.role == "assistant" and self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        if self.role == "tool" and self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.role == "assistant" and self.reasoning:
            msg["reasoning_content"] = self.reasoning
        return msg

    def to_ollama(self) -> dict[str, Any]:
        """Ollama native /api/chat format.

        Key differences from OpenAI format:
        - tool results use tool_name instead of tool_call_id
        - assistant tool_calls use {type, function: {index, name, arguments}}
        """
        if self.role == "tool":
            msg: dict[str, Any] = {"role": "tool", "content": self.content}
            if self.name:
                msg["tool_name"] = self.name
            return msg
        if self.role == "assistant" and self.tool_calls:
            msg = {"role": "assistant", "content": self.content or ""}
            msg["tool_calls"] = [tc.to_ollama() for tc in self.tool_calls]
            return msg
        return self.to_openai()

    @classmethod
    def from_openai(cls, data: dict[str, Any]) -> Message:
        role = data["role"]
        content = data.get("content", "")
        reasoning = data.get("reasoning_content")
        tool_calls: list[ToolCall] = []
        if role == "assistant" and "tool_calls" in data:
            tool_calls = [ToolCall.from_openai(tc) for tc in data["tool_calls"]]
        tool_call_id = data.get("tool_call_id")
        return cls(role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id, reasoning=reasoning)

    def to_anthropic(self) -> dict[str, Any]:
        if self.role == "system":
            return {"type": "text", "text": self.content}
        if self.role == "user":
            return {"role": "user", "content": self.content}
        if self.role == "assistant":
            content: list[dict[str, Any]] = []
            if self.reasoning:
                content.append({"type": "thinking", "thinking": self.reasoning})
            if self.content:
                content.append({"type": "text", "text": self.content})
            for tc in self.tool_calls:
                content.append(tc.to_anthropic())
            return {"role": "assistant", "content": content}
        if self.role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id or "",
                        "content": str(self.content),
                    }
                ],
            }
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_anthropic_block(cls, block: dict[str, Any]) -> Message | None:
        if block.get("type") == "text":
            return cls.assistant(content=block["text"])
        if block.get("type") == "tool_use":
            tc = ToolCall.from_anthropic(block)
            return cls.assistant(tool_calls=[tc])
        if block.get("type") == "thinking":
            return cls.assistant(content="", reasoning=block.get("thinking", ""))
        return None


StreamEventType = Literal[
    "text_start",
    "text_delta",
    "text_end",
    "tool_input_start",
    "tool_input_delta",
    "tool_input_end",
    "reasoning_start",
    "reasoning_delta",
    "reasoning_end",
    "interrupt",
    "done",
    "error",
    "ai_token",
    "tool_start",
    "tool_end",
    "reasoning",
    "tool_result",
    "usage",
    "rubric_evaluation_start",
    "rubric_evaluation_end",
]

_COMPAT_ALIAS_MAP: dict[str, str] = {
    "ai_token": "text_delta",
    "tool_start": "tool_input_start",
    "reasoning": "reasoning_delta",
    "usage": "usage",
}


class StreamChunk(BaseModel):
    """A single streaming event from the agent loop.

    Block-structured events (primary):
        text_start / text_delta / text_end
        tool_input_start / tool_input_delta / tool_input_end
        reasoning_start / reasoning_delta / reasoning_end
        interrupt / done / error / tool_result

    Backward-compatible aliases (also emitted alongside primary):
        ai_token → text_delta
        tool_start → tool_input_start
        reasoning → reasoning_delta
        tool_end → kept as-is (AgentLoop emits after execution)
    """

    type: StreamEventType
    content: str = ""
    tool: str | None = None
    call_id: str | None = None
    args: dict[str, Any] | None = None
    result_preview: str | None = None
    is_error: bool = False
    tool_calls: list[dict[str, Any]] | None = None
    usage: Usage | None = None

    @classmethod
    def text_start(cls) -> StreamChunk:
        return cls(type="text_start")

    @classmethod
    def text_delta(cls, content: str) -> StreamChunk:
        return cls(type="text_delta", content=content)

    @classmethod
    def text_end(cls) -> StreamChunk:
        return cls(type="text_end")

    @classmethod
    def tool_input_start(cls, tool: str, call_id: str, args: dict[str, Any] | None = None) -> StreamChunk:
        return cls(type="tool_input_start", tool=tool, call_id=call_id, args=args or {})

    @classmethod
    def tool_input_delta(cls, call_id: str, content: str = "") -> StreamChunk:
        return cls(type="tool_input_delta", call_id=call_id, content=content)

    @classmethod
    def tool_input_end(cls, call_id: str, tool: str = "") -> StreamChunk:
        return cls(type="tool_input_end", call_id=call_id, tool=tool)

    @classmethod
    def reasoning_start(cls) -> StreamChunk:
        return cls(type="reasoning_start")

    @classmethod
    def reasoning_delta(cls, content: str) -> StreamChunk:
        return cls(type="reasoning_delta", content=content)

    @classmethod
    def reasoning_end(cls) -> StreamChunk:
        return cls(type="reasoning_end")

    @classmethod
    def tool_result_event(cls, tool: str, call_id: str, result_preview: str = "", is_error: bool = False) -> StreamChunk:
        return cls(type="tool_result", tool=tool, call_id=call_id, result_preview=result_preview, is_error=is_error)

    @classmethod
    def ai_token(cls, content: str) -> StreamChunk:
        return cls(type="ai_token", content=content)

    @classmethod
    def tool_start(cls, tool: str, call_id: str, args: dict[str, Any] | None = None) -> StreamChunk:
        return cls(type="tool_start", tool=tool, call_id=call_id, args=args or {})

    @classmethod
    def tool_end(cls, tool: str, call_id: str, result_preview: str = "") -> StreamChunk:
        return cls(type="tool_end", tool=tool, call_id=call_id, result_preview=result_preview)

    @classmethod
    def interrupt(cls, tool: str, call_id: str, args: dict[str, Any] | None = None) -> StreamChunk:
        return cls(type="interrupt", tool=tool, call_id=call_id, args=args or {})

    @classmethod
    def reasoning(cls, content: str) -> StreamChunk:
        return cls(type="reasoning", content=content)

    @classmethod
    def done(cls, content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> StreamChunk:
        return cls(type="done", content=content, tool_calls=tool_calls)

    @classmethod
    def error(cls, message: str) -> StreamChunk:
        return cls(type="error", content=message)

    @classmethod
    def usage_event(cls, usage: Usage) -> StreamChunk:
        return cls(type="usage", usage=usage)

    @property
    def canonical_type(self) -> str:
        return _COMPAT_ALIAS_MAP.get(self.type, self.type)

    def to_ws_message(self) -> dict[str, Any]:
        from src.http.ws_protocol import (
            AiTokenMessage,
            DoneMessage,
            ErrorMessage,
            InterruptMessage,
            ReasoningDeltaMessage,
            ReasoningEndMessage,
            ReasoningStartMessage,
            TextDeltaMessage,
            TextEndMessage,
            TextStartMessage,
            ToolEndMessage,
            ToolInputDeltaMessage,
            ToolInputEndMessage,
            ToolInputStartMessage,
            ToolResultMessage,
            ToolStartMessage,
        )

        canonical = self.canonical_type

        if canonical == "text_delta":
            if self.type == "ai_token":
                return AiTokenMessage(content=self.content).model_dump()
            return TextDeltaMessage(content=self.content).model_dump()
        if canonical == "text_start":
            return TextStartMessage().model_dump()
        if canonical == "text_end":
            return TextEndMessage().model_dump()
        if canonical == "tool_input_start":
            if self.type == "tool_start":
                return ToolStartMessage(
                    tool=self.tool or "", call_id=self.call_id or "", args=self.args or {}
                ).model_dump()
            return ToolInputStartMessage(
                tool=self.tool or "", call_id=self.call_id or "", args=self.args or {}
            ).model_dump()
        if canonical == "tool_input_delta":
            return ToolInputDeltaMessage(
                call_id=self.call_id or "", content=self.content
            ).model_dump()
        if canonical == "tool_input_end":
            return ToolInputEndMessage(
                call_id=self.call_id or "", tool=self.tool or None
            ).model_dump()
        if canonical == "reasoning_delta":
            if self.type == "reasoning":
                from src.http.ws_protocol import ReasoningMessage

                return ReasoningMessage(content=self.content).model_dump()
            return ReasoningDeltaMessage(content=self.content).model_dump()
        if canonical == "reasoning_start":
            return ReasoningStartMessage().model_dump()
        if canonical == "reasoning_end":
            return ReasoningEndMessage().model_dump()
        if self.type == "interrupt":
            return InterruptMessage(
                tool=self.tool or "", call_id=self.call_id or "", args=self.args or {}
            ).model_dump()
        if self.type == "tool_end":
            return ToolEndMessage(
                tool=self.tool or "",
                call_id=self.call_id or "",
                result_preview=self.result_preview or "",
            ).model_dump()
        if self.type == "tool_result":
            return ToolResultMessage(
                tool=self.tool or "",
                call_id=self.call_id or "",
                result_preview=self.result_preview or "",
            ).model_dump()
        if self.type == "done":
            return DoneMessage(response=self.content, tool_calls=self.tool_calls or []).model_dump()
        if self.type == "error":
            return ErrorMessage(message=self.content).model_dump()
        if self.type == "usage":
            if self.usage:
                return {
                    "type": "usage",
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                    "reasoning_tokens": self.usage.reasoning_tokens,
                    "cache_read_tokens": self.usage.cache_read_tokens,
                    "cache_creation_tokens": self.usage.cache_creation_tokens,
                }
            return {"type": "usage"}
        return {"type": self.type, "content": self.content}
