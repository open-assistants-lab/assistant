"""Tests for Phase 5+6 features: structured streaming, annotations, guardrails, handoffs, tracing, RunConfig."""

import asyncio

import pytest

from src.sdk.guardrails import (
    GuardrailResult,
    GuardrailTripwire,
    InputGuardrail,
    OutputGuardrail,
)
from src.sdk.handoffs import Handoff, HandoffInput
from src.sdk.loop import AgentLoop, CostTracker, RunConfig
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.providers.base import LLMProvider, ModelInfo
from src.sdk.state import AgentState
from src.sdk.tools import ToolAnnotations, ToolResult, tool
from src.sdk.tracing import (
    ConsoleTraceProcessor,
    JsonTraceProcessor,
    Span,
    SpanType,
    TraceProvider,
)
from src.sdk.validation import normalize_tool_schema, repair_tool_call

# ─── StreamChunk Block Events ───


class TestStreamChunkBlocks:
    def test_text_start(self):
        chunk = StreamChunk.text_start()
        assert chunk.type == "text_start"

    def test_text_delta(self):
        chunk = StreamChunk.text_delta("hello")
        assert chunk.type == "text_delta"
        assert chunk.content == "hello"

    def test_text_end(self):
        chunk = StreamChunk.text_end()
        assert chunk.type == "text_end"

    def test_tool_input_start(self):
        chunk = StreamChunk.tool_input_start(tool="time_get", call_id="c1", args={"x": 1})
        assert chunk.type == "tool_input_start"
        assert chunk.tool == "time_get"
        assert chunk.call_id == "c1"

    def test_tool_input_delta(self):
        chunk = StreamChunk.tool_input_delta(call_id="c1", content="arg")
        assert chunk.type == "tool_input_delta"

    def test_tool_input_end(self):
        chunk = StreamChunk.tool_input_end(call_id="c1", tool="time_get")
        assert chunk.type == "tool_input_end"

    def test_reasoning_start(self):
        chunk = StreamChunk.reasoning_start()
        assert chunk.type == "reasoning_start"

    def test_reasoning_delta(self):
        chunk = StreamChunk.reasoning_delta("thinking...")
        assert chunk.type == "reasoning_delta"

    def test_reasoning_end(self):
        chunk = StreamChunk.reasoning_end()
        assert chunk.type == "reasoning_end"

    def test_tool_result_event(self):
        chunk = StreamChunk.tool_result_event(tool="time_get", call_id="c1", result_preview="3pm")
        assert chunk.type == "tool_result"
        assert chunk.result_preview == "3pm"

    def test_backward_compat_ai_token(self):
        chunk = StreamChunk.ai_token("hi")
        assert chunk.type == "ai_token"
        assert chunk.canonical_type == "text_delta"

    def test_backward_compat_tool_start(self):
        chunk = StreamChunk.tool_start(tool="x", call_id="c1")
        assert chunk.type == "tool_start"
        assert chunk.canonical_type == "tool_input_start"

    def test_backward_compat_reasoning(self):
        chunk = StreamChunk.reasoning("think")
        assert chunk.type == "reasoning"
        assert chunk.canonical_type == "reasoning_delta"

    def test_done_and_error(self):
        assert StreamChunk.done(content="ok").type == "done"
        assert StreamChunk.error(message="fail").type == "error"


# ─── Message.reasoning ───


class TestMessageReasoning:
    def test_assistant_with_reasoning(self):
        msg = Message.assistant(content="Here's the answer", reasoning="Let me think...")
        assert msg.reasoning == "Let me think..."
        assert msg.content == "Here's the answer"

    def test_to_anthropic_includes_thinking(self):
        msg = Message.assistant(reasoning="I pondered this", content="Answer")
        result = msg.to_anthropic()
        blocks = result["content"]
        types = [b.get("type") for b in blocks]
        assert "thinking" in types
        assert "text" in types

    def test_from_anthropic_thinking_block(self):
        block = {"type": "thinking", "thinking": "Deep thought"}
        msg = Message.from_anthropic_block(block)
        assert msg is not None
        assert msg.reasoning == "Deep thought"

    def test_provider_metadata(self):
        msg = Message.assistant(content="ok", provider_metadata={"anthropic": {"sig": "abc"}})
        assert msg.provider_metadata["anthropic"]["sig"] == "abc"


