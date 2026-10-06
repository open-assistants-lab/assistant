"""Follow-ups to the guardrail-prevention work (#148) and the hold buffer (#150)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.sdk.guardrails import GuardrailResult
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk
from tests.sdk.test_sdk_loop import MockProvider


class _TripOnSecret:
    name = "secret_filter"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def check(self, output: str, state: Any) -> GuardrailResult:
        self.seen.append(output)
        return GuardrailResult(
            tripwire_triggered="SECRET" in output, message="sensitive output"
        )


class _AlwaysTrip:
    name = "secret_filter"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def check(self, output: str, state: Any) -> GuardrailResult:
        self.seen.append(output)
        return GuardrailResult(tripwire_triggered=True, message="sensitive output")


def _observe(loop: AgentLoop, monkeypatch) -> list[str]:
    seen: list[str] = []

    def spy(message: Message) -> None:
        seen.append(str(message.content or ""))
        seen.append(str(getattr(message, "reasoning", "") or ""))

    monkeypatch.setattr(loop, "_log_session_message", spy, raising=False)
    return seen


class TestReasoningIsChecked:
    """#148: reasoning is model output; a clean answer must not carry it out."""

    @pytest.mark.asyncio
    async def test_safe_answer_with_secret_reasoning_is_blocked(self):
        guard = _TripOnSecret()
        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.reasoning_delta(content="SECRET REASONING"),
                StreamChunk.text_delta(content="fine"),
                StreamChunk.done(content="fine"),
            ]
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        delivered = "".join(str(c.content or "") for c in chunks)
        assert "SECRET" not in delivered, f"unchecked reasoning leaked: {delivered!r}"
        assert "SECRET REASONING" in "".join(guard.seen), (
            "the guard never saw the reasoning"
        )

    @pytest.mark.asyncio
    async def test_reasoning_only_round_is_still_checked(self):
        guard = _AlwaysTrip()
        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.reasoning_delta(content="thinking"),
                StreamChunk.done(content=""),
            ]
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        assert guard.seen, "a reasoning-only round skipped the output check"
        assert any(c.canonical_type == "error" for c in chunks)

    @pytest.mark.asyncio
    async def test_nonstream_reasoning_is_checked_and_scrubbed(self, monkeypatch):
        guard = _TripOnSecret()
        provider = MockProvider(responses=[
            Message.assistant(content="fine", reasoning="SECRET REASONING")
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        observed = _observe(loop, monkeypatch)
        result = await loop.run([Message.user("go")])
        assert not any("SECRET" in text for text in observed), observed
        assert not any(
            "SECRET" in str(getattr(m, "reasoning", "") or "") for m in result
        )

    @pytest.mark.asyncio
    async def test_allowed_reasoning_still_reaches_the_client(self):
        class _Pass:
            name = "allow"

            async def check(self, output: str, state: Any) -> GuardrailResult:
                return GuardrailResult(tripwire_triggered=False, message="")

        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.reasoning_delta(content="thinking"),
                StreamChunk.text_delta(content="answer"),
                StreamChunk.done(content="answer"),
            ]
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[_Pass()])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        delivered = "".join(str(c.content or "") for c in chunks)
        assert "thinking" in delivered and "answer" in delivered


class TestUnguardedRunsKeepStreaming:
    """#150: the hold buffer is a guarding cost, not a default."""

    @pytest.mark.asyncio
    async def test_without_guards_a_delta_arrives_before_the_provider_finishes(self):
        gate = asyncio.Event()

        class SlowProvider(MockProvider):
            async def chat_stream_impl(self, *args, **kwargs):
                yield StreamChunk.text_delta(content="first")
                await gate.wait()
                yield StreamChunk.text_delta(content="second")
                yield StreamChunk.done(content="firstsecond")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                return self.chat_stream_impl(messages, tools, model, **kwargs)

        loop = AgentLoop(provider=SlowProvider(), output_guardrails=[])

        received: list[str] = []
        consumer = asyncio.create_task(_collect_first_delta(loop, received))
        await asyncio.wait_for(consumer, timeout=2)
        assert received == ["first"], (
            "an unguarded stream held the delta until the round finished"
        )
        gate.set()

    @pytest.mark.asyncio
    async def test_with_guards_the_delta_is_held(self):
        class _Pass:
            name = "allow"

            async def check(self, output: str, state: Any) -> GuardrailResult:
                return GuardrailResult(tripwire_triggered=False, message="")

        gate = asyncio.Event()

        class SlowProvider(MockProvider):
            async def chat_stream_impl(self, *args, **kwargs):
                yield StreamChunk.text_delta(content="first")
                await gate.wait()
                yield StreamChunk.done(content="first")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                return self.chat_stream_impl(messages, tools, model, **kwargs)

        loop = AgentLoop(provider=SlowProvider(), output_guardrails=[_Pass()])

        received: list[str] = []
        consumer = asyncio.create_task(_collect_first_delta(loop, received))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(consumer, timeout=0.5)
        assert received == [], "guarded content was delivered before the check"
        gate.set()
        consumer.cancel()


async def _collect_first_delta(loop: AgentLoop, received: list[str]) -> None:
    async for chunk in loop.run_stream([Message.user("go")]):
        if chunk.canonical_type == "text_delta" and chunk.content:
            received.append(str(chunk.content))
            return
