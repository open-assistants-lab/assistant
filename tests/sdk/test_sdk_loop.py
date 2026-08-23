"""Agent loop tests — verify the ReAct loop, middleware hooks, HITL, streaming, and error handling.

These tests use a MockProvider that returns predetermined responses,
so they run without any real LLM service.
"""

import asyncio
import json
import time

import pytest

from src.sdk.compression import (
    CompressionArtifact,
    CompressionMessage,
    CompressionReason,
    CompressionResult,
    CompressionStatus,
    CompressionTelemetry,
)
from src.sdk.guardrails import GuardrailResult, InputGuardrail
from src.sdk.loop import AgentLoop, CostTracker, RunConfig
from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.middleware import Middleware
from src.sdk.middleware_summarization import SummarizationMiddleware
from src.sdk.providers.base import (
    LLMProvider,
    ModelCost,
    ModelInfo,
    ProviderContextOverflowError,
)
from src.sdk.run_models import ContextSnapshot, ContextSource
from src.sdk.state import AgentState
from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolResult, tool


class MockProvider(LLMProvider):
    """Predictable mock provider for testing the agent loop."""

    def __init__(self, responses: list[Message] | None = None):
        self.responses = responses or []
        self._call_count = 0
        self._last_messages: list[Message] | None = None
        self._last_tools: list[ToolDefinition] | None = None
        self._last_kwargs: dict | None = None
        self._stream_events: list[list[StreamChunk]] = []

    def set_responses(self, responses: list[Message]) -> None:
        self.responses = responses
        self._call_count = 0

    def set_stream_events(self, event_batches: list[list[StreamChunk]]) -> None:
        self._stream_events = event_batches

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> Message:
        self._last_messages = messages
        self._last_tools = tools
        self._last_kwargs = kwargs
        if self._call_count < len(self.responses):
            response = self.responses[self._call_count]
            self._call_count += 1
            return response
        return Message.assistant(content="No more responses")

    async def chat_stream_impl(self, messages, tools, model, **kwargs):
        if self._stream_events:
            idx = min(self._call_count, len(self._stream_events) - 1)
            for chunk in self._stream_events[idx]:
                yield chunk
            self._call_count += 1
        elif self.responses:
            idx = min(self._call_count, len(self.responses) - 1)
            resp = self.responses[idx]
            self._call_count += 1
            if resp.content:
                yield StreamChunk.text_delta(
                    content=resp.content if isinstance(resp.content, str) else ""
                )
            if resp.tool_calls:
                for tc in resp.tool_calls:
                    yield StreamChunk.tool_input_start(tool=tc.name, call_id=tc.id, args=tc.arguments)
                    yield StreamChunk.tool_input_end(tool=tc.name, call_id=tc.id)
            yield StreamChunk.done(content=resp.content if isinstance(resp.content, str) else "")
        else:
            yield StreamChunk.done(content="")

    def chat_stream(self, messages, tools=None, model=None, **kwargs):
        return self.chat_stream_impl(messages, tools, model, **kwargs)

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        return ModelInfo(id=model, name=model, provider_id="mock")

    @property
    def provider_id(self) -> str:
        return "mock"


def _measured_snapshot(**kwargs) -> ContextSnapshot:
    messages = kwargs["messages"]
    tools = kwargs["tools"] or []
    estimated_tokens = sum(len(str(message.content)) for message in messages) + len(tools) * 100
    return ContextSnapshot(
        model=kwargs["model"],
        attempt=kwargs["attempt"],
        llm_call_index=kwargs["llm_call_index"],
        estimated_tokens=estimated_tokens,
        context_window=10_000,
        percentage=estimated_tokens / 100,
        source=kwargs["source"],
        freshness=kwargs["freshness"],
        estimated=True,
    )


def _compression_result(context, status=CompressionStatus.SUCCEEDED) -> CompressionResult:
    if status is not CompressionStatus.SUCCEEDED:
        return CompressionResult(
            telemetry=CompressionTelemetry(
                status=status,
                reason=context.reason,
                summary_model=context.model,
                persistence={"status": "not_requested"},
                error_code="not_compressed",
                before_context=context.before,
            )
        )
    replacement = (CompressionMessage.from_message(Message.user("compressed summary")),)
    artifact = CompressionArtifact(
        summary="compressed summary",
        replacement_messages=replacement,
        summarized_message_count=1,
        preserved_message_count=0,
        summarized_message_ids=("old-1",),
        persistence_eligible=True,
    )
    return CompressionResult(
        artifact=artifact,
        telemetry=CompressionTelemetry(
            status=status,
            reason=context.reason,
            before_message_count=1,
            after_message_count=1,
            before_token_count=10,
            after_token_count=5,
            summarized_message_count=1,
            replacement_message_count=1,
            summary_model=context.model,
            persistence={"status": "not_requested"},
            before_context=context.before,
        ),
    )


class ScriptedCompressionMiddleware(SummarizationMiddleware):
    def __init__(self, *, automatic=False, status=CompressionStatus.SUCCEEDED):
        super().__init__("mock:test")
        self.automatic = automatic
        self.status = status
        self.contexts = []

    async def force_summarize(self, state, context, instructions=None):
        self.contexts.append(context)
        result = _compression_result(context, self.status)
        if result.compressed and result.artifact:
            state.messages = [message.to_message() for message in result.artifact.replacement_messages]
            state.extra["_compression_result"] = result
        return result

    async def abefore_model(self, state):
        if not self.automatic:
            return None
        context = state.extra["_compression_context"]
        result = await self.force_summarize(state, context)
        return {"extra": {"_compression_result": result}}


