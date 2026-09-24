"""Issue #18 regressions: payload-aware trigger, escape hatch, logging.

Long-lived sessions grew unbounded until the provider 400'd: the trigger
undercounted the real payload (tools + system prompt excluded), the
summarization LLM call failed with the same oversized context (no escape
hatch), and the async path logged nothing.
"""

from __future__ import annotations

import pytest

from src.sdk.compression import (
    CompressionContext,
    CompressionReason,
)
from src.sdk.messages import Message
from src.sdk.middleware_summarization import SummarizationMiddleware


def _mw(**kw) -> SummarizationMiddleware:
    kw.setdefault("model", "test-provider:test-model")
    kw.setdefault("trigger", ("tokens", 100))
    return SummarizationMiddleware(**kw)


def _ctx(session_id: str = "s1") -> CompressionContext:
    return CompressionContext(
        session_id=session_id,
        model="test-provider:test-model",
        attempt=1,
        llm_call_index=1,
        reason=CompressionReason.THRESHOLD,
    )


def _big_history(n: int = 12) -> list[Message]:
    return [
        Message.user(f"msg {i} " + "word " * 30) if i % 2 == 0 else Message.assistant(f"reply {i} " + "word " * 30)
        for i in range(n)
    ]


class TestPayloadAwareTrigger:
    def test_overhead_counts_toward_trigger(self):
        """Issue #18 defect 2: tools + system prompt tokens count."""
        mw = _mw(payload_overhead_tokens=5000)
        messages = [_msg("user", "hi")]  # tiny conversation
        total = mw.token_counter(messages)
        assert total < 100
        # Without overhead: no trigger. With 5000 overhead: fires.
        assert mw._should_summarize(messages, total) is False
        assert mw._should_summarize(messages, total + mw.payload_overhead_tokens) is True

    def test_overhead_default_zero(self):
        mw = _mw()
        assert mw.payload_overhead_tokens == 0


class TestSummaryBudget:
    def test_oversized_summary_uses_bounded_fallback(self):
        mw = _mw(max_summary_chars=100)

        bounded = mw._bounded_summary("x" * 50_000)

        assert len(bounded) <= 100
        assert "[... summary omitted ...]" in bounded


class TestEscapeHatch:
    @pytest.mark.asyncio
    async def test_summary_failure_forces_trim(self, monkeypatch, fake_mw_logger):
        """Issue #18 defect 3: summary LLM failure -> force-trim the oldest
        messages via the pruner so the session recovers."""
        mw = _mw(keep=("messages", 4))
        pruned_calls: list[tuple[str, int]] = []

        def fake_pruner(session_id: str, keep_messages: int) -> int:
            pruned_calls.append((session_id, keep_messages))
            return 8

        mw.context_pruner = fake_pruner

        # A summary provider that always 400s (like the real oversized case).
        async def broken_factory():
            class Broken:
                async def chat(self, **kw):
                    from src.sdk.messages import Message

                    return Message(content="", role="assistant")  # empty -> generation error

            return Broken()

        mw.summary_provider_factory = broken_factory
        mw.payload_overhead_tokens = 5000  # force the trigger with a tiny history

        state_messages = _big_history(12)
        update = await mw.abefore_model(
            _state(state_messages),  # type: ignore[arg-type]
        )

        # In-memory state must be trimmed to the keep boundary...
        assert update is not None and "messages" in update
        assert len(update["messages"]) <= 4
        # ...the pruner ran (store-side exclusion)...
        assert pruned_calls and pruned_calls[0][1] == 4
        # ...and the failure + forced trim are logged (no longer silent).
        events = [e for _, e, _ in fake_mw_logger.events]
        assert any("summarization.forced_trim" in e for e in events)
        assert any("summarization.persist" in e or "summarization.trigger" in e for e in events)

    @pytest.mark.asyncio
    async def test_failed_update_excludes_oversized_previous_summary(self, fake_mw_logger):
        excluded: list[str] = []
        mw = _mw(
            keep=("messages", 2),
            max_summary_chars=100,
            summary_context_excluder=lambda summary_id: excluded.append(summary_id) or True,
        )
        mw.context_pruner = lambda session_id, keep_messages: 4
        mw.payload_overhead_tokens = 0

        async def broken_factory():
            class Broken:
                async def chat(self, **kwargs):
                    raise TimeoutError("summary timed out")

            return Broken()

        mw.summary_provider_factory = broken_factory
        previous = Message(
            role="user",
            content="Here is a summary of the conversation to date:\n" + "x" * 50_000,
            source="summarization_middleware",
            storage_id="summary-1",
        )

        update = await mw.abefore_model(_state([previous, *_big_history(4)]))

        assert update is not None
        assert excluded == ["summary-1"]
        assert all(message.storage_id != "summary-1" for message in update["messages"])


class TestTriggerLogging:
    @pytest.mark.asyncio
    async def test_trigger_evaluation_logged(self, monkeypatch, fake_mw_logger):
        """Issue #18: the async path logs trigger evaluations (was silent)."""
        mw = _mw()
        state_messages = _big_history(6)
        await mw.abefore_model(_state(state_messages))  # type: ignore[arg-type]
        events = [e for _, e, _ in fake_mw_logger.events]
        assert any("summarization.trigger_eval" in e for e in events)




class _FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def debug(self, event, data, user_id="default_user", channel="cli"):
        self.events.append(("debug", event, data))

    def info(self, event, data, user_id="default_user", channel="cli"):
        self.events.append(("info", event, data))

    def warning(self, event, data, user_id="default_user", channel="cli"):
        self.events.append(("warning", event, data))

    def error(self, event, data, user_id="default_user", channel="cli"):
        self.events.append(("error", event, data))


@pytest.fixture()
def fake_mw_logger(monkeypatch):
    import src.sdk.middleware_summarization as mw_mod

    fake = _FakeLogger()
    monkeypatch.setattr(mw_mod, "logger", fake)
    return fake

# -- helpers --

def _msg(role: str, content: str) -> Message:
    return Message.user(content) if role == "user" else Message.assistant(content)


def _state(messages):
    """Build a minimal AgentState with a compression context."""
    from src.sdk.state import AgentState

    state = AgentState(messages=list(messages))
    state.extra["_compression_context"] = CompressionContext(
        session_id="s1",
        model="test-provider:test-model",
        attempt=1,
        llm_call_index=1,
        reason=CompressionReason.THRESHOLD,
    )
    return state
