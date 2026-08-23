"""Agent loop conformance tests (non-HTTP).

These tests verify agent behavior through direct Python calls,
not through the HTTP API. For API-level tests, see tests/api/test_agent_loop.py.
"""




import asyncio
import copy

import pytest

from src.sdk.guardrails import InputGuardrail
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message, StreamChunk
from src.sdk.middleware import Middleware
from src.sdk.tools import ToolAnnotations, tool
from src.sdk.validation import normalize_tool_schema
from tests.sdk.test_sdk_loop import MockProvider


class TestAgentLoopBasic:
    """Basic agent loop behavior that must be consistent."""

    def test_run_config_defaults(self):
        """AgentLoop RunConfig must have sensible defaults."""
        from src.sdk.loop import RunConfig

        config = RunConfig()
        assert config.max_llm_calls > 0
        assert config.max_iterations > 0

    def test_run_config_custom(self):
        """RunConfig must accept custom limits."""
        from src.sdk.loop import RunConfig

        config = RunConfig(max_llm_calls=10, max_iterations=5)
        assert config.max_llm_calls == 10
        assert config.max_iterations == 5

    def test_agent_loop_constructor(self):
        """AgentLoop must be constructable with provider and tools."""
        from unittest.mock import MagicMock

        from src.sdk.loop import AgentLoop

        # Real providers expose string model + provider_id attributes; the
        # loop infers its canonical model_id from them.
        provider = MagicMock(model="gpt-4o", provider_id="openai")
        loop = AgentLoop(provider=provider, tools=[], system_prompt="test")
        assert loop is not None
        assert loop.model_id == "openai:gpt-4o"


class TestAgentLoopWSProtocol:
    """WebSocket protocol must be self-consistent with HTTP API."""

    def test_ws_protocol_covers_all_event_types(self):
        """WS protocol must define types for all agent events."""
        from src.http.ws_protocol import CLIENT_MESSAGE_TYPES, SERVER_MESSAGE_TYPES

        expected_server_types = {
            "ai_token",
            "tool_start",
            "tool_end",
            "interrupt",
            "done",
            "error",
            "pong",
        }
        expected_client_types = {
            "user_message",
            "approve",
            "reject",
            "edit_and_approve",
            "cancel",
            "ping",
        }

        actual_server = set(SERVER_MESSAGE_TYPES.keys())
        actual_client = set(CLIENT_MESSAGE_TYPES.keys())

        assert expected_server_types.issubset(actual_server), (
            f"Missing: {expected_server_types - actual_server}"
        )
        assert expected_client_types.issubset(actual_client), (
            f"Missing: {expected_client_types - actual_client}"
        )

    def test_interrupt_message_has_allowed_actions(self):
        """Interrupt messages must specify allowed actions for HITL."""
        from src.http.ws_protocol import InterruptMessage

        msg = InterruptMessage(call_id="c1", tool="files_delete", args={"path": "/x"})
        assert "approve" in msg.allowed_actions
        assert "reject" in msg.allowed_actions

    def test_done_message_can_include_tool_calls(self):
        """Done message must be able to include tool call info."""
        from src.http.ws_protocol import DoneMessage

        msg = DoneMessage(
            response="Here are the results", tool_calls=[{"name": "time_get", "call_id": "c1"}]
        )
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["name"] == "time_get"


# --- Task 19 (audit B17): loop robustness micro-fixes ---


def _make_tool(name: str):
    @tool
    def _impl(text: str = "hello") -> str:
        """Echo helper."""
        return f"{name}:{text}"

    _impl.name = name
    return _impl


class TestStreamingBatchBaseException:
    @pytest.mark.asyncio
    async def test_streaming_batch_survives_baseexception_result(self, monkeypatch):
        """A CancelledError returned by gather(return_exceptions=True) must not
        crash the streaming tuple-unpack (audit B17)."""
        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.tool_input_start(tool="t1", call_id="c1", args={}),
                    StreamChunk.tool_input_end(tool="t1", call_id="c1"),
                    StreamChunk.done(content=""),
                    StreamChunk.text_delta(content="done"),
                ]
            ]
        )
        loop = AgentLoop(provider=provider, tools=[_make_tool("t1")])

        class _SimulatedChildCancellation(BaseException):
            pass

        async def _raise_cancelled(tc, state=None):
            raise _SimulatedChildCancellation("simulated child cancellation")

        monkeypatch.setattr(loop, "_execute_tool", _raise_cancelled)

        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        # The BaseException result must surface as an error tool_result,
        # not a TypeError from tuple-unpacking.
        error_results = [
            c for c in chunks if c.type == "tool_result" and "failed" in (c.result_preview or "")
        ]
        assert len(error_results) >= 1  # mock replays rounds; at least the first must be answered