class OverflowProvider(MockProvider):
    def __init__(self, *, failures: int, stream: bool = False):
        super().__init__([Message.assistant("recovered")])
        self.failures = failures
        self.stream_mode = stream
        self.attempts = 0

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ProviderContextOverflowError("too large")
        return await super().chat(messages, tools, model, **kwargs)

    async def chat_stream_impl(self, messages, tools, model, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ProviderContextOverflowError("too large")
        yield StreamChunk.text_delta(content="recovered")
        yield StreamChunk.usage_event(Usage(input_tokens=7, output_tokens=2))
        yield StreamChunk.done(content="recovered")


@tool
def echo(text: str = "hello") -> str:
    """Echo the input text."""
    return text


@tool
def context_probe(user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Return the execution context ids."""
    return json.dumps({"user_id": user_id, "workspace_id": workspace_id})


@tool
def destructive_context_probe(
    user_id: str = "default_user", workspace_id: str = "personal"
) -> str:
    """Destructive context probe."""
    return json.dumps({"user_id": user_id, "workspace_id": workspace_id})


destructive_context_probe.annotations = ToolAnnotations(destructive=True)


@tool
def add(a: int = 0, b: int = 0) -> str:
    """Add two numbers."""
    return str(a + b)


@tool
def fail_always(msg: str = "error") -> str:
    """Always raises an error."""
    raise ValueError(msg)


_call_log: list[str] = []


@tool
def slow_read(query: str = "x") -> str:
    """A slow read-only tool (simulates latency)."""
    _call_log.append(f"slow_read:{query}")
    time.sleep(0.1)
    return f"result:{query}"


slow_read.annotations = ToolAnnotations(read_only=True)


@tool
def destructive_write(path: str = "/tmp/x", content: str = "") -> str:
    """A destructive write tool."""
    _call_log.append(f"destructive_write:{path}")
    return f"wrote:{path}"


destructive_write.annotations = ToolAnnotations(destructive=True)


@tool
def stateful_action(action: str = "") -> str:
    """A stateful but non-destructive tool (neither read_only nor destructive)."""
    return f"action:{action}"


class TestAgentLoopBasic:
    """Basic agent loop behavior."""

    async def test_simple_response_no_tools(self):
        """Agent returns final message when LLM responds without tool calls."""
        provider = MockProvider(responses=[Message.assistant(content="Hello!")])
        loop = AgentLoop(provider=provider, tools=[])
        result = await loop.run([Message.user("Hi")])

        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"
        assert result[1].content == "Hello!"

    async def test_single_tool_call_and_result(self):
        """Agent calls a tool, gets result, then responds."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="call_1", name="echo", arguments={"text": "test"}),
                    ],
                ),
                Message.assistant(content="You said test"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run([Message.user("Say hello")])

        user_msg = [m for m in result if m.role == "user"]
        tool_res = [m for m in result if m.role == "tool"]
        asst = [m for m in result if m.role == "assistant"]

        assert len(user_msg) >= 1
        assert len(asst) == 2
        assert tool_res[0].tool_call_id == "call_1"
        assert "test" in tool_res[0].content

    async def test_max_iterations(self):
        """Agent stops after max iterations even if LLM keeps calling tools."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id=f"call_{i}", name="echo", arguments={"text": f"iter_{i}"})
                    ],
                )
                for i in range(30)
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo], max_iterations=3)
        result = await loop.run([Message.user("Keep going")])

        assistant_msgs = [m for m in result if m.role == "assistant"]
        assert len(assistant_msgs) <= 3

    async def test_no_tool_calls_exits_immediately(self):
        """Agent exits on first response with no tool calls."""
        provider = MockProvider(
            responses=[
                Message.assistant(content="I'm done."),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run([Message.user("Hello")])

        assert len(result) == 2
        assert result[-1].content == "I'm done."

    async def test_system_prompt_injected(self):
        """System prompt is prepended if not already present."""
        provider = MockProvider(
            responses=[
                Message.assistant(content="OK"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[], system_prompt="You are a bot.")
        await loop.run([Message.user("Hi")])

        assert provider._last_messages is not None
        assert provider._last_messages[0].role == "system"
        assert provider._last_messages[0].content == "You are a bot."

    async def test_system_prompt_not_duplicated(self):
        """System prompt is not duplicated if already present."""
        provider = MockProvider(responses=[Message.assistant(content="OK")])
        loop = AgentLoop(provider=provider, tools=[], system_prompt="You are a bot.")
        await loop.run([Message.system("You are a bot."), Message.user("Hi")])

        system_msgs = [m for m in provider._last_messages if m.role == "system"]
        assert len(system_msgs) == 1

    async def test_unknown_tool_returns_error(self):
        """Unknown tool call returns JSON error message."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="nonexistent_tool", arguments={}),
                    ],
                ),
                Message.assistant(content="Tool not found error"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run([Message.user("Call unknown tool")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 1
        error_data = json.loads(tool_res[0].content)
        assert "error" in error_data
        assert "nonexistent_tool" in error_data["error"]

    async def test_tool_error_handled_gracefully(self):
        """Tool that raises an exception returns error JSON, loop continues."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="fail_always", arguments={"msg": "boom"}),
                    ],
                ),
                Message.assistant(content="The tool failed."),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[fail_always])
        result = await loop.run([Message.user("Use failing tool")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 1
        error_data = json.loads(tool_res[0].content)
        assert "error" in error_data

    async def test_llm_error_handled(self):
        """LLM errors are caught and returned as assistant messages."""

        class FailProvider(MockProvider):
            async def chat(self, messages, tools=None, model=None, **kwargs):
                raise ConnectionError("LLM service unavailable")

        provider = FailProvider()
        loop = AgentLoop(provider=provider, tools=[])
        result = await loop.run([Message.user("Hello")])

        assert len(result) == 2
        assert "Error" in result[-1].content

    async def test_multiple_tool_calls_in_one_response(self):
        """Agent handles multiple tool calls in a single LLM response."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "a"}),
                        ToolCall(id="c2", name="add", arguments={"a": 1, "b": 2}),
                    ],
                ),
                Message.assistant(content="Results: a and 3"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo, add])
        result = await loop.run([Message.user("Multi-tool")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 2

    async def test_chained_tool_calls(self):
        """Agent can chain multiple LLM turns with tool calls."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="add", arguments={"a": 1, "b": 2}),
                    ],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c2", name="echo", arguments={"text": "3"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[add, echo])
        result = await loop.run([Message.user("Chain")])

        assistant_msgs = [m for m in result if m.role == "assistant"]
        assert len(assistant_msgs) == 3

    async def test_tool_registry_dedup(self):
        """Duplicate tools are rejected."""
        from src.sdk.tools import ToolRegistry

        reg = ToolRegistry()
        reg.register(echo)
        with pytest.raises(ValueError):
            reg.register(echo)


class TestAgentLoopStreaming:
    """Streaming agent loop behavior."""

    async def test_stream_simple_response(self):
        """run_stream yields ai_token then done for simple response."""
        provider = MockProvider(responses=[Message.assistant(content="Hi there")])
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content="Hi "),
                    StreamChunk.text_delta(content="there"),
                    StreamChunk.done(content="Hi there"),
                ]
            ]
        )
        loop = AgentLoop(provider=provider, tools=[])
        chunks = []
        async for chunk in loop.run_stream([Message.user("Hello")]):
            chunks.append(chunk)

        types = [c.type for c in chunks]
        assert "text_delta" in types
        assert "done" in types

    async def test_stream_with_tool_calls(self):
        """run_stream yields tool_start, tool_end for tool calls."""
        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content=""),
                    StreamChunk.tool_input_start(tool="echo", call_id="c1", args={"text": "hi"}),
                    StreamChunk.tool_input_end(tool="echo", call_id="c1"),
                    StreamChunk.done(content=""),
                ],
                [
                    StreamChunk.done(content="Final answer"),
                ],
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        chunks = []
        async for chunk in loop.run_stream([Message.user("Use tool")]):
            chunks.append(chunk)

        types = [c.type for c in chunks]
        assert "done" in types

    async def test_stream_tool_name_can_arrive_on_end(self):
        """OpenAI-compatible streams may emit tool name after the start chunk."""
        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.tool_input_start(tool="", call_id="c1"),
                    StreamChunk.tool_input_delta(call_id="c1", content='{"text": "hi"}'),
                    StreamChunk.tool_input_end(tool="echo", call_id="c1"),
                    StreamChunk.done(content=""),
                ],
                [
                    StreamChunk.text_delta(content="Final answer"),
                    StreamChunk.done(content="Final answer"),
                ],
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        chunks = []

        async for chunk in loop.run_stream([Message.user("Use tool")]):
            chunks.append(chunk)

        tool_results = [c for c in chunks if c.canonical_type == "tool_result"]
        assert tool_results
        assert tool_results[0].tool == "echo"
        assert "hi" in (tool_results[0].result_preview or "")
        done = [c for c in chunks if c.type == "done"][-1]
        assert done.tool_calls == [{"name": "echo", "call_id": "c1"}]


class TestAgentLoopMiddleware:
    """Middleware hook execution order and state updates."""

    async def test_middleware_hooks_fire_in_order(self):
        """Hooks fire: before_agent → before_model → after_model → after_agent."""
        call_order = []

        class TracingMiddleware(Middleware):
            def before_agent(self, state):
                call_order.append("before_agent")
                return None

            def after_agent(self, state):
                call_order.append("after_agent")
                return None

            def before_model(self, state):
                call_order.append("before_model")
                return None

            def after_model(self, state):
                call_order.append("after_model")
                return None

        provider = MockProvider(responses=[Message.assistant(content="Done")])
        loop = AgentLoop(provider=provider, tools=[], middlewares=[TracingMiddleware()])
        await loop.run([Message.user("Hi")])

        assert "before_agent" in call_order
        assert "after_agent" in call_order
        assert "before_model" in call_order
        assert "after_model" in call_order
        assert call_order.index("before_agent") < call_order.index("before_model")
        assert call_order.index("after_model") < call_order.index("after_agent")

    async def test_middleware_updates_state(self):
        """Middleware can add data to state.extra."""

        class CounterMiddleware(Middleware):
            def before_agent(self, state):
                return {"turn_count": 0}

            def after_model(self, state):
                count = state.get("turn_count", 0)
                return {"turn_count": count + 1}

        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "x"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo], middlewares=[CounterMiddleware()])
        result = await loop.run([Message.user("Go")])
        assert result is not None

    async def test_async_middleware_hooks(self):
        """Async hooks (abefore_*) work alongside sync hooks."""

        class AsyncMiddleware(Middleware):
            async def abefore_model(self, state):
                state.set("async_ran", True)
                return None

        provider = MockProvider(responses=[Message.assistant(content="OK")])
        loop = AgentLoop(provider=provider, tools=[], middlewares=[AsyncMiddleware()])
        await loop.run([Message.user("Hi")])

    async def test_middleware_wrap_tool_call(self):
        """wrap_tool_call can modify tool arguments."""

        class AuthMiddleware(Middleware):
            def wrap_tool_call(self, tool_name, tool_input):
                if tool_name == "echo":
                    tool_input["text"] = f"auth:{tool_input.get('text', '')}"
                return tool_input

        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "hello"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo], middlewares=[AuthMiddleware()])
        result = await loop.run([Message.user("Say hello")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 1
        assert "auth:hello" in tool_res[0].content

    async def test_middleware_error_does_not_crash_loop(self):
        """A middleware hook error is logged but does not crash the loop."""

        class BrokenMiddleware(Middleware):
            def before_model(self, state):
                raise RuntimeError("Middleware bug")

        provider = MockProvider(responses=[Message.assistant(content="OK")])
        loop = AgentLoop(provider=provider, tools=[], middlewares=[BrokenMiddleware()])
        result = await loop.run([Message.user("Hi")])
        assert len(result) >= 2


class TestAgentLoopHITL:
    """Human-in-the-loop interrupt handling.

    Interrupts are triggered by ToolAnnotations.destructive=True (not read_only).
    Both run() and run_stream() yield interrupt chunks — never raise Interrupt.
    """

    async def test_interrupt_on_destructive_tool_run(self):
        """run() yields messages with interrupt info when a destructive tool is called."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="files_delete", arguments={"path": "/important"}),
                    ],
                ),
            ]
        )
        destructive_delete = ToolDefinition(
            name="files_delete",
            description="Delete a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            annotations=ToolAnnotations(destructive=True),
            function=lambda **kw: "deleted",
        )
        loop = AgentLoop(provider=provider, tools=[destructive_delete])

        result = await loop.run([Message.user("Delete file")])

        assert len(result) >= 2

    async def test_interrupt_args_use_runtime_context_ids(self):
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="destructive_context_probe",
                            arguments={
                                "user_id": "default_user",
                                "workspace_id": "personal",
                            },
                        ),
                    ],
                ),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            tools=[destructive_context_probe],
            user_id="real_user",
            workspace_id="test12345",
        )

        result = await loop.run([Message.user("Create subagent")])

        # HITL disabled: the tool executes with the runtime context injected.
        tool_result = next(m for m in result if m.role == "tool")
        content = json.loads(tool_result.content)
        assert content == {"user_id": "real_user", "workspace_id": "test12345"}

    async def test_no_interrupt_on_destructive_tool_stream(self):
        """HITL disabled: a destructive tool executes; no interrupt chunk."""
        provider = MockProvider()
        provider.set_stream_events(
            [
                [
                    StreamChunk.text_delta(content=""),
                    StreamChunk.tool_input_start(tool="files_delete", call_id="c1", args={"path": "/x"}),
                    StreamChunk.interrupt(tool="files_delete", call_id="c1", args={"path": "/x"}),
                    StreamChunk.done(content=""),
                ],
            ]
        )
        destructive_delete = ToolDefinition(
            name="files_delete",
            description="Delete a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            annotations=ToolAnnotations(destructive=True),
            function=lambda **kw: "deleted",
        )
        loop = AgentLoop(provider=provider, tools=[destructive_delete])
        chunks = []
        async for chunk in loop.run_stream([Message.user("Delete")]):
            chunks.append(chunk)

        interrupt_chunks = [c for c in chunks if c.type == "interrupt"]
        assert len(interrupt_chunks) == 0
        tool_results = [c for c in chunks if c.type == "tool_result"]
        assert len(tool_results) >= 1

    async def test_no_interrupt_for_safe_tools(self):
        """Tools without destructive=True execute normally."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "safe"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])

        result = await loop.run([Message.user("Echo safe")])
        assert len(result) >= 3

    async def test_no_interrupt_on_destructive_readonly(self):
        """destructive=True but read_only=True should NOT interrupt (read_only wins)."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="audit_log", arguments={"path": "/log"}),
                    ],
                ),
                Message.assistant(content="Audit complete"),
            ]
        )
        audit = ToolDefinition(
            name="audit_log",
            description="Audit read-only log",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            annotations=ToolAnnotations(destructive=True, read_only=True),
            function=lambda **kw: "audited",
        )
        loop = AgentLoop(provider=provider, tools=[audit])
        result = await loop.run([Message.user("Audit log")])
        assert len(result) >= 3

    def test_should_interrupt_disabled_for_ship(self):
        """HITL is disabled for ship: destructive tools never interrupt,
        regardless of approval state."""
        destructive_delete = ToolDefinition(
            name="files_delete",
            description="Delete a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            annotations=ToolAnnotations(destructive=True),
            function=lambda **kw: "deleted",
        )
        loop = AgentLoop(provider=MockProvider(), tools=[destructive_delete])
        original = ToolCall(id="call-1", name="files_delete", arguments={"path": "/one"})
        retry = ToolCall(id="call-2", name="files_delete", arguments={"path": "/one"})

        loop.approve_tool_call(original)

        assert loop._should_interrupt(original) is False
        assert loop._should_interrupt(retry) is False


