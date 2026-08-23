"""Tests for the Pi-style steer mechanism.

A steer message submitted while the agent works is delivered after the
current tool completes, cancelling remaining tool calls in the current
batch. If the agent is generating text (no tool boundary), the steer stays
queued and is delivered as the next turn (follow-up semantics).
"""

from __future__ import annotations

from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.tools import ToolAnnotations, tool
from tests.sdk.test_sdk_loop import MockProvider, echo


def _destructive_tool(name: str, log: list[str], steer_loop: dict | None = None):
    @tool
    def _impl() -> str:
        log.append(name)
        if steer_loop is not None and steer_loop.get("loop") is not None:
            steer_loop["loop"].steer("stop now")
        return f"{name}:ok"

    _impl.name = name
    _impl.annotations = ToolAnnotations(title=name, destructive=True)
    return _impl


class TestSteer:
    async def test_steer_during_tool_execution_cancels_remaining_tools(self):
        executed: list[str] = []
        steer_loop: dict = {}
        first = _destructive_tool("first_tool", executed, steer_loop)
        second = _destructive_tool("second_tool", executed)

        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="first_tool", arguments={}),
                        ToolCall(id="c2", name="second_tool", arguments={}),
                    ],
                ),
                Message.assistant(content="stopped"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[first, second])
        steer_loop["loop"] = loop

        result = await loop.run([Message.user("go")])

        # Second tool was cancelled by the steer
        assert executed == ["first_tool"]
        # Steer injected as a user message
        user_msgs = [m for m in result if m.role == "user"]
        assert any("stop now" in (m.content or "") for m in user_msgs)
        # Cancelled tool got a tool_result explaining the cancellation
        cancelled = [
            m for m in result if m.role == "tool" and "cancelled" in (m.content or "")
        ]
        assert len(cancelled) == 1
        assert "second_tool" in cancelled[0].content
        # The next LLM call saw the steer and answered
        assert result[-1].content == "stopped"

    async def test_steer_before_run_injected_at_first_tool_boundary(self):
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "hi"}),
                    ],
                ),
                Message.assistant(content="after steer"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        loop.steer("redirect")

        result = await loop.run([Message.user("go")])

        user_msgs = [m for m in result if m.role == "user"]
        assert any("redirect" in (m.content or "") for m in user_msgs)
        assert result[-1].content == "after steer"
        assert not loop.has_pending_steer()

    async def test_steer_queued_without_tool_boundary_stays_pending(self):
        provider = MockProvider(responses=[Message.assistant(content="Hello!")])
        loop = AgentLoop(provider=provider, tools=[])
        loop.steer("wait")

        result = await loop.run([Message.user("Hi")])

        # No tool boundary → steer stays queued for the next turn
        assert result[-1].content == "Hello!"
        assert loop.has_pending_steer()
        assert loop.pop_steer() == "wait"
        assert not loop.has_pending_steer()

    async def test_steer_ignores_blank_messages(self):
        loop = AgentLoop(provider=MockProvider(responses=[Message.assistant(content="ok")]), tools=[])
        loop.steer("   ")
        loop.steer("")
        assert not loop.has_pending_steer()

    async def test_steer_streaming_cancels_remaining_tools(self):
        executed: list[str] = []
        steer_loop: dict = {}
        first = _destructive_tool("first_tool", executed, steer_loop)
        second = _destructive_tool("second_tool", executed)

        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content=""),
                    StreamChunk.tool_input_start(tool="first_tool", call_id="c1", args={}),
                    StreamChunk.tool_input_end(tool="first_tool", call_id="c1"),
                    StreamChunk.tool_input_start(tool="second_tool", call_id="c2", args={}),
                    StreamChunk.tool_input_end(tool="second_tool", call_id="c2"),
                    StreamChunk.done(content=""),
                ]
            ]
        )
        loop = AgentLoop(provider=provider, tools=[first, second])
        steer_loop["loop"] = loop

        chunks = []
        async for chunk in loop.run_stream([Message.user("go")]):
            chunks.append(chunk)

        # First tool executed; second cancelled by the steer
        assert executed == ["first_tool"]
        cancelled_events = [
            c
            for c in chunks
            if c.type == "tool_result" and "cancelled" in (c.result_preview or "")
        ]
        assert len(cancelled_events) == 1
        assert "second_tool" in cancelled_events[0].result_preview
        # Steer injected into state
        assert loop.state is not None
        user_msgs = [m for m in loop.state.messages if m.role == "user"]
        assert any("stop now" in (m.content or "") for m in user_msgs)

    async def test_steer_streaming_after_parallel_batch_terminates(self):
        """Regression: the streaming loop's `continue` must advance the
        iteration counter (while-loop, not for-loop) or the steer path after
        a parallel batch loops forever."""
        executed: list[str] = []
        steer_loop: dict = {}

        @tool
        def first_tool() -> str:
            executed.append("first_tool")
            steer_loop["loop"].steer("stop now")
            return "ok"

        @tool
        def second_tool() -> str:
            executed.append("second_tool")
            return "ok"

        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content=""),
                    StreamChunk.tool_input_start(tool="first_tool", call_id="c1", args={}),
                    StreamChunk.tool_input_end(tool="first_tool", call_id="c1"),
                    StreamChunk.tool_input_start(tool="second_tool", call_id="c2", args={}),
                    StreamChunk.tool_input_end(tool="second_tool", call_id="c2"),
                    StreamChunk.done(content=""),
                ]
            ]
        )
        loop = AgentLoop(provider=provider, tools=[first_tool, second_tool])
        steer_loop["loop"] = loop

        chunks = []
        async for chunk in loop.run_stream([Message.user("go")]):
            chunks.append(chunk)

        # Both parallel-safe tools executed (batch completes before the steer
        # is drained); the loop must terminate (regression: infinite loop).
        assert executed == ["first_tool", "second_tool"]
        assert any(c.type == "done" for c in chunks)
        assert loop.state is not None
        user_msgs = [m for m in loop.state.messages if m.role == "user"]
        assert any("stop now" in (m.content or "") for m in user_msgs)

    async def test_steer_sink_called_at_injection(self):
        """The steer sink persists at injection time (correct transcript
        position) — the WS layer relies on this to avoid double-persisting
        follow-up steers."""
        sink_calls: list[str] = []
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "hi"}),
                    ],
                ),
                Message.assistant(content="done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        loop.set_steer_sink(sink_calls.append)
        loop.steer("redirect")

        await loop.run([Message.user("go")])

        assert sink_calls == ["redirect"]

    async def test_steer_sink_not_called_for_followup_steer(self):
        """A steer that never reaches a tool boundary (text-only turn) is not
        injected, so the sink is not called — the follow-up run persists it
        via the normal user-message path instead."""
        sink_calls: list[str] = []
        provider = MockProvider(responses=[Message.assistant(content="Hello!")])
        loop = AgentLoop(provider=provider, tools=[])
        loop.set_steer_sink(sink_calls.append)
        loop.steer("wait")

        await loop.run([Message.user("Hi")])

        assert sink_calls == []
        assert loop.has_pending_steer()
