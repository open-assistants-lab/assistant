"""Abstract base class for LLM providers.

Every provider implements: chat(), chat_stream(), count_tokens(), provider_id.
Messages use the SDK's Message/ToolCall/StreamChunk types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from src.sdk.messages import Message, StreamChunk
from src.sdk.tools import ToolDefinition


@dataclass
class ModelCost:
    input: float = 0.0
    output: float = 0.0
    reasoning: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    input_audio: float | None = None
    output_audio: float | None = None


@dataclass
class ModelInfo:
    id: str = ""
    name: str = ""
    provider_id: str = ""
    family: str | None = None
    tool_call: bool = True
    reasoning: bool = False
    structured_output: bool = False
    temperature: bool = True
    attachment: bool = False
    interleaved: bool | str = False
    context_window: int = 128000
    input_limit: int | None = None
    output_limit: int = 4096
    cost: ModelCost | None = None
    modalities_input: list[str] = field(default_factory=lambda: ["text"])
    modalities_output: list[str] = field(default_factory=lambda: ["text"])
    open_weights: bool = False
    knowledge: str | None = None
    release_date: str | None = None
    last_updated: str | None = None
    status: str | None = None


class ProviderContextOverflowError(Exception):
    """Raised when the LLM API returns a 413 or context-too-large error."""
    pass


_CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "maximum context",
    "context_length_exceeded",
    "context window",
    "prompt is too long",
    "too many tokens",
    "tokens exceed",
    "request too large",
    "payload too large",
)


def raise_if_context_overflow(exc: BaseException) -> None:
    """Normalize provider-specific context-too-large errors."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status_code == 413:
        raise ProviderContextOverflowError(str(exc)) from exc

    text_parts = [str(exc)]
    if response is not None:
        try:
            text_parts.append(str(getattr(response, "text", "")))
        except Exception:
            pass
    text = " ".join(text_parts).lower()
    if any(marker in text for marker in _CONTEXT_OVERFLOW_MARKERS):
        raise ProviderContextOverflowError(str(exc)) from exc


class LLMProvider(ABC):
    """Abstract base class for all LLM providers.

    Usage:
        provider = OpenAIProvider(api_key="sk-...")
        response = await provider.chat(messages, tools=tool_defs, model="gpt-4o")
        async for chunk in provider.chat_stream(messages, tools=tool_defs):
            print(chunk.content, end="")
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        """Send messages and get a complete response.

        Args:
            messages: Conversation history (system, user, assistant, tool).
            tools: Available tools for the model to call.
            model: Override the default model.
            provider_options: Provider-specific options keyed by provider_id.
                e.g. {"anthropic": {"thinking": {"type": "enabled", "budget_tokens": 5000}}}
            **kwargs: Provider-specific options (temperature, max_tokens, etc.)

        Returns:
            Assistant message, possibly with tool_calls.
        """

    @abstractmethod
    def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Send messages and stream the response.

        Yields StreamChunk block events:
            text_start, text_delta, text_end,
            tool_input_start, tool_input_delta, tool_input_end,
            reasoning_start, reasoning_delta, reasoning_end,
            done, error.
        Also yields backward-compat: ai_token, tool_start, reasoning.

        Note: This is a regular method returning an async generator, not an async method.
        Implementations should use `async def ...` with `yield` or return an async generator.
        """

    @abstractmethod
    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count for text. Approximate for most providers."""

    @abstractmethod
    def get_model_info(self, model: str) -> ModelInfo:
        """Return metadata for a model. Falls back to defaults if unknown."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider (e.g., 'ollama', 'openai', 'anthropic')."""

    def get_client(self) -> Any | None:
        """Return the underlying HTTP client, if available, for connection testing."""
        return None

    def _extract_provider_options(
        self, provider_options: dict[str, dict[str, Any]] | None
    ) -> dict[str, Any]:
        """Extract options for this provider from the provider_options dict."""
        if not provider_options:
            return {}
        return provider_options.get(self.provider_id, {})
