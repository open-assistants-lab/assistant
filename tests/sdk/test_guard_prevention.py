"""Output guardrails must PREVENT, not detect (issue #74).

A guardrail that trips after the bytes were emitted is an alert system. These
tests pin the stronger contract: nothing the model produced reaches the client
or the session-log observer until the output guardrails for the round have
passed.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.sdk.guardrails import GuardrailResult
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.tools import tool
from tests.sdk.test_sdk_loop import MockProvider


class _AlwaysTrip:
    name = "secret_filter"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def check(self, output: str, state: Any) -> GuardrailResult:
        self.seen.append(output)
        return GuardrailResult(tripwire_triggered=True, message="sensitive output")


class _Pass:
    name = "allow_all"

    async def check(self, output: str, state: Any) -> GuardrailResult:
        return GuardrailResult(tripwire_triggered=False, message="")


class _TripOnSecret:
    """Trips only when the secret appears — lets other rounds through."""

    name = "secret_filter"

    async def check(self, output: str, state: Any) -> GuardrailResult:
        return GuardrailResult(
            tripwire_triggered="SECRET" in output, message="sensitive output"
        )


def _observe(loop: AgentLoop, monkeypatch) -> list[str]:
    """Record every message the session-log observer sees."""
    seen: list[str] = []

    def spy(message: Message) -> None:
        seen.append(str(message.content or ""))

    monkeypatch.setattr(loop, "_log_session_message", spy, raising=False)
    return seen


def _stream_text(text: str) -> list[StreamChunk]:
    return [StreamChunk.text_delta(content=text), StreamChunk.done(content=text)]


@tool
async def probe() -> str:
    """A tool the model calls once."""
    return "probe done"


class TestStreamedTextIsHeldUntilTheGuardPasses:
    @pytest.mark.asyncio
    async def test_blocked_text_never_reaches_the_client(self):
        guard = _AlwaysTrip()
        provider = MockProvider()
        provider.set_stream_events([_stream_text("SECRET")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("say something")])]

        delivered = "".join(str(c.content or "") for c in chunks)
        assert "SECRET" not in delivered, f"blocked text was streamed: {delivered!r}"
        assert any(c.canonical_type == "error" for c in chunks), "no block signal emitted"
        assert any(c.canonical_type == "done" for c in chunks)

    @pytest.mark.asyncio
    async def test_the_observer_never_sees_blocked_text(self, monkeypatch):
        guard = _AlwaysTrip()
        provider = MockProvider()
        provider.set_stream_events([_stream_text("SECRET")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        observed = _observe(loop, monkeypatch)

        async for _ in loop.run_stream([Message.user("say something")]):
            pass

        assert not any("SECRET" in text for text in observed), observed

    @pytest.mark.asyncio
    async def test_blocked_reasoning_is_not_streamed_either(self):
        guard = _AlwaysTrip()
        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.reasoning_delta(content="SECRET REASONING"),
                StreamChunk.text_delta(content="SECRET"),
                StreamChunk.done(content="SECRET"),
            ]
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        delivered = "".join(str(c.content or "") for c in chunks)
        assert "SECRET" not in delivered, delivered

    @pytest.mark.asyncio
    async def test_allowed_text_still_streams_with_its_block_boundaries(self):
        guard = _Pass()
        provider = MockProvider()
        provider.set_stream_events([_stream_text("hello world")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]

        kinds = [c.canonical_type for c in chunks]
        assert "text_delta" in kinds, kinds
        assert "done" in kinds
        text = "".join(str(c.content or "") for c in chunks if c.canonical_type == "text_delta")
        assert text == "hello world", text
        assert kinds.index("text_delta") < kinds.index("done")


class TestNonStreamObserverSeesOnlyGuardedContent:
    @pytest.mark.asyncio
    async def test_blocked_text_never_reaches_the_observer(self, monkeypatch):
        guard = _AlwaysTrip()
        provider = MockProvider(responses=[Message.assistant(content="SECRET")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        observed = _observe(loop, monkeypatch)

        result = await loop.run([Message.user("go")])

        assert not any("SECRET" in text for text in observed), observed
        assert not any("SECRET" in str(m.content) for m in result)

    @pytest.mark.asyncio
    async def test_allowed_text_reaches_the_observer(self, monkeypatch):
        guard = _Pass()
        provider = MockProvider(responses=[Message.assistant(content="fine")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        observed = _observe(loop, monkeypatch)
        await loop.run([Message.user("go")])
        assert any("fine" in text for text in observed), observed


class TestToolRoundNarrationIsGuardedToo:
    @pytest.mark.asyncio
    async def test_narration_before_a_tool_call_is_not_streamed_unchecked(self):
        """A round that ends in a tool call also emits model text — guard it."""
        guard = _TripOnSecret()
        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.text_delta(content="SECRET narration"),
                StreamChunk.tool_input_start(tool="probe", call_id="c1"),
                StreamChunk.tool_input_delta(call_id="c1", content="{}"),
                StreamChunk.tool_input_end(tool="probe", call_id="c1"),
                StreamChunk.done(content=""),
            ],
            _stream_text("all done"),
        ])
        loop = AgentLoop(provider=provider, tools=[probe], output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]

        delivered = "".join(str(c.content or "") for c in chunks)
        assert "SECRET" not in delivered, f"tool-round narration bypassed the guard: {delivered!r}"
        # The tool still ran: the trip was on narration, not on the action.
        assert any(m.role == "tool" for m in loop.state.messages)
        # And an allowed later round still streams.
        assert "all done" in delivered

    @pytest.mark.asyncio
    async def test_allowed_narration_still_reaches_the_client(self):
        guard = _Pass()
        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.text_delta(content="thinking out loud"),
                StreamChunk.tool_input_start(tool="probe", call_id="c1"),
                StreamChunk.tool_input_delta(call_id="c1", content="{}"),
                StreamChunk.tool_input_end(tool="probe", call_id="c1"),
                StreamChunk.done(content=""),
            ],
            _stream_text("all done"),
        ])
        loop = AgentLoop(provider=provider, tools=[probe], output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        delivered = "".join(str(c.content or "") for c in chunks)
        assert "thinking out loud" in delivered, delivered