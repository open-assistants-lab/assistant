"""Ollama Cloud provider — native /api/chat endpoint with Bearer auth.

For local Ollama (OpenAI-compatible /v1/chat/completions), use OpenAIProvider
with base_url="http://localhost:11434/v1" — same protocol, no separate class needed.

OllamaCloud: POST https://ollama.com/api/chat (native Ollama protocol)
"""


from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.providers.base import LLMProvider, ModelInfo, raise_if_context_overflow
from src.sdk.tools import ToolDefinition


class OllamaCloud(LLMProvider):
    """Provider for Ollama Cloud (ollama.com/api/chat).

    Uses the native Ollama /api/chat API with Bearer authentication.
    Requires OLLAMA_API_KEY.

    Message format uses Ollama native format (to_ollama):
        - tool results: {role: "tool", tool_name: "...", content: "..."}
        - assistant tool_calls: {type: "function", function: {name, arguments}}
    """

    def __init__(
        self,
        base_url: str = "https://ollama.com",
        model: str = "minimax-m2.5",
        api_key: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider_id(self) -> str:
        return "ollama-cloud"

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
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
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_ollama() for m in messages],
            "stream": stream,
        }
        if tools:
            payload["tools"] = [t.to_openai_format() for t in tools]
        provider_opts = dict(self._extract_provider_options(provider_options))
        # Map the OpenAI-style max_tokens to Ollama's native
        # options.num_predict so callers can bound output uniformly.
        # Copy first: _extract_provider_options returns the caller's dict
        # and popping would mutate shared provider_options (e.g. the
        # grader loop's run_config).
        if "max_tokens" in provider_opts:
            options = dict(provider_opts.get("options") or {})
            options["num_predict"] = provider_opts.pop("max_tokens")
            provider_opts["options"] = options
        payload.update(kwargs)
        # max_tokens passed as a direct kwarg (e.g. the title call) is
        # otherwise silently ignored by Ollama's native API — map it too.
        if "max_tokens" in payload and "num_predict" not in (payload.get("options") or {}):
            options = dict(payload.get("options") or {})
            options["num_predict"] = payload.pop("max_tokens")
            payload["options"] = options
        payload.update(provider_opts)
        return payload

    def _parse_response(self, data: dict[str, Any]) -> Message:
        msg = data.get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("thinking", None)
        tool_calls = msg.get("tool_calls", [])
        parsed_tcs = []
        for tc in tool_calls:
            func = tc.get("function", {})
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    from src.sdk.validation import repair_tool_call
                    args = repair_tool_call(args)
            parsed_tcs.append(
                ToolCall(
                    id=tc.get("id", f"call_{uuid4().hex[:8]}"),
                    name=func.get("name", ""),
                    arguments=args,
                )
            )

        usage = None
        raw_usage = data.get("usage") or data.get("eval_count")
        if isinstance(raw_usage, dict):
            usage = Usage(
                input_tokens=raw_usage.get("prompt_tokens", 0),
                output_tokens=raw_usage.get("completion_tokens", 0),
                # Ollama's native API doesn't report reasoning tokens
                # separately; estimate from the thinking content.
                reasoning_tokens=len(reasoning) // 4 if reasoning else 0,
            )
        elif isinstance(raw_usage, int) and "prompt_eval_count" in data:
            usage = Usage(
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=raw_usage,
                reasoning_tokens=len(reasoning) // 4 if reasoning else 0,
            )

        result = Message.assistant(content=content, tool_calls=parsed_tcs, usage=usage)
        if reasoning:
            result.reasoning = reasoning
        return result

    def _parse_chunk(
        self,
        data: dict[str, Any],
        current_tool_calls: dict[int, dict[str, Any]],
        provider_options: dict[str, dict[str, Any]] | None = None,
        emitted_starts: set[tuple[str, str]] | None = None,
    ) -> list[StreamChunk]:
        """Parse a streaming chunk from Ollama Cloud native /api/chat.

        Emits block-structured events (tool_input_start/delta/end, text_delta,
        reasoning_delta) plus backward-compatible aliases (tool_start, ai_token, reasoning).
        The AgentLoop emits tool_result/tool_end after executing tools.
        """
        chunks: list[StreamChunk] = []
        msg = data.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")

        tool_calls = msg.get("tool_calls", [])
        for i, tc in enumerate(tool_calls):
            func = tc.get("function", {})
            tc_id = tc.get("id", f"call_{uuid4().hex[:8]}")
            name = func.get("name", "")
            args_str = func.get("arguments", {})

            if i not in current_tool_calls:
                current_tool_calls[i] = {
                    "id": tc_id,
                    "name": name,
                    "arguments": "",
                }
                # Dedup: skip tool_input_start for duplicate (name, args) pairs
                # that the MiniMax reasoning model emits multiple times.
                start_key = (name or "", str(args_str)[:200])
                if name and (emitted_starts is None or start_key not in emitted_starts):
                    if emitted_starts is not None:
                        emitted_starts.add(start_key)
                    chunks.append(
                        StreamChunk.tool_input_start(
                            tool=name,
                            call_id=tc_id,
                        )
                    )
                    chunks.append(
                        StreamChunk.tool_start(
                            tool=name,
                            call_id=tc_id,
                        )
                    )

            entry = current_tool_calls[i]
            if name:
                entry["name"] = name
            if tc_id:
                entry["id"] = tc_id
            if isinstance(args_str, str) and args_str:
                entry["arguments"] += args_str
                chunks.append(
                    StreamChunk.tool_input_delta(
                        call_id=tc_id,
                        content=args_str,
                    )
                )
            elif isinstance(args_str, dict) and args_str:
                arg_json = json.dumps(args_str)
                entry["arguments"] += arg_json
                chunks.append(
                    StreamChunk.tool_input_delta(
                        call_id=tc_id,
                        content=arg_json,
                    )
                )

        if data.get("done", False):
            if thinking:
                chunks.append(StreamChunk.reasoning_delta(content=thinking))
                chunks.append(StreamChunk.reasoning(content=thinking))
            if content:
                chunks.append(StreamChunk.text_delta(content=content))
                chunks.append(StreamChunk.ai_token(content=content))
            for entry in current_tool_calls.values():
                chunks.append(
                    StreamChunk.tool_input_end(
                        call_id=entry["id"],
                        tool=entry["name"],
                    )
                )
            current_tool_calls.clear()

            raw_usage = data.get("usage") or {}
            if raw_usage:
                chunks.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=raw_usage.get(
                                "prompt_tokens", data.get("prompt_eval_count", 0)
                            ),
                            output_tokens=raw_usage.get(
                                "completion_tokens", data.get("eval_count", 0)
                            ),
                        )
                    )
                )
            elif "prompt_eval_count" in data or "eval_count" in data:
                chunks.append(
                    StreamChunk.usage_event(
                        Usage(
                            input_tokens=data.get("prompt_eval_count", 0),
                            output_tokens=data.get("eval_count", 0),
                        )
                    )
                )

            chunks.append(StreamChunk.done(content=content))
        else:
            if thinking:
                chunks.append(StreamChunk.reasoning_delta(content=thinking))
                chunks.append(StreamChunk.reasoning(content=thinking))
            if content:
                chunks.append(StreamChunk.text_delta(content=content))
                chunks.append(StreamChunk.ai_token(content=content))

        return chunks

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
        response = await client.post(f"{self.base_url}/api/chat", json=payload)
        try:
            response.raise_for_status()
        except Exception as e:
            raise_if_context_overflow(e)
            raise
        return self._parse_response(response.json())

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
        current_tool_calls: dict[int, dict[str, Any]] = {}
        emitted_starts: set[tuple[str, str]] = set()
        total_thinking = ""
        async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
            try:
                response.raise_for_status()
            except Exception as e:
                raise_if_context_overflow(e)
                raise
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for chunk in self._parse_chunk(data, current_tool_calls, provider_options, emitted_starts):
                    # Count only the canonical reasoning_delta event — the
                    # provider also emits the backward-compat `reasoning`
                    # alias for the same content.
                    if chunk.type == "reasoning_delta" and chunk.content:
                        total_thinking += chunk.content
                    elif chunk.type == "usage" and chunk.usage and total_thinking:
                        # Ollama's native API doesn't report reasoning tokens
                        # separately; estimate from the streamed thinking.
                        chunk.usage.reasoning_tokens = len(total_thinking) // 4
                    yield chunk

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        return ModelInfo(id=model, name=model, provider_id="ollama-cloud", context_window=128000)
