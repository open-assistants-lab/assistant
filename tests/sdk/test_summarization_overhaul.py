"""Tests for summarization middleware — aligned with LangChain API."""

from __future__ import annotations

import pytest

from src.sdk.messages import Message


def _msg(role: str, content: str = "", tool_call_id: str | None = None, tool_calls=None) -> Message:
    if role == "tool":
        return Message(role="tool", content=content, tool_call_id=tool_call_id or "tc1", name="time_get")
    if role == "assistant" and tool_calls:
        return Message(role="assistant", content=content, tool_calls=tool_calls)
    return Message(role=role, content=content)


def _tc(tc_id: str, name: str = "time_get", args=None) -> Message:
    from src.sdk.messages import ToolCall
    return ToolCall(id=tc_id, name=name, arguments=args or {})


# -- ProviderContextOverflowError --


def test_provider_context_overflow_error_exists():
    from src.sdk.providers.base import ProviderContextOverflowError
    err = ProviderContextOverflowError("too long")
    assert "too long" in str(err)
    assert isinstance(err, Exception)


# -- AI/Tool pair preservation --


def test_find_safe_cutoff_point_preserves_ai_tool_pair():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("assistant", "let me check", tool_calls=[_tc("tc1")]),
        _msg("tool", "result", tool_call_id="tc1"),
        _msg("user", "thanks"),
        _msg("assistant", "done"),
    ]

    # Cutoff at index 2 (tool message) — should search back to include the assistant at index 1
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 2)
    assert cutoff == 1  # Include the assistant message that initiated the tool call


def test_find_safe_cutoff_point_no_tool_at_cutoff():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("assistant", "hi"),
        _msg("user", "bye"),
    ]

    # No tool at cutoff — return as-is
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 1)
    assert cutoff == 1


def test_find_safe_cutoff_point_advances_past_orphaned_tools():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("tool", "orphan result", tool_call_id="tc_missing"),
        _msg("user", "bye"),
    ]

    # No matching AI message found — should advance past tool messages
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 1)
    assert cutoff == 2  # Skip the orphaned tool message


# -- Token counting --


def test_count_tokens_returns_positive():
    from src.sdk.middleware_summarization import count_tokens_approximately

    messages = [_msg("user", "hello world")]
    tokens = count_tokens_approximately(messages)
    assert tokens > 0


def test_count_tokens_public_method():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    tokens = mw.count_tokens([_msg("user", "hello")])
    assert tokens > 0


# -- Trigger evaluation --


def test_trigger_tokens_exceeds():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("tokens", 5))
    messages = [_msg("user", "hello world this is a long message")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is True


def test_trigger_tokens_below_threshold():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("tokens", 10000))
    messages = [_msg("user", "hi")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is False


def test_trigger_messages_exceeds():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("messages", 3))
    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3"), _msg("assistant", "4")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is True


def test_trigger_and_clause():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger={"tokens": 5, "messages": 3})
    messages = [_msg("user", "1"), _msg("assistant", "2")]
    total = mw.token_counter(messages)
    # Only 2 messages but tokens > 5 — AND clause requires both, so should NOT trigger
    assert mw._should_summarize(messages, total) is False


def test_trigger_or_clause():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=[("tokens", 5), ("messages", 3)])
    messages = [_msg("user", "1"), _msg("assistant", "2")]
    total = mw.token_counter(messages)
    # 2 messages (< 3) but tokens > 5 — OR clause, should trigger
    assert mw._should_summarize(messages, total) is True


def test_trigger_none_never_triggers():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=None)
    messages = [_msg("user", "hello" * 1000)]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is False


# -- Cutoff determination --


def test_determine_cutoff_with_messages_keep():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("messages", 2))
    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3"), _msg("assistant", "4")]
    cutoff = mw._determine_cutoff_index(messages)
    assert cutoff == 2  # Keep last 2 messages


def test_determine_cutoff_with_tokens_keep():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("tokens", 10))
    messages = [_msg("user", "long message here"), _msg("assistant", "short"), _msg("user", "bye")]
    cutoff = mw._determine_cutoff_index(messages)
    assert cutoff >= 1  # At least 1 message to summarize


# -- Message trimming --


def test_trim_messages_for_summary():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trim_tokens_to_summarize=20)
    messages = [_msg("user", f"message {i} " * 10) for i in range(10)]
    trimmed = mw._trim_messages_for_summary(messages)
    assert len(trimmed) < len(messages)


def test_trim_messages_none_returns_all():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trim_tokens_to_summarize=None)
    messages = [_msg("user", "hello")]
    trimmed = mw._trim_messages_for_summary(messages)
    assert len(trimmed) == len(messages)


# -- Summary message type --


def test_build_new_messages_uses_user_role_with_source():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    msgs = SummarizationMiddleware._build_new_messages("test summary")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert "test summary" in msgs[0].content
    assert getattr(msgs[0], "source", None) == "summarization_middleware"


# -- force_summarize --


@pytest.mark.asyncio
async def test_force_summarize_returns_false_for_short_conversation():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    state = AgentState(messages=[Message.user("hi")])
    result = await mw.force_summarize(state)
    assert result is False


@pytest.mark.asyncio
async def test_force_summarize_returns_false_for_two_messages():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("messages", 2))
    state = AgentState(messages=[Message.user("hi"), Message.assistant("hello")])
    # Can't split — all messages are in the "keep" window
    result = await mw.force_summarize(state)
    assert result is False


# -- Constructor validation --


def test_validate_context_size_rejects_zero():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    with pytest.raises(ValueError):
        SummarizationMiddleware._validate_context_size(("tokens", 0), "trigger")


def test_validate_context_size_rejects_invalid_fraction():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    with pytest.raises(ValueError):
        SummarizationMiddleware._validate_context_size(("fraction", 1.5), "keep")


def test_normalize_trigger_rejects_unknown_key():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    with pytest.raises(ValueError):
        mw._normalize_trigger({"unknown": 5})  # type: ignore[dict-item]


# -- Partition --


def test_partition_messages():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3")]
    to_summarize, preserved = SummarizationMiddleware._partition_messages(messages, 2)
    assert len(to_summarize) == 2
    assert len(preserved) == 1
    assert preserved[0].content == "3"
