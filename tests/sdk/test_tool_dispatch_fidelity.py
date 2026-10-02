"""Tool dispatch fidelity: #57, #58, #59, #61 (audit batch 2026-10-02)."""

import json
import tempfile
from pathlib import Path

import pytest

from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.state import AgentState
from src.sdk.tools import ToolAnnotations, tool
from tests.sdk.test_sdk_loop import MockProvider


@tool
async def reader(path: str) -> str:
    """Read a file."""
    return f"contents of {path}"


reader.annotations = ToolAnnotations(title="Read File", read_only=True, idempotent=True)


@tool
async def writer(path: str, body: str) -> str:
    """Change the world."""
    return f"wrote {path}"


@tool
async def other(value: str) -> str:
    """A second read-only tool with a distinguishable result."""
    return f"other saw {value}"


other.annotations = ToolAnnotations(title="Other", read_only=True, idempotent=True)


def _proposed_ids(messages: list[Message]) -> list[str]:
    return [tc.id for m in messages if m.role == "assistant" for tc in (m.tool_calls or [])]


def _answered_ids(messages: list[Message]) -> list[str]:
    return [m.tool_call_id for m in messages if m.role == "tool" and m.tool_call_id]


def _duplicated_stream() -> list[list[StreamChunk]]:
    def _call(call_id: str) -> list[StreamChunk]:
        return [
            StreamChunk.tool_input_start(tool="reader", call_id=call_id),
            StreamChunk.tool_input_delta(call_id=call_id, content='{"path": "x"}'),
            StreamChunk.tool_input_end(tool="reader", call_id=call_id),
        ]

    return [
        [*_call("a"), *_call("b"), StreamChunk.done(content="")],
        [StreamChunk.text_delta(content="done"), StreamChunk.done(content="done")],
    ]


class TestUnansweredToolCallIds:
    """#57: every assistant tool_call id must be answered, on both paths."""

    @pytest.mark.asyncio
    async def test_streamed_duplicate_ids_are_all_answered(self):
        """Two identical streamed calls (ids a/b) execute once; both are answered."""
        provider = MockProvider()
        provider.set_stream_events(_duplicated_stream())
        loop = AgentLoop(provider=provider, tools=[reader])
        chunks = [c async for c in loop.run_stream([Message.user("read x")])]
        assert any(c.canonical_type == "done" for c in chunks)
        messages = list(loop.state.messages)
        proposed, answered = _proposed_ids(messages), _answered_ids(messages)
        # The stored assistant message must not claim an id that is never
        # answered: before the fix it kept both a and b while only a ran.
        assert proposed == ["a"]
        assert answered == ["a"]
        results = [m for m in messages if m.role == "tool"]
        assert sum("contents of x" in str(m.content) for m in results) == 1, (
            "the identical second call must not execute a second time"
        )

    @pytest.mark.asyncio
    async def test_streamed_transcript_is_valid_for_strict_providers(self):
        """Strict providers reject an assistant tool_call with no matching result."""
        provider = MockProvider()
        provider.set_stream_events(_duplicated_stream())
        loop = AgentLoop(provider=provider, tools=[reader])
        async for _ in loop.run_stream([Message.user("read x")]):
            pass
        payload = [m.to_openai() for m in loop.state.messages]
        proposed = [tc["id"] for m in payload if m["role"] == "assistant"
                    for tc in m.get("tool_calls", [])]
        answered = [m["tool_call_id"] for m in payload if m["role"] == "tool"]
        assert sorted(proposed) == sorted(answered)


class TestDuplicateCacheScope:
    """#58: memoized reads must not survive a new turn, and writes invalidate them."""

    def test_a_previous_turns_read_is_not_memoized(self):
        loop = AgentLoop(provider=MockProvider(), tools=[reader])
        state = AgentState(messages=[
            Message.user("first question"),
            Message.assistant(tool_calls=[ToolCall(id="c1", name="reader", arguments={"path": "x"})]),
            Message.tool_result(tool_call_id="c1", content="contents of x", name="reader"),
            Message.user("second question"),
        ])
        loop._seed_executed_tool_calls(state)
        fresh, dupes = loop._split_duplicate_tool_calls(
            [ToolCall(id="c2", name="reader", arguments={"path": "x"})], state
        )
        assert [c.id for c in fresh] == ["c2"]
        assert dupes == []

    def test_a_retry_within_the_turn_is_still_memoized(self):
        """A verification retry rebuilds state from the previous attempt: that stays."""
        loop = AgentLoop(provider=MockProvider(), tools=[reader])
        state = AgentState(messages=[
            Message.user("do the thing"),
            Message.assistant(tool_calls=[ToolCall(id="c1", name="reader", arguments={"path": "x"})]),
            Message.tool_result(tool_call_id="c1", content="contents of x", name="reader"),
        ])
        loop._seed_executed_tool_calls(state)
        fresh, dupes = loop._split_duplicate_tool_calls(
            [ToolCall(id="c2", name="reader", arguments={"path": "x"})], state
        )
        assert fresh == []
        assert [c.id for c in dupes] == ["c2"]

    @pytest.mark.asyncio
    async def test_a_write_invalidates_the_memoized_read(self):
        """read -> write -> same read must re-read, not serve the pre-edit content."""
        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="r1", name="reader", arguments={"path": "x"})]),
            Message.assistant(tool_calls=[
                ToolCall(id="w1", name="writer", arguments={"path": "x", "body": "new"})
            ]),
            Message.assistant(tool_calls=[ToolCall(id="r2", name="reader", arguments={"path": "x"})]),
            Message.assistant(content="all done"),
        ])
        loop = AgentLoop(provider=provider, tools=[reader, writer])
        result = await loop.run([Message.user("edit then verify")])
        reads = [
            m for m in result
            if m.role == "tool"
            and m.name == "reader"
            and not str(m.content).startswith("Duplicate call skipped")
        ]
        assert len(reads) == 2, f"expected a fresh read after the write, got {reads}"


