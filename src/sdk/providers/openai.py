"""OpenAI + OpenAI-compatible provider.

Covers OpenAI, Ollama Cloud, Together, Groq, DeepSeek, OpenRouter,
Fireworks, LM Studio, llama.cpp, and 80+ OpenAI-compatible APIs.

Uses the `openai` Python SDK for protocol handling (SSE parsing, retries,
error handling). We wrap it with our SDK Message types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI

from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.providers.base import LLMProvider, ModelInfo, raise_if_context_overflow
from src.sdk.tools import ToolDefinition

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider using the openai SDK.

    Works with any OpenAI-compatible API by changing base_url.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = OPENAI_DEFAULT_BASE_URL,
        model: str = "gpt-4o",
        organization: str | None = None,
        timeout: float = 120.0,
        provider_id: str = "openai",
        default_provider_options: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self._provider_id = provider_id
        self._default_provider_options = default_provider_options or {}
        self._client = AsyncOpenAI(
            api_key=api_key or "unused",
            base_url=base_url,
            organization=organization,
            timeout=timeout,
        )

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _extract_provider_options(
        self, provider_options: dict[str, dict[str, Any]] | None
    ) -> dict[str, Any]:
        options = dict(self._default_provider_options)
        if provider_options:
            options.update(provider_options.get(self.provider_id, {}))
        return options

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        model = model or self.model
        openai_msgs = [m.to_openai() for m in messages]
        tool_schemas = [t.to_openai_format() for t in tools] if tools else None

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_msgs,
        }
        if tool_schemas:
            params["tools"] = tool_schemas
        provider_opts = self._extract_provider_options(provider_options)
        params.update(kwargs)
        params.update(provider_opts)
        self._move_extension_fields_to_extra_body(params)

        try:
            response = await self._client.chat.completions.create(**params)
        except Exception as e:
            raise_if_context_overflow(e)
            raise
        return self._parse_response(response)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        model = model or self.model
        openai_msgs = [m.to_openai() for m in messages]
        tool_schemas = [t.to_openai_format() for t in tools] if tools else None

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_msgs,
            "stream": True,
        }
        if tool_schemas:
            params["tools"] = tool_schemas
        provider_opts = self._extract_provider_options(provider_options)
        params.update(kwargs)
        params.update(provider_opts)
        self._move_extension_fields_to_extra_body(params)

        current_tool_calls: dict[int, dict[str, Any]] = {}

        try:
            stream = await self._client.chat.completions.create(**params)
        except Exception as e:
            raise_if_context_overflow(e)
            raise
        async for chunk in stream:
            for event in self._parse_stream_chunk(chunk, current_tool_calls):
                yield event

    def _parse_response(self, response: Any) -> Message:
        choice = response.choices[0] if response.choices else None
        if not choice:
            return Message.assistant(content="")

        msg = choice.message
        content = msg.content or ""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                import json

                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        pass
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        reasoning = None
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            if isinstance(msg.reasoning_content, str):
                reasoning = msg.reasoning_content

        usage = None
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            usage = Usage(
                input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                output_tokens=getattr(u, "completion_tokens", 0) or 0,
                reasoning_tokens=getattr(u, "completion_tokens_details", None)
                and getattr(u.completion_tokens_details, "reasoning_tokens", 0)
                or 0,
                cache_read_tokens=getattr(u, "prompt_tokens_details", None)
                and getattr(u.prompt_tokens_details, "cached_tokens", 0)
                or 0,
            )

        return Message.assistant(content=content, tool_calls=tool_calls, usage=usage, reasoning=reasoning)

    def _move_extension_fields_to_extra_body(self, params: dict[str, Any]) -> None:
        """Move OpenAI-compatible extension fields into the SDK escape hatch."""
        extra_body = dict(params.get("extra_body") or {})
        for key in ("chat_template_kwargs", "thinking"):
            if key in params:
                extra_body[key] = params.pop(key)
        if extra_body:
            params["extra_body"] = extra_body

    def _parse_stream_chunk(
        self, chunk: Any, current_tool_calls: dict[int, dict[str, Any]]
    ) -> list[StreamChunk]:
        events: list[StreamChunk] = []

        if not chunk.choices:
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                events.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                            output_tokens=getattr(u, "completion_tokens", 0) or 0,
                            reasoning_tokens=getattr(u, "completion_tokens_details", None)
                            and getattr(u.completion_tokens_details, "reasoning_tokens", 0)
                            or 0,
                            cache_read_tokens=getattr(u, "prompt_tokens_details", None)
                            and getattr(u.prompt_tokens_details, "cached_tokens", 0)
                            or 0,
                        )
                    )
                )
            return events

        choice = chunk.choices[0]
        delta = choice.delta

        if delta.content:
            events.append(StreamChunk.text_delta(content=delta.content))
            events.append(StreamChunk.ai_token(content=delta.content))

        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            if isinstance(delta.reasoning_content, str):
                # Emit the canonical event plus the backward-compat alias,
                # matching the other providers (Anthropic/Gemini/Ollama).
                events.append(StreamChunk.reasoning_delta(content=delta.reasoning_content))
                events.append(StreamChunk.reasoning(content=delta.reasoning_content))

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in current_tool_calls:
                    current_tool_calls[idx] = {
                        "id": tc_delta.id or "",
                        "name": "",
                        "arguments": "",
                    }
                    events.append(
                        StreamChunk.tool_input_start(
                            tool="",
                            call_id=tc_delta.id or f"call_{uuid4().hex[:8]}",
                        )
                    )
                    events.append(
                        StreamChunk.tool_start(
                            tool="",
                            call_id=tc_delta.id or f"call_{uuid4().hex[:8]}",
                        )
                    )
                entry = current_tool_calls[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments
                        events.append(
                            StreamChunk.tool_input_delta(
                                call_id=entry["id"] or f"call_{uuid4().hex[:8]}",
                                content=tc_delta.function.arguments,
                            )
                        )

        if choice.finish_reason:
            for idx, tc in current_tool_calls.items():
                events.append(
                    StreamChunk.tool_input_end(
                        call_id=tc["id"] or f"call_{uuid4().hex[:8]}",
                        tool=tc["name"],
                    )
                )
            current_tool_calls.clear()
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                events.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                            output_tokens=getattr(u, "completion_tokens", 0) or 0,
                            reasoning_tokens=getattr(u, "completion_tokens_details", None)
                            and getattr(u.completion_tokens_details, "reasoning_tokens", 0)
                            or 0,
                            cache_read_tokens=getattr(u, "prompt_tokens_details", None)
                            and getattr(u.prompt_tokens_details, "cached_tokens", 0)
                            or 0,
                        )
                    )
                )
            events.append(StreamChunk.done())

        return events

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        return ModelInfo(id=model, name=model, provider_id=self.provider_id, context_window=128000)