# ─── ToolAnnotations ───


class TestToolAnnotations:
    def test_defaults(self):
        ann = ToolAnnotations()
        assert ann.read_only is False
        assert ann.destructive is False
        assert ann.idempotent is False
        assert ann.open_world is False
        assert ann.title is None

    def test_with_title(self):
        ann = ToolAnnotations(title="List Emails", read_only=True)
        assert ann.title == "List Emails"
        assert ann.read_only is True


class TestToolDefinitionAnnotations:
    def test_tool_with_annotations(self):
        @tool
        def safe_read(path: str) -> str:
            """Read a file."""
            return "content"

        safe_read.annotations = ToolAnnotations(read_only=True, idempotent=True)
        assert safe_read.annotations.read_only is True

    def test_tool_with_output_schema(self):
        @tool
        def get_data(user_id: str) -> str:
            """Get user data."""
            return "{}"

        get_data.output_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
        }
        assert get_data.output_schema is not None
        assert "properties" in get_data.output_schema


class TestToolResult:
    def test_basic_result(self):
        r = ToolResult(content="3:00 PM")
        assert r.content == "3:00 PM"
        assert r.is_error is False
        assert r.structured_content is None

    def test_structured_result(self):
        r = ToolResult(content="Found 5 items", structured_content={"count": 5})
        assert r.structured_content == {"count": 5}

    def test_error_result(self):
        r = ToolResult(content="File not found", is_error=True)
        assert r.is_error is True

    def test_audience(self):
        r = ToolResult(content="internal", audience=["assistant"])
        assert "assistant" in r.audience


# ─── Validation ───


class TestRepairToolCall:
    def test_valid_json(self):
        assert repair_tool_call('{"key": "value"}') == {"key": "value"}

    def test_trailing_comma(self):
        result = repair_tool_call('{"key": "value",}')
        assert result == {"key": "value"}

    def test_single_quotes(self):
        result = repair_tool_call("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_markdown_code_fence(self):
        result = repair_tool_call('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_returns_empty(self):
        assert repair_tool_call("not json at all") == {}

    def test_empty_string(self):
        assert repair_tool_call("") == {}


class TestNormalizeSchema:
    def test_adds_additional_properties_false(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = normalize_tool_schema(schema)
        assert result.get("additionalProperties") is False

    def test_strips_none_defaults(self):
        schema = {"type": "object", "properties": {"x": {"type": "string", "default": None}}}
        result = normalize_tool_schema(schema)
        assert "default" not in result["properties"]["x"]


# ─── RunConfig ───


class TestRunConfig:
    def test_defaults(self):
        config = RunConfig()
        assert config.max_llm_calls == 50
        assert config.max_iterations == 25
        assert config.max_tokens_total == 1_000_000

    def test_custom(self):
        config = RunConfig(max_llm_calls=10, max_iterations=5)
        assert config.max_llm_calls == 10
        assert config.max_iterations == 5


class TestCostTracker:
    def test_add_usage(self):
        tracker = CostTracker()
        tracker.add_usage(input_tokens=100, output_tokens=50)
        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.llm_calls == 1

    def test_exceeds_limits(self):
        config = RunConfig(max_llm_calls=2)
        tracker = CostTracker()
        tracker.add_usage()
        tracker.add_usage()
        assert tracker.exceeds_limits(config) is not None

    def test_within_limits(self):
        config = RunConfig(max_llm_calls=100)
        tracker = CostTracker()
        tracker.add_usage()
        assert tracker.exceeds_limits(config) is None


# ─── Auto-approval via annotations ───


class MockProvider(LLMProvider):
    def __init__(self, responses=None):
        self._responses = responses or []
        self._idx = 0

    @property
    def provider_id(self):
        return "mock"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return Message.assistant(content="done")

    async def chat_stream(self, messages, tools=None, model=None, **kwargs):
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            yield StreamChunk.ai_token(content=r.content or "")
            if r.tool_calls:
                for tc in r.tool_calls:
                    yield StreamChunk.tool_start(tool=tc.name, call_id=tc.id, args=tc.arguments)
            yield StreamChunk.done()

    def count_tokens(self, text, model=None):
        return max(1, len(text) // 4)

    def get_model_info(self, model):
        return ModelInfo(id=model, name=model, provider_id="mock")


class TestAutoApproval:
    @pytest.mark.asyncio
    async def test_destructive_tool_executes_hitl_disabled(self):
        @tool
        def delete_file(path: str) -> str:
            """Delete a file."""
            return "deleted"

        delete_file.annotations = ToolAnnotations(destructive=True)

        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="delete_file", arguments={"path": "/x"})],
                )
            ]
        )
        loop = AgentLoop(provider=provider, tools=[delete_file], max_iterations=5)
        result = await loop.run([Message.user("delete /x")])
        tool_results = [m for m in result if m.role == "tool"]
        # HITL disabled: the destructive tool executes without approval.
        assert len(tool_results) >= 1
        assert "interrupt" not in tool_results[0].content

    @pytest.mark.asyncio
    async def test_read_only_tool_no_interrupt(self):
        @tool
        def read_file(path: str) -> str:
            """Read a file."""
            return "content"

        read_file.annotations = ToolAnnotations(read_only=True, idempotent=True)

        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "/x"})],
                ),
                Message.assistant(content="Done"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[read_file], max_iterations=5)
        result = await loop.run([Message.user("read /x")])
        assert any(m.role == "tool" and m.name == "read_file" for m in result)