class TestGuardrailTaskHygiene:
    @pytest.mark.asyncio
    async def test_guardrail_task_cancelled_on_early_return(self, monkeypatch):
        """run_stream's early-return paths must not leave the input-guardrail
        task pending forever (audit B17)."""
        started: list[asyncio.Task] = []

        class SlowGuardrail(InputGuardrail):
            def __init__(self):
                super().__init__(name="slow", check=self._check)

            async def _check(self, input_text, state):
                started.append(asyncio.current_task())
                await asyncio.sleep(30)
                return None

        provider = MockProvider(responses=[Message.assistant("hi")])
        cancel = asyncio.Event()
        cancel.set()  # force early return before first LLM call
        loop = AgentLoop(
            provider=provider,
            tools=[],
            input_guardrails=[SlowGuardrail()],
            run_config=RunConfig(max_llm_calls=1),
            cancel_event=cancel,
        )

        created_tasks: list[asyncio.Task] = []
        real_ensure_future = asyncio.ensure_future

        def spying_ensure_future(coro, *a, **k):
            t = real_ensure_future(coro, *a, **k)
            created_tasks.append(t)
            return t

        monkeypatch.setattr(asyncio, "ensure_future", spying_ensure_future)

        chunks = [c async for c in loop.run_stream([Message.user("go")])]
        assert any(c.type == "done" for c in chunks)
        assert created_tasks, "guardrail task was never scheduled"
        guardrail_task = created_tasks[0]
        await asyncio.sleep(0.05)
        assert guardrail_task.done() and guardrail_task.cancelled(), (
            f"guardrail task leaked: done={guardrail_task.done()} "
            f"cancelled={guardrail_task.cancelled()}"
        )


class TestMiddlewareArgParity:
    @pytest.mark.asyncio
    async def test_execute_single_tool_does_not_mutate_history_args(self):
        """_execute_single_tool must not write transformed arguments back into
        the ToolCall embedded in persisted state (batch paths copy first)."""
        received: list[dict] = []

        class InjectingMW(Middleware):
            name = "injector"

            def wrap_tool_call(self, name, arguments):
                return {"injected": True}

        @tool
        def spy(**kwargs) -> str:
            """Record kwargs."""
            received.append(kwargs)
            return "ok"

        # Destructive => classified as sequential => executed via
        # _execute_single_tool (the mutation site under test).
        spy.annotations = ToolAnnotations(title="spy", destructive=True)

        provider = MockProvider(
            responses=[
                Message.assistant(tool_calls=[{"id": "x1", "name": "spy", "arguments": {}}]),
                Message.assistant(content="done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[spy], middlewares=[InjectingMW()])
        await loop.run([Message.user("go")])

        assert received and received[0] == {"injected": True}
        assistant = next(m for m in loop.state.messages if m.role == "assistant" and m.tool_calls)
        assert assistant.tool_calls[0].arguments == {}, (
            f"history mutated: {assistant.tool_calls[0].arguments}"
        )


class TestNormalizeSchemaPurity:
    def test_normalize_tool_schema_does_not_mutate_caller(self):
        schema = {
            "type": "object",
            "properties": {"q": {"type": "string", "default": None}},
            "$defs": {"Item": {"type": "object", "properties": {}}},
            "definitions": {"Legacy": {"type": "object", "properties": {}}},
        }
        snapshot = copy.deepcopy(schema)
        normalize_tool_schema(schema)
        assert schema == snapshot


class TestStreamingFR9IdClear:
    @pytest.mark.asyncio
    async def test_final_answer_clears_persisted_tool_calls(self):
        """FR-9 escalation must clear the PERSISTED assistant message's
        tool_calls, not just the local variable (dangling ids otherwise)."""
        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content="answering"),
                    StreamChunk.tool_input_start(tool="t1", call_id="c9", args={}),
                    StreamChunk.tool_input_end(tool="t1", call_id="c9"),
                    StreamChunk.done(content=""),
                ]
            ]
        )
        loop = AgentLoop(provider=provider, tools=[_make_tool("t1")])
        # Simulate an already-escalated duplicate-guard turn.
        state_seed = Message.user("go")

        async def run():
            gen = loop.run_stream([state_seed])
            first = True
            async for chunk in gen:
                if first and chunk.type == "text_delta":
                    # Escalate before the assistant message is finalized.
                    loop.state.extra["_final_answer_requested"] = True
                    first = False
                yield chunk

        chunks = []
        async for c in run():
            chunks.append(c)

        assistants = [m for m in loop.state.messages if m.role == "assistant"]
        assert assistants, "no assistant message produced"
        assert all(m.tool_calls == [] for m in assistants), (
            f"dangling ids: {[m.tool_calls for m in assistants]}"
        )
