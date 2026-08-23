"""Anthropic provider — direct HTTP to https://api.anthropic.com/v1/messages.

Handles:
- Anthropic-specific SSE streaming (message_start, content_block_start, content_block_delta, etc.)
- Tool use blocks (different from OpenAI's tool_calls)
- x-api-key auth, anthropic-version header
- Extended thinking support (for Claude's reasoning mode)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.providers.base import (
    LLMProvider,
    ModelInfo,
    is_timeout_error,
    raise_if_context_overflow,
)
from src.sdk.tools import ToolDefinition

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    """Anthropic provider using direct HTTP API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        base_url: str = ANTHROPIC_BASE_URL,
        timeout: float = 120.0,
        max_tokens: int = 4096,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider_id(self) -> str:
        return "anthropic"

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            headers = {
                "x-api-key": self.api_key or "",
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            }
            self._http_client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._http_client

    def get_client(self) -> httpx.AsyncClient | None:
        return self._get_client()

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        model: str,
        stream: bool = False,
        max_tokens: int | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        system_parts: list[str] = []
        anthropic_msgs = []
        for m in messages:
            if m.role == "system":
                # Audit B2: JOIN all system messages. The duplicate-tool-call
                # guard appends mid-conversation system nudges; last-one-wins
                # here silently replaced the agent's real system prompt.
                system_parts.append(str(m.content))
                continue
            am = m.to_anthropic()
            if m.role == "tool":
                am = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": str(m.content),
                        }
                    ],
                }
            anthropic_msgs.append(am)

        system_content: str | None = "\n\n".join(system_parts) if system_parts else None

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if system_content:
            payload["system"] = system_content
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = [t.to_anthropic_format() for t in tools]
        provider_opts = self._extract_provider_options(provider_options)
        payload.update(kwargs)
        payload.update(provider_opts)
        return payload

    @staticmethod
    def _to_anthropic_tool(td: ToolDefinition) -> dict[str, Any]:
        return {
            "name": td.name,
            "description": td.description,
            "input_schema": td.parameters
            if td.parameters
            else {"type": "object", "properties": {}},
        }

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        model = model or self.model
        payload = self._build_payload(
            messages, tools, model, stream=False, provider_options=provider_options, **kwargs
        )
        client = self._get_client()
        # One retry on transient timeout/connect errors.
        attempts = 0
        while True:
            try:
                response = await client.post(f"{self.base_url}/v1/messages", json=payload)
                break
            except Exception as e:
                if attempts == 0 and is_timeout_error(e):
                    attempts += 1
                    continue
                raise
        try:
            response.raise_for_status()
        except Exception as e:
            raise_if_context_overflow(e)
            raise
        data = response.json()
        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> Message:
        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls = []
        reasoning = None
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", f"call_{uuid4().hex[:8]}"),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )
            elif block.get("type") == "thinking":
                reasoning = block.get("thinking", "")
        content = "\n".join(text_parts) if text_parts else ""
        stop_reason = data.get("stop_reason", "")
        if stop_reason == "tool_use" and not tool_calls:
            pass

        usage = None
        raw_usage = data.get("usage")
        if raw_usage:
            usage = Usage(
                input_tokens=raw_usage.get("input_tokens", 0),
                output_tokens=raw_usage.get("output_tokens", 0),
                cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0),
                cache_creation_tokens=raw_usage.get("cache_creation_input_tokens", 0),
            )

        result = Message.assistant(content=content, tool_calls=tool_calls, usage=usage)
        if reasoning:
            result.reasoning = reasoning
        return result

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        model = model or self.model
        payload = self._build_payload(
            messages, tools, model, stream=True, provider_options=provider_options, **kwargs
        )
        client = self._get_client()
        # Retry the stream once on a timeout, but only if NO content has
        # been emitted yet — a mid-stream retry would re-emit the partial
        # text the loop already appended (duplication).
        attempts = 0
        while True:
            current_tool_calls: dict[int, dict[str, Any]] = {}
            emitted = False
            try:
                async with client.stream("POST", f"{self.base_url}/v1/messages", json=payload) as response:
                    try:
                        response.raise_for_status()
                    except Exception as e:
                        raise_if_context_overflow(e)
                        raise
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if not data_str:
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        for event in self._parse_sse_event(data, current_tool_calls):
                            if event.canonical_type in ("text_delta", "reasoning_delta", "tool_input_delta"):
                                emitted = True
                            yield event
                return
            except Exception as e:
                if attempts == 0 and not emitted and is_timeout_error(e):
                    attempts += 1
                    continue
                raise

    def _parse_sse_event(
        self, data: dict[str, Any], current_tool_calls: dict[int, dict[str, Any]]
    ) -> list[StreamChunk]:
        events: list[StreamChunk] = []
        event_type = data.get("type", "")

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            idx = data.get("index", 0)
            if delta.get("type") == "text_delta":
                events.append(StreamChunk.text_delta(content=delta.get("text", "")))
                events.append(StreamChunk.ai_token(content=delta.get("text", "")))
            elif delta.get("type") == "thinking_delta":
                events.append(StreamChunk.reasoning_delta(content=delta.get("thinking", "")))
                events.append(StreamChunk.reasoning(content=delta.get("thinking", "")))
            elif delta.get("type") == "input_json_delta":
                if idx in current_tool_calls:
                    partial = delta.get("partial_json", "")
                    current_tool_calls[idx]["arguments"] += partial
                    events.append(
                        StreamChunk.tool_input_delta(
                            call_id=current_tool_calls[idx]["id"],
                            content=partial,
                        )
                    )

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            idx = data.get("index", 0)
            if block.get("type") == "tool_use":
                current_tool_calls[idx] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": "",
                    "_type": "tool_use",
                }
                events.append(
                    StreamChunk.tool_input_start(
                        tool=block.get("name", ""),
                        call_id=block.get("id", f"call_{uuid4().hex[:8]}"),
                    )
                )
                events.append(
                    StreamChunk.tool_start(
                        tool=block.get("name", ""),
                        call_id=block.get("id", f"call_{uuid4().hex[:8]}"),
                    )
                )
            elif block.get("type") == "thinking":
                events.append(StreamChunk.reasoning_start())
                current_tool_calls[idx] = {"_type": "thinking"}
            elif block.get("type") == "text":
                events.append(StreamChunk.text_start())
                current_tool_calls[idx] = {"_type": "text"}

        elif event_type == "content_block_stop":
            idx = data.get("index", 0)
            block_info = current_tool_calls.pop(idx, {})
            block_type = block_info.get("_type", "")
            if block_type == "tool_use":
                events.append(
                    StreamChunk.tool_input_end(
                        call_id=block_info.get("id", ""),
                        tool=block_info.get("name", ""),
                    )
                )
            elif block_type == "thinking":
                events.append(StreamChunk.reasoning_end())
            elif block_type == "text":
                events.append(StreamChunk.text_end())

        elif event_type == "message_start":
            msg_data = data.get("message", {})
            raw_usage = msg_data.get("usage")
            if raw_usage:
                events.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=raw_usage.get("input_tokens", 0),
                            output_tokens=raw_usage.get("output_tokens", 0),
                            cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0),
                            cache_creation_tokens=raw_usage.get("cache_creation_input_tokens", 0),
                        )
                    )
                )

        elif event_type == "message_delta":
            delta_usage = data.get("usage", {})
            if delta_usage:
                events.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=0,
                            output_tokens=delta_usage.get("output_tokens", 0),
                        )
                    )
                )

        elif event_type == "message_stop":
            events.append(StreamChunk.done())

        return events

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        defaults = {
            "claude-sonnet-4-20250514": ModelInfo(
                id=model,
                name="Claude Sonnet 4",
                provider_id="anthropic",
                context_window=200000,
                output_limit=64000,
                reasoning=True,
                tool_call=True,
            ),
            "claude-opus-4-20250514": ModelInfo(
                id=model,
                name="Claude Opus 4",
                provider_id="anthropic",
                context_window=200000,
                output_limit=32000,
                reasoning=True,
                tool_call=True,
            ),
        }
        return defaults.get(
            model, ModelInfo(id=model, name=model, provider_id="anthropic", context_window=200000)
        )