class TestAgentLoopRunSingle:
    """Single LLM call (no tool loop)."""

    async def test_run_single_returns_assistant_message(self):
        """run_single makes one LLM call and returns the response."""
        provider = MockProvider(responses=[Message.assistant(content="Summary")])
        loop = AgentLoop(provider=provider, tools=[], system_prompt="Summarize.")
        result = await loop.run_single([Message.user("Long text...")])

        assert result.role == "assistant"
        assert "Summary" in result.content

    async def test_run_single_no_tool_execution(self):
        """run_single does not execute tool calls even if present in response."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "should not run"}),
                    ],
                ),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run_single([Message.user("Hello")])

        assert result.role == "assistant"

    async def test_run_single_with_system_prompt(self):
        """System prompt is prepended in run_single."""
        provider = MockProvider(responses=[Message.assistant(content="OK")])
        loop = AgentLoop(provider=provider, tools=[], system_prompt="You are a summarizer.")
        await loop.run_single([Message.user("Summarize this")])

        assert provider._last_messages is not None
        assert provider._last_messages[0].role == "system"


class TestAgentState:
    """AgentState data operations."""

    def test_state_initialization(self):
        state = AgentState(messages=[Message.user("hello")])
        assert state.message_count() == 1
        assert state.last_message().content == "hello"

    def test_state_get_set_extra(self):
        state = AgentState()
        state.set("key", "value")
        assert state.get("key") == "value"
        assert state.get("missing", "default") == "default"

    def test_state_update_messages(self):
        state = AgentState()
        msgs = [Message.user("test")]
        state.update({"messages": msgs})
        assert len(state.messages) == 1

    def test_state_from_dict(self):
        data = {
            "messages": [{"role": "user", "content": "hi"}],
            "extra": {"key": "val"},
        }
        state = AgentState.from_dict(data)
        assert state.message_count() == 1
        assert state.get("key") == "val"

    def test_state_user_assistant_tool_messages(self):
        state = AgentState(
            messages=[
                Message.system("sys"),
                Message.user("hi"),
                Message.assistant("hello"),
                Message.tool_result("c1", "result"),
            ]
        )
        assert len(state.user_messages()) == 1
        assert len(state.assistant_messages()) == 1
        assert len(state.tool_results()) == 1
        assert state.system_message() is not None


class TestMiddlewareBase:
    """Middleware base class behavior."""

    def test_default_hooks_return_none(self):
        mw = Middleware.__new__(Middleware)
        state = AgentState()
        assert mw.before_agent(state) is None
        assert mw.after_agent(state) is None
        assert mw.before_model(state) is None
        assert mw.after_model(state) is None

    async def test_default_async_hooks_delegate_to_sync(self):
        mw = Middleware.__new__(Middleware)
        state = AgentState()
        assert await mw.abefore_agent(state) is None
        assert await mw.aafter_agent(state) is None
        assert await mw.abefore_model(state) is None
        assert await mw.aafter_model(state) is None

    def test_wrap_tool_call_passthrough(self):
        mw = Middleware.__new__(Middleware)
        args = {"text": "hello"}
        assert mw.wrap_tool_call("echo", args) == args

    def test_name_property(self):
        class MyMiddleware(Middleware):
            pass

        mw = MyMiddleware()
        assert mw.name == "MyMiddleware"


class TestStreamChunk:
    """StreamChunk factory methods and WS message conversion."""

    def test_ai_token_factory(self):
        chunk = StreamChunk.ai_token(content="Hi")
        assert chunk.type == "ai_token"
        assert chunk.content == "Hi"

    def test_tool_start_factory(self):
        chunk = StreamChunk.tool_start(tool="echo", call_id="c1", args={"text": "x"})
        assert chunk.type == "tool_start"
        assert chunk.tool == "echo"
        assert chunk.call_id == "c1"

    def test_tool_end_factory(self):
        chunk = StreamChunk.tool_end(tool="echo", call_id="c1", result_preview="x")
        assert chunk.type == "tool_end"
        assert chunk.result_preview == "x"

    def test_interrupt_factory(self):
        chunk = StreamChunk.interrupt(tool="files_delete", call_id="c1", args={"path": "/x"})
        assert chunk.type == "interrupt"
        assert chunk.tool == "files_delete"

    def test_reasoning_factory(self):
        chunk = StreamChunk.reasoning(content="thinking...")
        assert chunk.type == "reasoning"

    def test_done_factory(self):
        chunk = StreamChunk.done(content="Final", tool_calls=[{"name": "echo"}])
        assert chunk.type == "done"
        assert chunk.content == "Final"
        assert len(chunk.tool_calls) == 1

    def test_error_factory(self):
        chunk = StreamChunk.error(message="Failed")
        assert chunk.type == "error"
        assert chunk.content == "Failed"


class TestToolDefinition:
    """ToolDefinition operations used by the loop."""

    def test_tool_decorator(self):
        assert echo.name == "echo"
        assert echo.description == "Echo the input text."
        assert "text" in echo.parameters["properties"]

    def test_tool_invoke(self):
        result = echo.invoke({"text": "hello"})
        assert result == "hello"

    async def test_tool_ainvoke(self):
        result = await echo.ainvoke({"text": "async"})
        assert result == "async"

    async def test_tool_invoke_with_error(self):
        """Tool errors are caught by AgentLoop._execute_tool and returned as ToolResult."""
        loop = AgentLoop(provider=MockProvider(), tools=[fail_always])
        result = await loop._execute_tool(
            ToolCall(id="c1", name="fail_always", arguments={"msg": "boom"})
        )
        assert result.is_error
        assert "boom" in result.content

    async def test_tool_invoke_returns_tool_result(self):
        """Normal tool returns ToolResult with is_error=False."""
        loop = AgentLoop(provider=MockProvider(), tools=[echo])
        result = await loop._execute_tool(
            ToolCall(id="c1", name="echo", arguments={"text": "hello"})
        )
        assert isinstance(result, ToolResult)
        assert result.content == "hello"
        assert not result.is_error

    async def test_tool_context_ids_override_model_arguments(self):
        """Model-supplied context ids cannot override runtime user/workspace."""
        loop = AgentLoop(
            provider=MockProvider(),
            tools=[context_probe],
            user_id="real_user",
            workspace_id="subagent-test",
        )

        result = await loop._execute_tool(
            ToolCall(
                id="c1",
                name="context_probe",
                arguments={"user_id": "fake_user", "workspace_id": "invalid workspace"},
            )
        )

        assert json.loads(result.content) == {
            "user_id": "real_user",
            "workspace_id": "subagent-test",
        }

    async def test_tool_result_from_raw(self):
        """ToolResult.from_raw wraps strings and passes through ToolResult."""
        wrapped = ToolResult.from_raw("test")
        assert wrapped.content == "test"
        assert not wrapped.is_error

        direct = ToolResult(content="error msg", is_error=True)
        passed = ToolResult.from_raw(direct)
        assert passed is direct

    def test_tool_registry_lookup(self):
        from src.sdk.tools import ToolRegistry

        reg = ToolRegistry()
        reg.register(echo)
        assert reg.get("echo") is not None
        assert reg.get("nonexistent") is None

    def test_tool_openai_format(self):
        fmt = echo.to_openai_format()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "echo"

    def test_tool_anthropic_format(self):
        fmt = echo.to_anthropic_format()
        assert fmt["name"] == "echo"
        assert "input_schema" in fmt


class TestParallelToolExecution:
    """Parallel tool execution in AgentLoop."""

    def test_classify_parallel_safe_readonly(self):
        loop = AgentLoop(provider=MockProvider(), tools=[echo, add, slow_read])
        tc1 = ToolCall(id="c1", name="echo", arguments={"text": "a"})
        tc2 = ToolCall(id="c2", name="add", arguments={"a": 1, "b": 2})
        tc3 = ToolCall(id="c3", name="slow_read", arguments={"query": "q"})

        parallel, sequential, interrupts = loop._classify_tool_calls([tc1, tc2, tc3])
        assert len(parallel) == 3
        assert len(sequential) == 0
        assert len(interrupts) == 0

    def test_classify_destructive_sequential(self):
        """HITL disabled: a destructive write is sequential (executed one at
        a time, no approval interrupt)."""
        loop = AgentLoop(provider=MockProvider(), tools=[echo, destructive_write])
        tc1 = ToolCall(id="c1", name="echo", arguments={"text": "a"})
        tc2 = ToolCall(id="c2", name="destructive_write", arguments={"path": "/x"})

        parallel, sequential, interrupts = loop._classify_tool_calls([tc1, tc2])
        assert len(parallel) == 1
        assert parallel[0].name == "echo"
        assert len(sequential) == 1
        assert sequential[0].name == "destructive_write"
        assert len(interrupts) == 0

    def test_classify_no_interrupts_hitl_disabled(self):
        loop = AgentLoop(provider=MockProvider(), tools=[destructive_write])
        tc1 = ToolCall(id="c1", name="destructive_write", arguments={"path": "/x"})

        parallel, sequential, interrupts = loop._classify_tool_calls([tc1])
        assert len(parallel) == 0
        assert len(sequential) == 1
        assert len(interrupts) == 0

    def test_classify_mixed(self):
        """Mixed: read-only goes parallel, stateful goes parallel, destructive goes sequential or interrupt."""
        loop = AgentLoop(
            provider=MockProvider(),
            tools=[echo, add, destructive_write, slow_read, stateful_action],
        )
        tc1 = ToolCall(id="c1", name="echo", arguments={"text": "a"})
        tc2 = ToolCall(id="c2", name="destructive_write", arguments={"path": "/x"})
        tc3 = ToolCall(id="c3", name="add", arguments={"a": 1, "b": 2})
        tc4 = ToolCall(id="c4", name="stateful_action", arguments={"action": "test"})

        parallel, sequential, interrupts = loop._classify_tool_calls([tc1, tc2, tc3, tc4])
        assert len(parallel) == 3  # echo, add, stateful_action
        assert len(sequential) == 1  # destructive_write (HITL disabled)
        assert len(interrupts) == 0

    async def test_parallel_execution_order_run(self):
        """Parallel-safe tools execute concurrently, destructive sequentially
        (HITL disabled — no interrupts)."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "a"}),
                        ToolCall(id="c2", name="add", arguments={"a": 1, "b": 2}),
                        ToolCall(id="c3", name="destructive_write", arguments={"path": "/x"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo, add, destructive_write])
        result = await loop.run([Message.user("Multi")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 3

        results_by_name = {}
        for m in tool_res:
            results_by_name.setdefault(m.name, []).append(m.content)

        assert "a" in results_by_name["echo"]
        assert "3" in results_by_name["add"]
        assert "wrote:/x" in results_by_name["destructive_write"]

    async def test_parallel_execution_concurrency(self):
        """Multiple read-only tools actually execute concurrently (faster than sequential)."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="slow_read", arguments={"query": "a"}),
                        ToolCall(id="c2", name="slow_read", arguments={"query": "b"}),
                        ToolCall(id="c3", name="slow_read", arguments={"query": "c"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[slow_read])

        start = time.time()
        result = await loop.run([Message.user("Parallel")])
        elapsed = time.time() - start

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 3

        assert elapsed < 0.35, (
            f"Parallel execution should be faster than sequential (took {elapsed:.2f}s)"
        )

    async def test_destructive_with_parallel_safe_batch(self):
        """HITL disabled: destructive + safe tools both execute."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "safe"}),
                        ToolCall(id="c2", name="destructive_write", arguments={"path": "/x"}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo, destructive_write])
        result = await loop.run([Message.user("Interrupt + safe")])

        tool_res = [m for m in result if m.role == "tool"]
        assert len(tool_res) == 2

        safe_result = next(m for m in tool_res if m.name == "echo")
        assert safe_result.content == "safe"

        destructive_result = next(m for m in tool_res if m.name == "destructive_write")
        assert destructive_result.content == "wrote:/x"

    async def test_parallel_execution_streaming(self):
        """Parallel execution works in streaming mode too."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="echo", arguments={"text": "a"}),
                        ToolCall(id="c2", name="add", arguments={"a": 1, "b": 2}),
                    ],
                ),
                Message.assistant(content="Done"),
            ]
        )
        provider.set_stream_events(
            [
                [
                    StreamChunk.tool_input_start(tool="echo", call_id="c1", args={"text": "a"}),
                    StreamChunk.tool_input_end(tool="echo", call_id="c1"),
                    StreamChunk.tool_input_start(tool="add", call_id="c2", args={"a": 1, "b": 2}),
                    StreamChunk.tool_input_end(tool="add", call_id="c2"),
                    StreamChunk.done(content=""),
                ],
                [
                    StreamChunk.text_delta(content="Done"),
                    StreamChunk.done(content="Done"),
                ],
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo, add])

        chunks = []
        async for chunk in loop.run_stream([Message.user("Stream parallel")]):
            chunks.append(chunk)

        tool_result_chunks = [c for c in chunks if c.type == "tool_result"]
        assert len(tool_result_chunks) == 2


class TestUsageTracking:
    """Tests for usage extraction from provider responses and CostTracker integration."""

    async def test_usage_in_run_response(self):
        """CostTracker records usage from provider response."""
        provider = MockProvider(
            responses=[
                Message.assistant(content="Hello!", usage=Usage(input_tokens=10, output_tokens=5)),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[])
        result = await loop.run([Message.user("Hi")])
        assert result[-1].usage is not None
        assert result[-1].usage.input_tokens == 10
        assert result[-1].usage.output_tokens == 5

    async def test_cost_tracker_records_usage_from_run(self):
        """AgentLoop.run() passes usage from response to CostTracker."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "test"})],
                    usage=Usage(input_tokens=50, output_tokens=20),
                ),
                Message.assistant(content="Done", usage=Usage(input_tokens=40, output_tokens=10)),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo], run_config=RunConfig(max_llm_calls=10))
        result = await loop.run([Message.user("Hi")])
        assert len(result) >= 3

    async def test_usage_none_in_response(self):
        """Provider response without usage still works."""
        provider = MockProvider(
            responses=[Message.assistant(content="Hello!")],
        )
        loop = AgentLoop(provider=provider, tools=[])
        result = await loop.run([Message.user("Hi")])
        assert result[-1].usage is None
        assert result[-1].content == "Hello!"

    async def test_streaming_usage_extraction(self):
        """StreamChunk with type='usage' has Usage data attached."""
        usage = Usage(input_tokens=100, output_tokens=50)
        chunk = StreamChunk.usage_event(usage)
        assert chunk.type == "usage"
        assert chunk.usage is not None
        assert chunk.usage.input_tokens == 100
        assert chunk.usage.output_tokens == 50

    async def test_streaming_usage_accumulation(self):
        """Usage chunks from streaming accumulate in CostTracker via CostTracker.add_usage()."""
        from src.sdk.loop import CostTracker

        tracker = CostTracker()
        tracker.add_usage(input_tokens=100, output_tokens=50)
        tracker.add_usage(input_tokens=200, output_tokens=75, reasoning_tokens=10)
        assert tracker.total_input_tokens == 300
        assert tracker.total_output_tokens == 125
        assert tracker.total_reasoning_tokens == 10
        assert tracker.llm_calls == 2

    async def test_cost_tracker_add_usage_with_cost(self):
        """CostTracker correctly computes cost from ModelCost."""
        tracker = CostTracker()
        cost = ModelCost(input=3.0, output=15.0)
        tracker.add_usage(input_tokens=1000, output_tokens=500, cost=cost)
        assert tracker.total_input_tokens == 1000
        assert tracker.total_output_tokens == 500
        assert tracker.total_cost_usd > 0
        assert tracker.llm_calls == 1

    async def test_cost_tracker_add_usage_without_cost(self):
        """CostTracker records tokens without cost model."""
        from src.sdk.loop import CostTracker

        tracker = CostTracker()
        tracker.add_usage(input_tokens=100, output_tokens=50, reasoning_tokens=10)
        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.total_reasoning_tokens == 10
        assert tracker.total_cost_usd == 0.0