# ─── Guardrails ───


class TestGuardrails:
    @pytest.mark.asyncio
    async def test_input_guardrail_blocks(self):
        async def block_pii(input_text: str, state: AgentState) -> GuardrailResult:
            if "SSN" in input_text:
                return GuardrailResult(tripwire_triggered=True, message="PII detected")
            return GuardrailResult()

        @tool
        def echo(text: str) -> str:
            return text

        provider = MockProvider(responses=[Message.assistant(content="ok")])
        loop = AgentLoop(
            provider=provider,
            tools=[echo],
            max_iterations=3,
            input_guardrails=[InputGuardrail(name="pii_check", check=block_pii)],
        )
        result = await loop.run([Message.user("My SSN is 123-45-6789")])
        assert any("blocked" in str(m.content).lower() for m in result)

    @pytest.mark.asyncio
    async def test_output_guardrail_blocks(self):
        async def block_secrets(output: str, state: AgentState) -> GuardrailResult:
            if "password" in output.lower():
                return GuardrailResult(tripwire_triggered=True, message="Secret in output")
            return GuardrailResult()

        provider = MockProvider(responses=[Message.assistant(content="Your password is xyz")])
        loop = AgentLoop(
            provider=provider,
            max_iterations=3,
            output_guardrails=[OutputGuardrail(name="secret_check", check=block_secrets)],
        )
        result = await loop.run([Message.user("What is my password?")])
        assert any("blocked" in str(m.content).lower() for m in result)

    def test_guardrail_result_default(self):
        r = GuardrailResult()
        assert r.tripwire_triggered is False
        assert r.message is None

    def test_guardrail_tripwire_exception(self):
        result = GuardrailResult(tripwire_triggered=True, message="blocked")
        exc = GuardrailTripwire(result, "test")
        assert exc.result.tripwire_triggered


# ─── Handoffs ───


class TestHandoffs:
    def test_tool_name_auto_generated(self):
        h = Handoff(agent_name="research", description="Research agent")
        assert h.tool_name == "transfer_to_research"

    def test_custom_tool_name(self):
        h = Handoff(agent_name="math", tool_name="call_math_agent", description="Math agent")
        assert h.tool_name == "call_math_agent"

    def test_handoff_input(self):
        hi = HandoffInput(
            input_history=[Message.user("hello")],
            pre_handoff_items=[Message.assistant("hi")],
            new_items=[Message.user("do math")],
        )
        assert len(hi.input_history) == 1


# ─── Tracing ───


