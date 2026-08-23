"""Tests for incremental + structured summarization (Pi-style compaction).

Covers:
- structured default summary prompt (Goal / Progress / Decisions / Next Steps)
- incremental update: second compression uses the update prompt with the
  previous summary and only summarizes messages after it
- file operation tracking appended to the summary
- split-turn handling: cutting mid-turn generates a turn-prefix summary
- previous summary message is replaced, never duplicated
"""

from __future__ import annotations

from src.sdk.compression import CompressionContext, CompressionStatus
from src.sdk.messages import Message, ToolCall
from src.sdk.middleware_summarization import (
    DEFAULT_SUMMARY_PROMPT,
    TURN_PREFIX_SUMMARY_PROMPT,
    UPDATE_SUMMARY_PROMPT,
    SummarizationMiddleware,
)
from src.sdk.state import AgentState


def _msg(role: str, content: str = "", tool_call_id: str | None = None, tool_calls=None) -> Message:
    if role == "tool":
        return Message(role="tool", content=content, tool_call_id=tool_call_id or "tc1", name="time_get")
    if role == "assistant" and tool_calls:
        return Message(role="assistant", content=content, tool_calls=tool_calls)
    return Message(role=role, content=content)


def _tc(tc_id: str, name: str, args=None) -> ToolCall:
    return ToolCall(id=tc_id, name=name, arguments=args or {})


def _context(reason="manual") -> CompressionContext:
    return CompressionContext(
        session_id="session-1",
        model="ollama-cloud:test",
        attempt=1,
        llm_call_index=1,
        reason=reason,
    )


class _Provider:
    """Records every prompt it receives."""

    def __init__(self, content="summary"):
        self.content = content
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages):
        self.calls += 1
        self.prompts.append(messages[-1].content)
        return Message.assistant(self.content)


def _make_middleware(provider, **kwargs) -> SummarizationMiddleware:
    return SummarizationMiddleware(
        "ollama-cloud:test",
        summary_provider_factory=lambda: provider,
        **kwargs,
    )


async def _compress(mw: SummarizationMiddleware, messages: list[Message], reason="manual"):
    state = AgentState(messages=list(messages), extra={"_compression_context": _context(reason)})
    result = await mw.force_summarize(state, _context(reason))
    return result, state


def _summary_messages(messages: list[Message]) -> list[Message]:
    return [m for m in messages if m.source == "summarization_middleware"]


# ---------------------------------------------------------------------------
# Structured prompt
# ---------------------------------------------------------------------------


def test_default_prompt_is_structured():
    for section in (
        "## Goal",
        "## Constraints & Preferences",
        "## Progress",
        "## Key Decisions",
        "## Next Steps",
        "## Critical Context",
    ):
        assert section in DEFAULT_SUMMARY_PROMPT


def test_update_prompt_preserves_previous_information():
    assert "PRESERVE all existing information" in UPDATE_SUMMARY_PROMPT
    assert "## Goal" in UPDATE_SUMMARY_PROMPT
    assert "## Next Steps" in UPDATE_SUMMARY_PROMPT


def test_turn_prefix_prompt_exists():
    assert "## Original Request" in TURN_PREFIX_SUMMARY_PROMPT
    assert "## Context for Suffix" in TURN_PREFIX_SUMMARY_PROMPT


# ---------------------------------------------------------------------------
# Incremental update
# ---------------------------------------------------------------------------


async def test_second_compression_uses_update_prompt_with_previous_summary():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i}") for i in range(20)]

    result1, state1 = await _compress(mw, messages)
    assert result1.telemetry.status is CompressionStatus.SUCCEEDED
    assert "<previous-summary>" not in provider.prompts[0]
    assert "## Goal" in provider.prompts[0]

    # Add new messages after the first compression
    state1.messages.extend([_msg("user", f"user-{20 + i}") for i in range(10)])
    result2 = await mw.force_summarize(state1, _context())

    assert result2.telemetry.status is CompressionStatus.SUCCEEDED
    assert provider.calls == 2
    second_prompt = provider.prompts[1]
    assert "<previous-summary>" in second_prompt
    assert "summary" in second_prompt  # previous summary text embedded
    assert "Update the existing structured summary" in second_prompt


async def test_incremental_summarizes_only_messages_after_previous_summary():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i:02d}") for i in range(20)]

    _, state1 = await _compress(mw, messages)
    state1.messages.extend([_msg("user", f"user-{20 + i:02d}") for i in range(10)])
    await mw.force_summarize(state1, _context())

    second_prompt = provider.prompts[1]
    # Old pre-summary content must not be re-sent
    assert "user-01" not in second_prompt
    assert "user-05" not in second_prompt
    # New messages after the previous summary are included
    assert "user-21" in second_prompt


async def test_previous_summary_message_is_replaced_not_duplicated():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i}") for i in range(20)]

    _, state1 = await _compress(mw, messages)
    assert len(_summary_messages(state1.messages)) == 1

    state1.messages.extend([_msg("user", f"user-{20 + i}") for i in range(10)])
    await mw.force_summarize(state1, _context())

    assert len(_summary_messages(state1.messages)) == 1


async def test_no_new_messages_after_previous_summary_skips():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i}") for i in range(20)]

    _, state1 = await _compress(mw, messages)
    provider.calls = 0
    result2 = await mw.force_summarize(state1, _context())

    assert result2.telemetry.status is CompressionStatus.SKIPPED
    assert provider.calls == 0


# ---------------------------------------------------------------------------
# File operation tracking
# ---------------------------------------------------------------------------


