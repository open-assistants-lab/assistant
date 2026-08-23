"""Google Gemini provider — direct REST API.

Uses Google's generativelanguage.googleapis.com/v1beta API.
Handles:
- generateContent (non-streaming)
- streamGenerateContent (streaming, SSE)
- functionCall / functionResponse (Gemini's tool format)
- GOOGLE_API_KEY auth via key= query parameter
- Thinking config support (for Gemini 2.5+)
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

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    """Google Gemini provider using direct REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        base_url: str = GEMINI_BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key or ""
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider_id(self) -> str:
        return "gemini"

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
            )
        return self._http_client

    def _messages_to_contents(self, messages: list[Message]) -> list[dict[str, Any]]:
        contents = []
        for m in messages:
            role = "user" if m.role in ("user", "system") else "model"
            if m.role == "system":
                system_text = str(m.content)
                contents.append({"role": "user", "parts": [{"text": f"[System]\n{system_text}"}]})
                contents.append({"role": "model", "parts": [{"text": "Understood."}]})
                continue
            if m.role == "tool":
                contents.append(
                    {
                        "role": "function",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.name or "unknown",
                                    "response": {"result": str(m.content)},
                                }
                            }
                        ],
                    }
                )
                continue
            parts: list[dict[str, Any]] = []
            if m.content and isinstance(m.content, str) and m.content.strip():
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                parts.append(
                    {
                        "functionCall": {"name": tc.name, "args": tc.arguments},
                    }
                )
            if parts:
                contents.append({"role": role, "parts": parts})
        return contents

    def _tools_to_gemini(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        result = []
        for t in tools:
            params = t.parameters if t.parameters else {"type": "object", "properties": {}}
            result.append(
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": params,
                        }
                    ]
                }
            )
        return result

    def _url(self, model: str, stream: bool = False) -> str:
        method = "streamGenerateContent" if stream else "generateContent"
        return f"{self.base_url}/models/{model}:{method}?key={self.api_key}"

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        model = model or self.model
        payload = self._build_payload(messages, tools, provider_options=provider_options, **kwargs)
        client = self._get_client()
        # One retry on transient timeout/connect errors.
        attempts = 0
        while True:
            try:
                response = await client.post(self._url(model, stream=False), json=payload)
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

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": self._messages_to_contents(messages),
        }
        if tools:
            payload["tools"] = self._tools_to_gemini(tools)
        provider_opts = self._extract_provider_options(provider_options)
        payload.update(kwargs)
        payload.update(provider_opts)
        return payload

    def _parse_response(self, data: dict[str, Any]) -> Message:
        candidates = data.get("candidates", [])
        if not candidates:
            return Message.assistant(content="")
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        text_parts = []
        tool_calls = []
        reasoning = None
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name=fc.get("name", ""),
                        arguments=fc.get("args", {}),
                    )
                )
            elif "thought" in part:
                reasoning = part["thought"]
        text = "\n".join(text_parts) if text_parts else ""

        usage = None
        usage_meta = data.get("usageMetadata")
        if usage_meta:
            usage = Usage(
                input_tokens=usage_meta.get("promptTokenCount", 0),
                output_tokens=usage_meta.get("candidatesTokenCount", 0),
                reasoning_tokens=usage_meta.get("thoughtsTokenCount", 0),
                cache_read_tokens=usage_meta.get("cachedContentTokenCount", 0),
            )

        result = Message.assistant(content=text, tool_calls=tool_calls, usage=usage)
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
        payload = self._build_payload(messages, tools, provider_options=provider_options, **kwargs)
        client = self._get_client()
        url = self._url(model, stream=True)

        # Retry the stream once on a timeout, but only if NO content has
        # been emitted yet — a mid-stream retry would re-emit the partial
        # text the loop already appended (duplication).
        attempts = 0
        while True:
            current_tool_calls: dict[int, dict[str, Any]] = {}
            emitted = False
            try:
                async with client.stream("POST", url, json=payload) as response:
                    try:
                        response.raise_for_status()
                    except Exception as e:
                        raise_if_context_overflow(e)
                        raise
                    buffer = ""
                    async for chunk_bytes in response.aiter_bytes():
                        buffer += chunk_bytes.decode("utf-8")
                        while "[\n" in buffer or "]\n" in buffer:
                            chunk_str = buffer
                            buffer = ""
                            for line in chunk_str.split(",\n"):
                                line = line.strip().strip("[]")
                                if not line:
                                    continue
                                try:
                                    data = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                for event in self._parse_stream_chunk(data, current_tool_calls):
                                    if event.canonical_type in ("text_delta", "reasoning_delta", "tool_input_delta"):
                                        emitted = True
                                    yield event
                return
            except Exception as e:
                if attempts == 0 and not emitted and is_timeout_error(e):
                    attempts += 1
                    continue
                raise

    def _parse_stream_chunk(
        self, data: dict[str, Any], current_tool_calls: dict[int, dict[str, Any]]
    ) -> list[StreamChunk]:
        """Parse Gemini streaming chunk with proper tool call accumulation.

        Gemini sends functionCall blocks potentially across multiple chunks.
        We accumulate them in current_tool_calls and emit block-structured events.
        """
        events: list[StreamChunk] = []
        candidates = data.get("candidates", [])
        if not candidates:
            return events
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        for part in parts:
            if "text" in part:
                events.append(StreamChunk.text_delta(content=part["text"]))
                events.append(StreamChunk.ai_token(content=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                idx = len(current_tool_calls)
                call_id = f"call_{uuid4().hex[:8]}"
                current_tool_calls[idx] = {
                    "id": call_id,
                    "name": fc.get("name", ""),
                    "args": fc.get("args", {}),
                }
                events.append(
                    StreamChunk.tool_input_start(
                        tool=fc.get("name", ""),
                        call_id=call_id,
                        args=fc.get("args", {}),
                    )
                )
                events.append(
                    StreamChunk.tool_start(
                        tool=fc.get("name", ""),
                        call_id=call_id,
                        args=fc.get("args", {}),
                    )
                )
                arg_json = json.dumps(fc.get("args", {}))
                events.append(
                    StreamChunk.tool_input_delta(
                        call_id=call_id,
                        content=arg_json,
                    )
                )
                events.append(
                    StreamChunk.tool_input_end(
                        call_id=call_id,
                        tool=fc.get("name", ""),
                    )
                )
            elif "thought" in part:
                events.append(StreamChunk.reasoning_delta(content=part["thought"]))
                events.append(StreamChunk.reasoning(content=part["thought"]))

        finish_reason = candidates[0].get("finishReason")
        if finish_reason:
            events.append(StreamChunk.done())

        usage_meta = data.get("usageMetadata")
        # Gemini reports cumulative totals on every chunk — emitting on each
        # one would be summed by the loop into a ~50x overcount (audit S2.3).
        # Report usage only on the terminal chunk (finishReason present).
        if finish_reason and usage_meta:
            events.append(
                StreamChunk.usage_event(
                    Usage(
                        input_tokens=usage_meta.get("promptTokenCount", 0),
                        output_tokens=usage_meta.get("candidatesTokenCount", 0),
                        reasoning_tokens=usage_meta.get("thoughtsTokenCount", 0),
                        cache_read_tokens=usage_meta.get("cachedContentTokenCount", 0),
                    )
                )
            )

        return events

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        defaults = {
            "gemini-2.5-flash": ModelInfo(
                id=model,
                name="Gemini 2.5 Flash",
                provider_id="gemini",
                context_window=1048576,
                output_limit=65536,
                reasoning=True,
                tool_call=True,
            ),
            "gemini-2.5-pro": ModelInfo(
                id=model,
                name="Gemini 2.5 Pro",
                provider_id="gemini",
                context_window=1048576,
                output_limit=65536,
                reasoning=True,
                tool_call=True,
            ),
        }
        return defaults.get(
            model, ModelInfo(id=model, name=model, provider_id="gemini", context_window=1048576)
        )