class TestToolCallBudget:
    """#59: one budget, applied on every dispatch path, reset per run."""

    @pytest.mark.asyncio
    async def test_lazy_loaded_tool_respects_a_zero_budget(self):
        from src.sdk.tool_index import ToolIndex, _rebuild_custom_function
        from src.sdk.tools import ToolDefinition

        index = ToolIndex(Path(tempfile.mkdtemp()) / "tool_index")
        try:
            marker = Path(tempfile.mkdtemp()) / "ran.txt"
            td = ToolDefinition(
                name="greeter",
                description="Lazy tool with a real body",
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            )
            reconstruct = {"command": f'touch "{marker}"', "install": []}
            td = _rebuild_custom_function(td, reconstruct)
            index.index_tool(td, tool_type="custom", reconstruct=reconstruct)

            loop = AgentLoop(
                provider=MockProvider(), tools=[], run_config=RunConfig(max_tool_calls=0)
            )
            loop._tool_index = index
            result = await loop._execute_tool(
                ToolCall(id="c1", name="greeter", arguments={"name": "World"})
            )
            assert not marker.exists(), "a lazy tool body ran with an exhausted budget"
            assert result.is_error
            assert "tool_budget_exceeded" in str(result.content)
        finally:
            index.close()

    @pytest.mark.asyncio
    async def test_each_streamed_run_gets_its_own_budget(self):
        provider = MockProvider()
        call_round = [
            StreamChunk.tool_input_start(tool="reader", call_id="a"),
            StreamChunk.tool_input_delta(call_id="a", content='{"path": "x"}'),
            StreamChunk.tool_input_end(tool="reader", call_id="a"),
            StreamChunk.done(content=""),
        ]
        text_round = [StreamChunk.text_delta(content="done"), StreamChunk.done(content="done")]
        provider.set_stream_events([call_round, text_round, call_round, text_round])
        loop = AgentLoop(
            provider=provider, tools=[reader], run_config=RunConfig(max_tool_calls=1)
        )
        first = [c async for c in loop.run_stream([Message.user("read x")])]
        second = [c async for c in loop.run_stream([Message.user("read x again")])]
        assert any(c.canonical_type == "tool_result" for c in first)
        wire = json.dumps([c.model_dump() for c in second], default=str)
        assert "tool_budget_exceeded" not in wire, (
            "the second run inherited the first run's exhausted budget"
        )
        assert any(c.canonical_type == "tool_result" for c in second)


class TestDuplicateReceiptsArePerCall:
    """#61: each skipped id must be answered from ITS OWN earlier result."""

    @pytest.mark.asyncio
    async def test_each_duplicate_reports_its_own_arguments_result(self):
        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="r1", name="reader", arguments={"path": "x"})]),
            Message.assistant(tool_calls=[ToolCall(id="o1", name="other", arguments={"value": "v"})]),
            Message.assistant(tool_calls=[
                ToolCall(id="r2", name="reader", arguments={"path": "x"}),
                ToolCall(id="o2", name="other", arguments={"value": "v"}),
            ]),
            Message.assistant(content="both already known"),
        ])
        loop = AgentLoop(provider=provider, tools=[reader, other])
        result = await loop.run([Message.user("check both")])
        synthetic = {
            m.tool_call_id: str(m.content)
            for m in result
            if m.role == "tool" and str(m.content).startswith("Duplicate call skipped")
        }
        assert set(synthetic) == {"r2", "o2"}
        assert "contents of x" in synthetic["r2"]
        assert "other saw v" in synthetic["o2"]
        assert "other saw v" not in synthetic["r2"]