class TestProviderOptions:
    """Tests for RunConfig.provider_options wiring."""

    async def test_provider_options_passed_to_provider(self):
        """RunConfig.provider_options is passed through to provider.chat()."""
        provider = MockProvider(responses=[Message.assistant(content="OK")])
        loop = AgentLoop(
            provider=provider,
            tools=[],
            run_config=RunConfig(provider_options={"anthropic": {"thinking": {"type": "enabled"}}}),
        )
        await loop.run([Message.user("Hi")])
        assert provider._last_messages is not None

    async def test_provider_options_default_none(self):
        """RunConfig.provider_options defaults to None."""
        config = RunConfig()
        assert config.provider_options is None

    async def test_provider_options_dict(self):
        """RunConfig.provider_options accepts provider-specific options."""
        config = RunConfig(
            provider_options={
                "anthropic": {"thinking": {"type": "enabled", "budget_tokens": 5000}},
                "openai": {"reasoning_effort": "high"},
            }
        )
        assert config.provider_options is not None
        assert "anthropic" in config.provider_options
        assert "openai" in config.provider_options


class TestContextTelemetry:
    async def test_model_id_is_inferred_and_explicit_id_is_normalized(self):
        provider = MockProvider([Message.assistant("ok")])
        provider.model = "test-model"
        inferred = AgentLoop(provider=provider)
        explicit = AgentLoop(provider=provider, model_id=" openai:gpt-4o ")
        assert inferred.model_id == "mock:test-model"
        assert explicit.model_id == "openai:gpt-4o"

    def test_invalid_explicit_model_id_is_rejected(self):
        with pytest.raises(ValueError, match="canonical"):
            AgentLoop(provider=MockProvider(), model_id="not canonical model")

    async def test_snapshot_uses_pre_hook_and_actual_messages_with_tools(self):
        measured = []

        def measurer(**kwargs):
            measured.append((list(kwargs["messages"]), list(kwargs["tools"] or [])))
            return _measured_snapshot(**kwargs)

        class AddContext(Middleware):
            async def abefore_model(self, state):
                return {"messages": [*state.messages, Message.system("hook context")]}

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]),
            tools=[echo],
            system_prompt="system context",
            middlewares=[AddContext()],
            context_measurer=measurer,
        )
        await loop.run([Message.user("question")])
        assert [message.content for message in measured[0][0]] == ["system context", "question"]
        assert [message.content for message in measured[1][0]] == [
            "system context",
            "question",
            "hook context",
        ]
        assert [tool.name for tool in measured[1][1]] == ["echo"]

    async def test_dynamic_tools_are_measured_on_next_call(self):
        snapshots = []

        class RegisterTool(Middleware):
            calls = 0

            async def abefore_model(self, state):
                self.calls += 1
                if self.calls == 2:
                    loop.register_tool(add)

        provider = MockProvider(
            [
                Message.assistant(tool_calls=[ToolCall(id="one", name="echo", arguments={})]),
                Message.assistant("done"),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            tools=[echo],
            middlewares=[RegisterTool()],
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("go")])
        assert snapshots[1].estimated_tokens - snapshots[0].estimated_tokens >= 100

    async def test_nonstream_react_indexes_and_final_projection(self):
        snapshots = []
        provider = MockProvider(
            [
                Message.assistant(
                    tool_calls=[
                        ToolCall(id="one", name="echo", arguments={}),
                        ToolCall(id="two", name="add", arguments={"a": 1, "b": 2}),
                    ]
                ),
                Message.assistant("final answer"),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            tools=[echo, add],
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("go")])
        assert [snapshot.llm_call_index for snapshot in snapshots] == [1, 2]
        assert loop.last_call_context == snapshots[-1]
        assert loop.next_context.llm_call_index == 2
        assert loop.next_context.source is ContextSource.POST_RUN_PROJECTION

    async def test_stream_matches_nonstream_snapshot_and_attaches_usage(self):
        nonstream_snapshots = []
        stream_snapshots = []
        nonstream = AgentLoop(
            provider=MockProvider([Message.assistant("answer")]),
            model_id="mock:test",
            context_sink=nonstream_snapshots.append,
            context_measurer=_measured_snapshot,
        )
        stream_provider = MockProvider()
        stream_provider.set_stream_events(
            [[StreamChunk.text_delta("answer"), StreamChunk.usage_event(Usage(input_tokens=9))]]
        )
        stream = AgentLoop(
            provider=stream_provider,
            model_id="mock:test",
            context_sink=stream_snapshots.append,
            context_measurer=_measured_snapshot,
        )
        await nonstream.run([Message.user("same")])
        events = [event async for event in stream.run_stream([Message.user("same")])]
        assert stream_snapshots == nonstream_snapshots
        assert stream.state.messages[-1].usage == Usage(input_tokens=9)
        assert all(event.canonical_type != "context_snapshot" for event in events)

    @pytest.mark.parametrize("stream", [False, True])
    async def test_blocked_input_guardrail_has_no_snapshot(self, stream):
        async def block(input_text, state):
            return GuardrailResult(tripwire_triggered=True, message="blocked")

        snapshots = []
        loop = AgentLoop(
            provider=MockProvider([Message.assistant("unused")]),
            input_guardrails=[InputGuardrail(name="block", check=block)],
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        if stream:
            _ = [event async for event in loop.run_stream([Message.user("secret")])]
        else:
            await loop.run([Message.user("secret")])
        assert snapshots == []
        assert loop.last_call_context is None

    async def test_overflow_compresses_between_indexed_calls(self):
        snapshots = []
        telemetry = []
        middleware = ScriptedCompressionMiddleware()
        loop = AgentLoop(
            provider=OverflowProvider(failures=1),
            middlewares=[middleware],
            context_sink=snapshots.append,
            compression_sink=telemetry.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("large")])
        assert [snapshot.llm_call_index for snapshot in snapshots] == [1, 2]
        assert middleware.contexts[0].reason is CompressionReason.PROVIDER_OVERFLOW
        assert middleware.contexts[0].llm_call_index == 2
        assert telemetry[0].before_context.llm_call_index == 2
        assert telemetry[0].after_context.llm_call_index == 2

    async def test_manual_compression_measures_current_prepared_context(self):
        measured = []

        def measurer(**kwargs):
            measured.append(
                ([message.content for message in kwargs["messages"]], kwargs["tools"])
            )
            return _measured_snapshot(**kwargs)

        summary_provider = MockProvider([Message.assistant("current summary")])
        middleware = SummarizationMiddleware(
            "mock:test",
            keep=("messages", 1),
            summary_provider_factory=lambda: summary_provider,
        )
        loop = AgentLoop(
            provider=MockProvider([Message.assistant("initial answer")]),
            tools=[echo],
            middlewares=[middleware],
            context_measurer=measurer,
        )
        await loop.run([Message.user("initial question")])
        loop.state.add_message(
            Message.assistant(
                "checking current state",
                tool_calls=[ToolCall(id="manual-tool", name="echo", arguments={})],
            )
        )
        loop.state.add_message(
            Message.tool_result(
                tool_call_id="manual-tool", content="current tool output", name="echo"
            )
        )
        current_content = [message.content for message in loop._prepare_messages(loop.state)]
        measured.clear()

        result = await loop.compress_context(CompressionReason.MANUAL)

        assert result.compressed
        assert measured[0][0] == current_content
        assert [tool.name for tool in measured[0][1]] == ["echo"]
        assert result.telemetry.before_context.estimated_tokens == sum(
            len(str(content)) for content in current_content
        ) + 100
        assert result.telemetry.after_context == _measured_snapshot(
            model=loop.model_id,
            messages=loop._prepare_messages(loop.state),
            tools=[echo],
            attempt=1,
            llm_call_index=2,
            source=ContextSource.PREPARED_CONTEXT,
            freshness=result.telemetry.before_context.freshness,
        )

    async def test_overflow_is_bounded_to_three_provider_calls(self):
        provider = OverflowProvider(failures=10)
        snapshots = []
        loop = AgentLoop(
            provider=provider,
            middlewares=[ScriptedCompressionMiddleware()],
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        result = await loop.run([Message.user("large")])
        assert provider.attempts == 3
        assert [snapshot.llm_call_index for snapshot in snapshots] == [1, 2, 3]
        assert result[-1].content == "Context too large after summarization attempt."

    async def test_stream_overflow_retries_same_react_iteration(self):
        snapshots = []
        loop = AgentLoop(
            provider=OverflowProvider(failures=1, stream=True),
            middlewares=[ScriptedCompressionMiddleware()],
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        events = [event async for event in loop.run_stream([Message.user("large")])]
        assert [snapshot.llm_call_index for snapshot in snapshots] == [1, 2]
        assert loop.state.messages[-1].usage == Usage(input_tokens=7, output_tokens=2)
        assert events[-1].type == "done"

    async def test_automatic_compression_observer_gets_contexts_and_cleanup(self):
        observed = []
        loop = AgentLoop(
            provider=MockProvider([Message.assistant("done")]),
            middlewares=[ScriptedCompressionMiddleware(automatic=True)],
            compression_sink=observed.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("history")])
        assert observed[0].reason is CompressionReason.THRESHOLD
        assert observed[0].before_context.estimated_tokens == len("history")
        assert observed[0].after_context.estimated_tokens == len("compressed summary")
        assert loop.last_compression == observed[0]
        assert "_compression_context" not in loop.state.extra
        assert "_compression_result" not in loop.state.extra

    @pytest.mark.parametrize(
        "status", [CompressionStatus.SKIPPED, CompressionStatus.FAILED]
    )
    async def test_automatic_non_success_keeps_contract_context_semantics(self, status):
        observed = []
        loop = AgentLoop(
            provider=MockProvider([Message.assistant("done")]),
            middlewares=[ScriptedCompressionMiddleware(automatic=True, status=status)],
            compression_sink=observed.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("history")])
        assert observed[0].status is status
        assert observed[0].before_context.llm_call_index == 1
        assert observed[0].after_context is None

    async def test_post_projection_includes_final_hook_and_excludes_schemas(self):
        class FinalSummary(Middleware):
            async def aafter_agent(self, state):
                return {"messages": [*state.messages, Message.user("final summary")]}

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("final answer")]),
            tools=[echo],
            middlewares=[FinalSummary()],
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("question")])
        assert loop.next_context.estimated_tokens == len("questionfinal answerfinal summary")

    async def test_context_sink_exception_is_nonfatal(self):
        def broken_sink(snapshot):
            raise RuntimeError("sink broke")

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]),
            context_sink=broken_sink,
            context_measurer=_measured_snapshot,
        )
        result = await loop.run([Message.user("hi")])
        assert result[-1].content == "ok"
        assert loop.last_call_context.llm_call_index == 1

    async def test_async_context_sink_is_awaited(self):
        snapshots = []

        async def sink(snapshot):
            await asyncio.sleep(0)
            snapshots.append(snapshot)

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]),
            context_sink=sink,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("hi")])
        assert len(snapshots) == 1

    async def test_measurement_exception_preserves_actual_call_index(self):
        calls = 0

        def broken_measurer(**kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("measurement broke")

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]), context_measurer=broken_measurer
        )
        result = await loop.run([Message.user("hi")])
        assert result[-1].content == "ok"
        assert loop._agent_call_index == 1
        assert loop.last_call_context is None
        assert calls >= 2

    async def test_compression_observer_exception_is_nonfatal(self):
        def broken_observer(telemetry):
            raise RuntimeError("observer broke")

        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]),
            middlewares=[ScriptedCompressionMiddleware(automatic=True)],
            compression_sink=broken_observer,
            context_measurer=_measured_snapshot,
        )
        result = await loop.run([Message.user("hi")])
        assert result[-1].content == "ok"
        assert loop.last_compression.status is CompressionStatus.SUCCEEDED

    async def test_direct_run_resets_prior_telemetry(self):
        middleware = ScriptedCompressionMiddleware(automatic=True)
        provider = MockProvider([Message.assistant("first"), Message.assistant("second")])
        loop = AgentLoop(
            provider=provider, middlewares=[middleware], context_measurer=_measured_snapshot
        )
        await loop.run([Message.user("one")])
        middleware.automatic = False
        await loop.run([Message.user("two")])
        assert loop._agent_call_index == 1
        assert loop.last_call_context.llm_call_index == 1
        assert loop.last_compression is None

    async def test_post_projection_is_not_sent_to_call_sink(self):
        snapshots = []
        loop = AgentLoop(
            provider=MockProvider([Message.assistant("ok")]),
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        await loop.run([Message.user("hi")])
        assert len(snapshots) == 1
        assert snapshots[0].source is ContextSource.PREPARED_CONTEXT
        assert loop.next_context.source is ContextSource.POST_RUN_PROJECTION

    async def test_cancelled_stream_before_provider_has_no_snapshot(self):
        snapshots = []
        cancel = asyncio.Event()
        cancel.set()
        loop = AgentLoop(
            provider=MockProvider(),
            cancel_event=cancel,
            context_sink=snapshots.append,
            context_measurer=_measured_snapshot,
        )
        events = [event async for event in loop.run_stream([Message.user("hi")])]
        assert snapshots == []
        assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_run_stream_aclose_from_different_context_does_not_raise():
    """Regression: run_stream's finally block calls _current_agent_loop.reset(token),
    but when the async generator is torn down (aclose) from a different async
    context (as FastAPI's StreamingResponse does on client disconnect), the
    token was created in a different contextvars.Context and .reset() raises
    ValueError. The teardown must not crash — this is the production SSE
    disconnect path.

    What we can and do guarantee:
      - aclose from a different context completes without raising ValueError.
      - the generator is fully closed afterwards.
    What we deliberately do NOT assert:
      - the originating context's ContextVar state after cross-context
        teardown (Python contextvars cannot reach into a live foreign context
        to mutate it; the originating context is being discarded by the
        caller in the real SSE case).
    """
    from src.sdk.loop import get_current_agent_loop

    provider = MockProvider(responses=[Message.assistant(content="hello")])
    provider.set_stream_events(
        [
            [
                StreamChunk.text_delta(content="hel"),
                StreamChunk.text_delta(content="lo"),
                StreamChunk.done(content="hello"),
            ]
        ]
    )
    loop = AgentLoop(provider=provider, tools=[])

    # Start iterating in the current task, consume one chunk, then suspend.
    gen = loop.run_stream([Message.user("hi")])

    async def consume_one() -> StreamChunk | None:
        async for chunk in gen:
            return chunk
        return None

    first = await consume_one()
    assert first is not None
    # While streaming, the loop is registered as the current agent loop.
    assert get_current_agent_loop() is loop

    # Tear down the generator from a DIFFERENT task context, exactly as
    # FastAPI does when a streaming client disconnects. Before the fix this
    # raised: ValueError: <Token> was created in a different Context, which
    # escaped the SSE handler and corrupted the response.
    async def teardown_in_other_context() -> None:
        await gen.aclose()

    await asyncio.wait_for(
        asyncio.get_event_loop().create_task(teardown_in_other_context()),
        timeout=5.0,
    )

    # The generator must be fully closed (StopAsyncIteration, not more chunks).
    drained: list[StreamChunk] = []
    async for leftover in gen:
        drained.append(leftover)
    assert drained == [], "generator should yield no more chunks after cross-context aclose"


@pytest.mark.asyncio
async def test_run_stream_completes_normally_resets_context_var():
    """Companion: when run_stream completes normally (full iteration), the
    context var must be reset to None afterwards. Guards against the fix
    accidentally leaking the loop reference.
    """
    from src.sdk.loop import _current_agent_loop

    provider = MockProvider(responses=[Message.assistant(content="ok")])
    provider.set_stream_events(
        [[StreamChunk.text_delta(content="ok"), StreamChunk.done(content="ok")]]
    )
    loop = AgentLoop(provider=provider, tools=[])

    async for _ in loop.run_stream([Message.user("Reply exactly: ok")]):
        pass

    assert _current_agent_loop.get() is None


class TestDuplicateToolCallGuard:
    """US-003: soft duplicate-call guard — an already-executed (tool, args)
    pair is never executed again; the model gets a system-message nudge, and
    after K nudges a brief capped final text response is requested."""

    async def test_repeated_tool_call_is_nudged_not_reexecuted(self):
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[
                        ToolCall(id="call_1", name="echo", arguments={"text": "hi"}),
                    ],
                ),
                # The model re-proposes the SAME call (the deepseek regression).
                Message.assistant(
                    content="The current time is 13:43:55 UTC",
                    tool_calls=[
                        ToolCall(id="call_2", name="echo", arguments={"text": "hi"}),
                    ],
                ),
                Message.assistant(content="The result is: hi"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run([Message.user("Say hi")])

        tool_res = [m for m in result if m.role == "tool"]
        # Synthetic duplicate answers don't count as executions (B1).
        real_executions = [m for m in tool_res if not str(m.content).startswith("Duplicate call skipped")]
        assert len(real_executions) == 1, "the duplicate call must NOT be re-executed"
        nudge_msgs = [m for m in result if m.role == "system" and "already called" in str(m.content)]
        assert len(nudge_msgs) == 1
        final = [m for m in result if m.role == "assistant" and m.content == "The result is: hi"]
        assert final, "the final text answer must be used"

    async def test_duplicate_guard_escalates_to_capped_final_response(self):
        """After max_duplicate_tool_nudges nudges, the loop requests one brief
        final text response; tool calls in it are suppressed and its text used."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c3", name="echo", arguments={"text": "x"})],
                ),
                # The K-th re-proposal triggers the final-response escalation.
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c4", name="echo", arguments={"text": "x"})],
                ),
                # The final capped call: still proposes the tool — must be
                # suppressed and the text used.
                Message.assistant(content="brief final", tool_calls=[ToolCall(id="c5", name="echo", arguments={"text": "x"})]),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            tools=[echo],
            run_config=RunConfig(max_duplicate_tool_nudges=2),
        )
        result = await loop.run([Message.user("say")])

        tool_res = [m for m in result if m.role == "tool"]
        # Synthetic duplicate answers don't count as executions (B1).
        real_executions = [m for m in tool_res if not str(m.content).startswith("Duplicate call skipped")]
        assert len(real_executions) == 1, "only the first execution happens"
        finals = [m for m in result if m.role == "assistant" and m.content == "brief final"]
        assert finals, "the final response text must be used"
        # The final call must have been capped via provider options.
        final_kwargs = provider._last_kwargs or {}
        assert final_kwargs.get("provider_options"), "final call must carry capped provider_options"
        assert provider._last_kwargs["provider_options"]["ollama-cloud"]["max_tokens"] == 200

    async def test_streaming_guard_emits_single_tool_input(self):
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(
                    content="partial text",
                    tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(content="final answer"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        chunks = [c async for c in loop.run_stream([Message.user("say")])]
        tool_starts = [c for c in chunks if c.type == "tool_input_start"]
        assert len(tool_starts) == 1
        done = [c for c in chunks if c.type == "done"]
        assert done and "final answer" in str(done[-1].content)

    async def test_rerun_seeds_executed_set_from_previous_attempt(self):
        """The grader re-run rebuilds the state from the previous attempt's
        messages — the guard must seed the executed set from the assistant
        tool_calls already present, so the re-run does not re-execute them."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(content="final"),
            ]
        )
        previous = [
            Message.user("what time is it"),
            Message.assistant(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
            ),
            Message.tool_result(tool_call_id="c1", content="13:00 UTC", name="echo"),
        ]
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run(previous)

        tool_res = [m for m in result if m.role == "tool"]
        # Synthetic duplicate answers don't count as executions (B1).
        real_executions = [m for m in tool_res if not str(m.content).startswith("Duplicate call skipped")]
        assert len(real_executions) == 1, "the re-run must not re-execute the previous attempt's call"
        nudge = [m for m in result if m.role == "system" and "already called" in str(m.content)]
        assert nudge, "the re-run should nudge instead of re-executing"

    async def test_duplicate_nudge_appends_tool_results(self):
        """B1: after a duplicate nudge, every dangling tool_call id must have a
        tool result — strict provider APIs (OpenAI/Anthropic) reject an
        assistant tool_calls block that is never answered."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="call_2", name="echo", arguments={"text": "hi"})],
                ),
                Message.assistant(content="The result is: hi"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        result = await loop.run([Message.user("Say hi")])

        answered = {m.tool_call_id for m in result if m.role == "tool"}
        for msg in result:
            for tc in msg.tool_calls or []:
                assert tc.id in answered, f"dangling tool_call {tc.id} without tool result"

    async def test_duplicate_escalation_appends_tool_results(self):
        """B1: the final-answer escalation branch (nudges >= max) must ALSO
        answer the duplicate tool_calls before requesting the brief final
        response — same provider-rejection hazard as the nudge branch."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(content="brief final"),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            tools=[echo],
            run_config=RunConfig(max_duplicate_tool_nudges=0),
        )
        result = await loop.run([Message.user("say")])

        answered = {m.tool_call_id for m in result if m.role == "tool"}
        for msg in result:
            for tc in msg.tool_calls or []:
                assert tc.id in answered, f"dangling tool_call {tc.id} without tool result"
        finals = [m for m in result if m.role == "assistant" and m.content == "brief final"]
        assert finals, "the escalation must still produce the capped final answer"

    async def test_streaming_duplicate_nudge_emits_tool_result_events(self):
        """B1: the streaming twin must both persist the synthetic tool results
        and yield tool_result events so clients see the duplicate being
        answered."""
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "x"})],
                ),
                Message.assistant(content="final answer"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[echo])
        chunks = [c async for c in loop.run_stream([Message.user("say")])]

        answered = {
            m.tool_call_id
            for m in loop.state.messages
            if m.role == "tool"
        }
        for msg in loop.state.messages:
            for tc in msg.tool_calls or []:
                assert tc.id in answered, f"dangling tool_call {tc.id} without tool result"
        tr_events = [c for c in chunks if c.type == "tool_result" and c.call_id == "c2"]
        assert tr_events, "the duplicate call must be answered on the wire too"