class TestTracing:
    def test_span_creation(self):
        provider = TraceProvider()
        span = provider.start_span_sync(SpanType.AGENT, "test_run")
        assert span.type == SpanType.AGENT
        assert span.name == "test_run"

    def test_span_end(self):
        provider = TraceProvider()
        span = provider.start_span_sync(SpanType.LLM_CALL, "call_0")
        provider.end_span(span)
        assert span.ended_at is not None

    def test_console_processor(self):
        processor = ConsoleTraceProcessor()
        span = Span(
            span_id="s1",
            type=SpanType.TOOL_EXECUTION,
            name="time_get",
            started_at="2026-01-01T00:00:00",
        )
        asyncio.run(processor.on_span_start(span))
        span.finish()
        asyncio.run(processor.on_span_end(span))

    def test_json_processor(self, tmp_path):
        log_file = tmp_path / "trace.jsonl"
        processor = JsonTraceProcessor(path=str(log_file))
        span = Span(span_id="s1", type=SpanType.AGENT, name="run", started_at="2026-01-01T00:00:00")
        asyncio.run(processor.on_span_start(span))
        span.finish()
        asyncio.run(processor.on_span_end(span))
        assert log_file.exists()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        provider = TraceProvider()
        async with provider.start_span(SpanType.LLM_CALL, "test") as span:
            span.set_meta("tokens", 100)
        assert span.ended_at is not None
        assert span.metadata.get("tokens") == 100


# ─── Block streaming in agent loop ───


class TestBlockStreaming:
    @pytest.mark.asyncio
    async def test_stream_emits_text_blocks(self):
        @tool
        def greet(name: str) -> str:
            return f"Hello {name}"

        chunks = []
        provider = MockProvider(
            responses=[
                Message.assistant(
                    content="",
                    tool_calls=[ToolCall(id="c1", name="greet", arguments={"name": "World"})],
                ),
                Message.assistant(content="Done!"),
            ]
        )
        loop = AgentLoop(provider=provider, tools=[greet], max_iterations=5)
        async for chunk in loop.run_stream([Message.user("Hi")]):
            chunks.append(chunk)
        types = [c.type for c in chunks]
        assert "done" in types

    @pytest.mark.asyncio
    async def test_stream_no_tools(self):
        provider = MockProvider(responses=[Message.assistant(content="Hello!")])
        loop = AgentLoop(provider=provider, max_iterations=3)
        chunks = []
        async for chunk in loop.run_stream([Message.user("Hi")]):
            chunks.append(chunk)
        assert any(c.type == "done" for c in chunks)


# ─── WS Protocol new message types ───


class TestWSProtocolNewTypes:
    def test_text_delta_message(self):
        from src.http.ws_protocol import TextDeltaMessage

        msg = TextDeltaMessage(content="hi")
        assert msg.type == "text_delta"

    def test_tool_input_start_message(self):
        from src.http.ws_protocol import ToolInputStartMessage

        msg = ToolInputStartMessage(tool="time_get", call_id="c1", args={})
        assert msg.type == "tool_input_start"
        assert msg.tool == "time_get"

    def test_tool_result_message(self):
        from src.http.ws_protocol import ToolResultMessage

        msg = ToolResultMessage(tool="time_get", call_id="c1", result_preview="3pm")
        assert msg.type == "tool_result"

    def test_reasoning_delta_message(self):
        from src.http.ws_protocol import ReasoningDeltaMessage

        msg = ReasoningDeltaMessage(content="hmm")
        assert msg.type == "reasoning_delta"

    def test_backward_compat_ai_token_still_works(self):
        from src.http.ws_protocol import AiTokenMessage

        msg = AiTokenMessage(content="hi")
        d = msg.model_dump()
        assert d["type"] == "ai_token"

    def test_stream_chunk_to_ws_text_delta(self):
        chunk = StreamChunk.text_delta("hello")
        ws_msg = chunk.to_ws_message()
        assert ws_msg["type"] == "text_delta"
        assert ws_msg["content"] == "hello"

    def test_stream_chunk_to_ws_tool_input_start(self):
        chunk = StreamChunk.tool_input_start(tool="x", call_id="c1")
        ws_msg = chunk.to_ws_message()
        assert ws_msg["type"] == "tool_input_start"

    def test_stream_chunk_to_ws_backward_compat(self):
        chunk = StreamChunk.ai_token("hi")
        ws_msg = chunk.to_ws_message()
        assert ws_msg["type"] == "ai_token"

        chunk = StreamChunk.tool_start(tool="x", call_id="c1")
        ws_msg = chunk.to_ws_message()
        assert ws_msg["type"] == "tool_start"
