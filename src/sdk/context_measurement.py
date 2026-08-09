"""Provider-neutral measurement of prepared agent context."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from src.sdk.messages import Message
from src.sdk.providers.base import ModelInfo
from src.sdk.registry import list_models
from src.sdk.run_models import (
    CanonicalModel,
    ContextFreshness,
    ContextSnapshot,
    ContextSource,
)
from src.sdk.tools import ToolDefinition

TokenEstimator = Callable[[str], int]
ModelLister = Callable[[str], list[ModelInfo]]
ContextWindowResolver = Callable[[CanonicalModel], int | None]

_MESSAGE_OVERHEAD_TOKENS = 4


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def estimate_text_tokens(text: str) -> int:
    """Return a deterministic character-based token approximation."""
    return (len(text) + 3) // 4


def estimate_message_tokens(
    messages: Sequence[Message], estimator: TokenEstimator = estimate_text_tokens
) -> int:
    """Estimate tokens for message fields with provider wire semantics."""
    total = 0
    for message in messages:
        total += _MESSAGE_OVERHEAD_TOKENS
        total += estimator(message.role)
        content = message.content if isinstance(message.content, str) else _stable_json(message.content)
        total += estimator(content)

        reasoning = getattr(message, "reasoning", None)
        if reasoning:
            total += estimator(reasoning)

        if message.role == "assistant":
            for tool_call in getattr(message, "tool_calls", None) or ():
                total += estimator(tool_call.id)
                total += estimator(tool_call.name)
                total += estimator(_stable_json(tool_call.arguments))

        if message.role == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                total += estimator(tool_call_id)
            name = getattr(message, "name", None)
            if name:
                total += estimator(name)

    return total


def estimate_tool_schema_tokens(
    tools: Sequence[ToolDefinition] | None,
    estimator: TokenEstimator = estimate_text_tokens,
) -> int:
    """Estimate the selected tool schemas in stable OpenAI wire format."""
    if not tools:
        return 0
    schemas = sorted(_stable_json(tool.to_openai_format()) for tool in tools)
    return sum(estimator(schema) for schema in schemas)


def estimate_prepared_tokens(
    messages: Sequence[Message],
    tools: Sequence[ToolDefinition] | None = None,
    estimator: TokenEstimator = estimate_text_tokens,
) -> int:
    """Estimate all messages and selected tool schemas prepared for a call."""
    return estimate_message_tokens(messages, estimator) + estimate_tool_schema_tokens(tools, estimator)


def resolve_context_window(
    model: CanonicalModel,
    list_models_fn: ModelLister = list_models,
) -> int | None:
    """Resolve an exact canonical model match without relying on synthetic defaults."""
    provider, separator, model_id = model.partition(":")
    if not separator:
        return None
    try:
        models = list_models_fn(provider)
    except Exception:
        return None

    for model_info in models:
        if model_info.provider_id == provider and model_info.id == model_id:
            return model_info.context_window if model_info.context_window > 0 else None
    return None


def build_context_snapshot(
    *,
    model: CanonicalModel,
    messages: Sequence[Message],
    tools: Sequence[ToolDefinition] | None,
    attempt: int,
    llm_call_index: int,
    source: ContextSource,
    freshness: ContextFreshness,
    estimator: TokenEstimator = estimate_text_tokens,
    context_window_resolver: ContextWindowResolver = resolve_context_window,
) -> ContextSnapshot:
    """Build a context snapshot for the exact context prepared for an LLM call."""
    estimated_tokens = estimate_prepared_tokens(messages, tools, estimator)
    context_window = context_window_resolver(model)
    percentage = estimated_tokens / context_window * 100 if context_window else None
    return ContextSnapshot(
        model=model,
        attempt=attempt,
        llm_call_index=llm_call_index,
        estimated_tokens=estimated_tokens,
        context_window=context_window,
        percentage=percentage,
        source=source,
        freshness=freshness,
        estimated=True,
    )
