"""#68 (aborted generation dispatches calls) and #69 (retries bypass max_iterations)."""

import json

import pytest

from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message, StreamChunk
from src.sdk.tools import ToolAnnotations, tool


@tool
async def destructive(text: str = "x") -> str:
    """A write-like tool."""
    return f"wrote {text}"


destructive.annotations = ToolAnnotations(destructive=True, read_only=False)


@tool
async def reader(path: str = "") -> str:
    """Read-only."""
    return f"contents of {path}"


reader.annotations = ToolAnnotations(title="Read", read_only=True, idempotent=True)


class _ScriptedStreamProvider:
    """Emits a scripted chunk list per round, counting model calls."""

    def __init__(self, batches):
        self.batches = batches
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, **kwargs):  # pragma: no cover
        return Message.assistant(content="")

    async def chat_stream_impl(self, messages, tools=None, model=None, **kwargs):
        idx = min(self.calls, len(self.batches) - 1)
        self.calls += 1
        for chunk in self.batches[idx]:
            yield chunk

    def chat_stream(self, messages, tools=None, model=None, **kwargs):
        return self.chat_stream_impl(messages, tools, model, **kwargs)

    def get_model_info(self, model=None):
        from src.sdk.providers.base import ModelInfo

        return ModelInfo(id=model or "mock", provider_id="mock")

    def count_tokens(self, text, model=None):
        return max(1, len(text) // 4)


def _tool_call(call_id: str, name: str = "destructive", args: dict | None = None) -> list[StreamChunk]:
    return [
        StreamChunk.tool_input_start(tool=name, call_id=call_id),
        StreamChunk.tool_input_delta(call_id=call_id, content=json.dumps(args or {})),
        StreamChunk.tool_input_end(tool=name, call_id=call_id),
    ]


class TestAbortedGenerationIsNotDispatched:
    """#68: a round the provider abandoned must not run its tools."""

    @pytest.mark.asyncio
    async def test_provider_error_does_not_execute_an_unfinished_call(self):
        ran: list[str] = []

        @tool
        async def marker(text: str = "x") -> str:
            """Writes."""
            ran.append(text)
            return "wrote"

        provider = _ScriptedStreamProvider([
            [
                StreamChunk.tool_input_start(tool="marker", call_id="a", args={}),
                StreamChunk.tool_input_delta(call_id="a", content="{}"),
                StreamChunk.error(message="Overloaded"),
                StreamChunk.done(content=""),
            ],
        ])
        loop = AgentLoop(provider=provider, tools=[marker])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        assert ran == [], f"an unfinished call ran after a provider error: {ran}"
        assert provider.calls == 1, "the loop kept going after the provider failed"
        assert any(c.canonical_type == "error" for c in chunks)

    @pytest.mark.asyncio
    async def test_truncated_tool_input_is_not_dispatched(self):
        """No error event, but the input block never completed."""
        ran: list[str] = []

        @tool
        async def marker(text: str = "x") -> str:
            """Writes."""
            ran.append(text)
            return "wrote"

        provider = _ScriptedStreamProvider([
            [
                StreamChunk.tool_input_start(tool="marker", call_id="a", args={}),
                StreamChunk.tool_input_delta(call_id="a", content='{"text": "tru'),
                StreamChunk.done(content=""),
            ],
        ])
        loop = AgentLoop(provider=provider, tools=[marker])
        async for _ in loop.run_stream([Message.user("go")]):
            pass
        assert ran == [], f"a truncated tool call executed: {ran}"
        proposed = [tc.id for m in loop.state.messages if m.role == "assistant" for tc in (m.tool_calls or [])]
        answered = [m.tool_call_id for m in loop.state.messages if m.role == "tool" and m.tool_call_id]
        assert sorted(proposed) == sorted(answered), "an incomplete call was recorded unanswered"

    @pytest.mark.asyncio
    async def test_a_complete_round_still_dispatches(self):
        """The control: normal, complete tool input must still execute."""
        ran: list[str] = []

        @tool
        async def marker(text: str = "x") -> str:
            """Writes."""
            ran.append(text)
            return "wrote"

        provider = _ScriptedStreamProvider([
            _tool_call("a", "marker", {"text": "ok"}),
            [StreamChunk.text_delta(content="done"), StreamChunk.done(content="done")],
        ])
        loop = AgentLoop(provider=provider, tools=[marker])
        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        assert ran == ["ok"]
        assert any(c.canonical_type == "done" for c in chunks)


class TestRetriesAreBounded:
    """#69: every model round must consume the iteration budget."""

    @pytest.mark.asyncio
    async def test_duplicate_only_rounds_respect_max_iterations(self):
        provider = _ScriptedStreamProvider(
            [_tool_call("a", "reader") for _ in range(5)]
            + [[StreamChunk.text_delta(content="finished"), StreamChunk.done(content="finished")]]
        )
        loop = AgentLoop(
            provider=provider, tools=[reader], run_config=RunConfig(max_iterations=2)
        )
        async for _ in loop.run_stream([Message.user("go")]):
            pass
        assert provider.calls <= 2, f"{provider.calls} model calls with max_iterations=2"

    @pytest.mark.asyncio
    async def test_repetition_budget_rounds_respect_max_iterations(self):
        """Each round proposes the same tool with a new argument value."""
        batches = [
            _tool_call(str(i), "reader", {"path": str(i)}) for i in range(8)
        ]
        provider = _ScriptedStreamProvider(
            batches + [[StreamChunk.text_delta(content="stop"), StreamChunk.done(content="stop")]]
        )
        loop = AgentLoop(
            provider=provider, tools=[reader], run_config=RunConfig(max_iterations=3)
        )
        async for _ in loop.run_stream([Message.user("go")]):
            pass
        assert provider.calls <= 3, f"{provider.calls} model calls with max_iterations=3"