async def test_file_ops_appended_to_summary():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [
        _msg("user", "work on files"),
        _msg(
            "assistant",
            tool_calls=[
                _tc("c1", "files_read", {"path": "src/a.py"}),
                _tc("c2", "files_write", {"path": "src/b.py"}),
                _tc("c3", "files_edit", {"path": "src/c.py"}),
            ],
        ),
        _msg("tool", "ok", tool_call_id="c1"),
        _msg("tool", "ok", tool_call_id="c2"),
        _msg("tool", "ok", tool_call_id="c3"),
        _msg("user", "done"),
        _msg("assistant", "finished"),
    ]
    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert result.artifact is not None
    summary = result.artifact.summary
    assert "## Files" in summary
    assert "src/a.py" in summary
    assert "src/b.py" in summary
    assert "src/c.py" in summary


async def test_file_ops_empty_when_no_file_tools():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [
        _msg("user", "hello"),
        _msg("assistant", "hi"),
        _msg("user", "again"),
        _msg("assistant", "ok"),
    ]
    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert result.artifact is not None
    assert "## Files" not in result.artifact.summary


# ---------------------------------------------------------------------------
# Split-turn handling
# ---------------------------------------------------------------------------


async def test_split_turn_generates_turn_prefix_summary():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1", tool_calls=[_tc("c1", "time_get")]),
        _msg("tool", "t1", tool_call_id="c1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
        _msg("tool", "t2", tool_call_id="c2"),
    ]
    result, state = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    # Two LLM calls: history summary + turn prefix summary
    assert provider.calls == 2
    assert "## Original Request" in provider.prompts[1]
    assert result.artifact is not None
    assert "Turn Context (split turn)" in result.artifact.summary
    # Cut assistant message + its tool result are kept; turn prefix dropped
    roles = [m.role for m in state.messages]
    assert roles == ["user", "assistant", "tool"]
    assert state.messages[1].content == "a2"


async def test_no_split_when_cut_at_user_message():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
    ]
    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert provider.calls == 1
    assert "Turn Context (split turn)" not in result.artifact.summary  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Update prompt shape
# ---------------------------------------------------------------------------


async def test_update_prompt_wraps_conversation_in_tags():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i}") for i in range(20)]

    _, state1 = await _compress(mw, messages)
    state1.messages.extend([_msg("user", f"user-{20 + i}") for i in range(10)])
    await mw.force_summarize(state1, _context())

    second_prompt = provider.prompts[1]
    assert "<conversation>" in second_prompt
    assert "</conversation>" in second_prompt
    assert "<previous-summary>" in second_prompt
    assert "</previous-summary>" in second_prompt


# ---------------------------------------------------------------------------
# Summarization call hygiene (Pi-style)
# ---------------------------------------------------------------------------


class _ToolCallingProvider:
    """Returns an assistant message with tool calls (should be rejected)."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return Message.assistant(
            content="", tool_calls=[_tc("c1", "time_get")]
        )


async def test_summary_provider_tool_call_is_rejected():
    provider = _ToolCallingProvider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [_msg("user", f"user-{i}") for i in range(6)]

    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.FAILED
    assert result.telemetry.error_code == "tool_call"


class _FlakyProvider:
    """Fails once, then succeeds (transient error)."""

    def __init__(self, content="summary"):
        self.content = content
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("transient stream drop")
        return Message.assistant(self.content)


async def test_summary_provider_transient_error_is_retried():
    provider = _FlakyProvider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [_msg("user", f"user-{i}") for i in range(6)]

    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# Self-review regression tests
# ---------------------------------------------------------------------------


def test_extract_previous_summary_strips_storage_framing():
    mw = SummarizationMiddleware("ollama-cloud:test")
    stored = Message(
        role="user",
        content="[SUMMARY OF PREVIOUS CONVERSATION]\n## Goal\nDo the thing",
        source="summarization_middleware",
    )
    assert mw._extract_previous_summary(stored) == "## Goal\nDo the thing"

    in_memory = Message(
        role="user",
        content="Here is a summary of the conversation to date:\n\n## Goal\nDo it",
        source="summarization_middleware",
    )
    assert mw._extract_previous_summary(in_memory) == "## Goal\nDo it"


def _summary_message(text: str) -> Message:
    return Message(
        role="user",
        content=f"Here is a summary of the conversation to date:\n\n{text}",
        source="summarization_middleware",
    )


async def test_split_turn_with_empty_history_preserves_previous_summary():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 2))
    messages = [
        _summary_message("PREVIOUS_SUMMARY_MARKER"),
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("tool", "t1", tool_call_id="c1"),
    ]
    result, _ = await _compress(mw, messages)

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert result.artifact is not None
    # The previous summary must survive (it is replaced by the new one)
    assert "PREVIOUS_SUMMARY_MARKER" in result.artifact.summary
    assert "No prior history." not in result.artifact.summary
    assert "Turn Context (split turn)" in result.artifact.summary


async def test_update_prompt_includes_force_summarize_instructions():
    provider = _Provider()
    mw = _make_middleware(provider, keep=("messages", 5))
    messages = [_msg("user", f"user-{i:02d}") for i in range(20)]

    _, state1 = await _compress(mw, messages)
    state1.messages.extend([_msg("user", f"user-{20 + i:02d}") for i in range(10)])
    await mw.force_summarize(state1, _context(), instructions="focus on tests")

    second_prompt = provider.prompts[1]
    assert "Additional focus: focus on tests" in second_prompt
