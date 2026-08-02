"""Tests for summarization middleware — aligned with LangChain API."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.sdk.messages import Message
from src.sdk.run_models import ContextFreshness, ContextSnapshot, ContextSource


def _msg(role: str, content: str = "", tool_call_id: str | None = None, tool_calls=None) -> Message:
    if role == "tool":
        return Message(role="tool", content=content, tool_call_id=tool_call_id or "tc1", name="time_get")
    if role == "assistant" and tool_calls:
        return Message(role="assistant", content=content, tool_calls=tool_calls)
    return Message(role=role, content=content)


def _tc(tc_id: str, name: str = "time_get", args=None) -> Message:
    from src.sdk.messages import ToolCall
    return ToolCall(id=tc_id, name=name, arguments=args or {})


def _context(reason="manual"):
    from src.sdk.compression import CompressionContext

    return CompressionContext(
        session_id="session-1",
        model="ollama-cloud:test",
        attempt=1,
        llm_call_index=1,
        reason=reason,
    )


def _stored(role: str, content: str, storage_id: str) -> Message:
    return Message(role=role, content=content, storage_id=storage_id)


class _Provider:
    def __init__(self, content="summary", usage=None, error=None):
        self.content = content
        self.usage = usage
        self.error = error
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return Message.assistant(self.content, usage=self.usage)


# -- Typed compression contracts --


def test_compression_context_validates_identity_and_json():
    before = ContextSnapshot(
        model="ollama-cloud:test",
        attempt=1,
        llm_call_index=1,
        estimated_tokens=10,
        context_window=100,
        percentage=10,
        source=ContextSource.PREPARED_CONTEXT,
        freshness=ContextFreshness.LIVE,
        estimated=True,
    )
    context = _context()
    context = context.model_copy(update={"before": before})
    assert context.model_dump(mode="json")["reason"] == "manual"
    with pytest.raises(ValidationError):
        context.__class__(**{**context.model_dump(), "attempt": 2})


@pytest.mark.parametrize("field,value", [("session_id", ""), ("attempt", 0), ("llm_call_index", 0)])
def test_compression_context_rejects_invalid_fields(field, value):
    data = _context().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        _context().__class__(**data)


def test_summary_persistence_result_invariants():
    from src.sdk.compression import PersistenceStatus, SummaryPersistenceResult

    assert SummaryPersistenceResult(status="succeeded", summary_id="sum-1").status is PersistenceStatus.SUCCEEDED
    with pytest.raises(ValidationError):
        SummaryPersistenceResult(status="succeeded")
    with pytest.raises(ValidationError):
        SummaryPersistenceResult(status="failed", summary_id="sum-1")


def test_compression_artifact_rejects_empty_duplicate_ids_and_extra_fields():
    from src.sdk.compression import CompressionArtifact, CompressionMessage

    base = dict(
        summary="summary",
        replacement_messages=(CompressionMessage.from_message(Message.user("summary")),),
        summarized_message_ids=("one",),
        preserved_message_ids=("two",),
        persistence_eligible=True,
    )
    with pytest.raises(ValidationError):
        CompressionArtifact(**{**base, "summarized_message_ids": ("one", "one")})
    with pytest.raises(ValidationError):
        CompressionArtifact(**{**base, "summary": ""})
    with pytest.raises(ValidationError):
        CompressionArtifact(**base, unknown=True)


def test_compression_result_compressed_invariant():
    from src.sdk.compression import CompressionResult, CompressionTelemetry

    telemetry = CompressionTelemetry(
        status="skipped",
        reason="manual",
        summary_model="ollama-cloud:test",
        persistence={"status": "not_requested"},
    )
    result = CompressionResult(telemetry=telemetry)
    assert result.compressed is False
    assert result.model_dump(mode="json")["telemetry"]["status"] == "skipped"


def test_failed_telemetry_requires_error_and_forbids_after_context():
    from src.sdk.compression import CompressionTelemetry

    with pytest.raises(ValidationError):
        CompressionTelemetry(
            status="failed",
            reason="manual",
            summary_model="ollama-cloud:test",
            persistence={"status": "not_requested"},
        )


def test_compression_snapshots_are_deeply_owned_and_materialize_fresh_messages():
    from src.sdk.compression import CompressionMessage
    from src.sdk.messages import ToolCall, Usage

    source = Message(
        role="assistant",
        content=[{"type": "text", "text": "original"}],
        tool_calls=[ToolCall(id="tc", name="tool", arguments={"nested": [1, 2]})],
        provider_metadata={"provider": {"nested": {"value": 1}}},
        usage=Usage(input_tokens=3),
        storage_id="stored-1",
        source="history",
    )
    snapshot = CompressionMessage.from_message(source)
    source.content[0]["text"] = "mutated"
    source.tool_calls[0].arguments["nested"].append(3)
    assert snapshot.to_message().content[0]["text"] == "original"
    assert snapshot.to_message().tool_calls[0].arguments == {"nested": [1, 2]}
    first = snapshot.to_message()
    second = snapshot.to_message()
    first.content[0]["text"] = "changed"
    assert second.content[0]["text"] == "original"
    assert first is not second
    with pytest.raises(ValidationError):
        snapshot.content_json = '"changed"'


def test_compression_result_rejects_count_and_persistence_mismatches():
    from src.sdk.compression import (
        CompressionArtifact,
        CompressionMessage,
        CompressionResult,
        CompressionTelemetry,
    )

    snapshot = CompressionMessage.from_message(Message.user("summary"))
    artifact = CompressionArtifact(
        summary="summary",
        replacement_messages=(snapshot,),
        summarized_message_ids=("old-1",),
        preserved_message_ids=(),
        persistence_eligible=True,
    )
    telemetry = CompressionTelemetry(
        status="succeeded",
        reason="manual",
        summarized_message_count=2,
        replacement_message_count=1,
        summary_model="ollama-cloud:test",
        persistence={"status": "not_requested"},
    )
    with pytest.raises(ValidationError):
        CompressionResult(artifact=artifact, telemetry=telemetry)


def test_compression_result_rejects_invalid_successful_persistence_linkage():
    from src.sdk.compression import (
        CompressionArtifact,
        CompressionMessage,
        CompressionResult,
        CompressionTelemetry,
    )

    snapshot = CompressionMessage.from_message(Message.user("summary"))
    artifact = CompressionArtifact(
        summary="summary",
        replacement_messages=(snapshot,),
        summarized_message_ids=("old-1",),
        persistence_eligible=True,
    )
    telemetry = CompressionTelemetry(
        status="succeeded",
        reason="manual",
        summarized_message_count=1,
        replacement_message_count=1,
        summary_model="ollama-cloud:test",
        persistence={"status": "succeeded", "summary_id": "summary-1"},
    )
    with pytest.raises(ValidationError):
        CompressionResult(artifact=artifact, telemetry=telemetry)


# -- ProviderContextOverflowError --


def test_provider_context_overflow_error_exists():
    from src.sdk.providers.base import ProviderContextOverflowError
    err = ProviderContextOverflowError("too long")
    assert "too long" in str(err)
    assert isinstance(err, Exception)


# -- AI/Tool pair preservation --


def test_find_safe_cutoff_point_preserves_ai_tool_pair():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("assistant", "let me check", tool_calls=[_tc("tc1")]),
        _msg("tool", "result", tool_call_id="tc1"),
        _msg("user", "thanks"),
        _msg("assistant", "done"),
    ]

    # Cutoff at index 2 (tool message) — should search back to include the assistant at index 1
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 2)
    assert cutoff == 1  # Include the assistant message that initiated the tool call


def test_find_safe_cutoff_point_no_tool_at_cutoff():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("assistant", "hi"),
        _msg("user", "bye"),
    ]

    # No tool at cutoff — return as-is
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 1)
    assert cutoff == 1


def test_find_safe_cutoff_point_advances_past_orphaned_tools():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [
        _msg("user", "hello"),
        _msg("tool", "orphan result", tool_call_id="tc_missing"),
        _msg("user", "bye"),
    ]

    # No matching AI message found — should advance past tool messages
    cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, 1)
    assert cutoff == 2  # Skip the orphaned tool message


# -- Token counting --


def test_count_tokens_returns_positive():
    from src.sdk.middleware_summarization import count_tokens_approximately

    messages = [_msg("user", "hello world")]
    tokens = count_tokens_approximately(messages)
    assert tokens > 0


def test_count_tokens_public_method():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    tokens = mw.count_tokens([_msg("user", "hello")])
    assert tokens > 0


# -- Trigger evaluation --


def test_trigger_tokens_exceeds():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("tokens", 5))
    messages = [_msg("user", "hello world this is a long message")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is True


def test_trigger_tokens_below_threshold():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("tokens", 10000))
    messages = [_msg("user", "hi")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is False


def test_trigger_messages_exceeds():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=("messages", 3))
    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3"), _msg("assistant", "4")]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is True


def test_trigger_and_clause():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger={"tokens": 5, "messages": 3})
    messages = [_msg("user", "1"), _msg("assistant", "2")]
    total = mw.token_counter(messages)
    # Only 2 messages but tokens > 5 — AND clause requires both, so should NOT trigger
    assert mw._should_summarize(messages, total) is False


def test_trigger_or_clause():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=[("tokens", 5), ("messages", 3)])
    messages = [_msg("user", "1"), _msg("assistant", "2")]
    total = mw.token_counter(messages)
    # 2 messages (< 3) but tokens > 5 — OR clause, should trigger
    assert mw._should_summarize(messages, total) is True


def test_trigger_none_never_triggers():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trigger=None)
    messages = [_msg("user", "hello" * 1000)]
    total = mw.token_counter(messages)
    assert mw._should_summarize(messages, total) is False


# -- Cutoff determination --


def test_determine_cutoff_with_messages_keep():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("messages", 2))
    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3"), _msg("assistant", "4")]
    cutoff = mw._determine_cutoff_index(messages)
    assert cutoff == 2  # Keep last 2 messages


def test_determine_cutoff_with_tokens_keep():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("tokens", 10))
    messages = [_msg("user", "long message here"), _msg("assistant", "short"), _msg("user", "bye")]
    cutoff = mw._determine_cutoff_index(messages)
    assert cutoff >= 1  # At least 1 message to summarize


# -- Message trimming --


def test_trim_messages_for_summary():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trim_tokens_to_summarize=20)
    messages = [_msg("user", f"message {i} " * 10) for i in range(10)]
    trimmed = mw._trim_messages_for_summary(messages)
    assert len(trimmed) < len(messages)


def test_trim_messages_none_returns_all():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test", trim_tokens_to_summarize=None)
    messages = [_msg("user", "hello")]
    trimmed = mw._trim_messages_for_summary(messages)
    assert len(trimmed) == len(messages)


# -- Summary message type --


def test_build_new_messages_uses_user_role_with_source():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    msgs = SummarizationMiddleware._build_new_messages("test summary")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert "test summary" in msgs[0].content
    assert getattr(msgs[0], "source", None) == "summarization_middleware"


# -- force_summarize --


@pytest.mark.asyncio
async def test_force_summarize_skips_short_conversation_without_mutation():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    state = AgentState(messages=[Message.user("hi")])
    original = list(state.messages)
    result = await mw.force_summarize(state, _context())
    assert result.compressed is False
    assert state.messages == original


@pytest.mark.asyncio
async def test_force_summarize_skips_when_keep_window_covers_all_messages():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    mw = SummarizationMiddleware(model="ollama-cloud:test", keep=("messages", 2))
    state = AgentState(messages=[Message.user("hi"), Message.assistant("hello")])
    # Can't split — all messages are in the "keep" window
    result = await mw.force_summarize(state, _context())
    assert result.compressed is False


# -- Constructor validation --


def test_validate_context_size_rejects_zero():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    with pytest.raises(ValueError):
        SummarizationMiddleware._validate_context_size(("tokens", 0), "trigger")


def test_validate_context_size_rejects_invalid_fraction():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    with pytest.raises(ValueError):
        SummarizationMiddleware._validate_context_size(("fraction", 1.5), "keep")


def test_normalize_trigger_rejects_unknown_key():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(model="ollama-cloud:test")
    with pytest.raises(ValueError):
        mw._normalize_trigger({"unknown": 5})  # type: ignore[dict-item]


# -- Partition --


def test_partition_messages():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [_msg("user", "1"), _msg("assistant", "2"), _msg("user", "3")]
    to_summarize, preserved = SummarizationMiddleware._partition_messages(messages, 2)
    assert len(to_summarize) == 2
    assert len(preserved) == 1
    assert preserved[0].content == "3"


# -- Lossless typed compression behavior --


@pytest.mark.asyncio
async def test_force_retains_recent_suffix_and_unsummarized_old_remainder():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    provider = _Provider()
    messages = [_stored("user", str(i), f"id-{i}") for i in range(6)]
    state = AgentState(messages=list(messages))
    mw = SummarizationMiddleware(
        "ollama-cloud:test",
        keep=("messages", 2),
        trim_tokens_to_summarize=2,
        token_counter=lambda items: len(list(items)),
        summary_provider_factory=lambda: provider,
    )
    result = await mw.force_summarize(state, _context())
    assert result.compressed
    assert [m.content for m in state.messages[1:]] == ["2", "3", "4", "5"]
    assert result.artifact.summarized_message_ids == ("id-0", "id-1")
    assert result.artifact.preserved_message_ids == ("id-2", "id-3", "id-4", "id-5")


@pytest.mark.asyncio
async def test_auto_and_force_use_identical_compression_output():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    messages = [_stored("user", str(i), f"id-{i}") for i in range(4)]
    kwargs = dict(
        model="ollama-cloud:test",
        trigger=("messages", 3),
        keep=("messages", 2),
        summary_provider_factory=lambda: _Provider("same"),
    )
    forced_state = AgentState(messages=list(messages))
    forced = await SummarizationMiddleware(**kwargs).force_summarize(forced_state, _context())
    auto_state = AgentState(messages=list(messages), extra={"_compression_context": _context("threshold")})
    update = await SummarizationMiddleware(**kwargs).abefore_model(auto_state)
    assert update is not None
    assert [m.content for m in forced.artifact.replacement_messages] == [m.content for m in update["messages"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [RuntimeError("boom"), ""])
async def test_generation_failure_leaves_exact_state_and_does_not_call_sink(response):
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    sink_calls = []
    provider = _Provider(error=response) if isinstance(response, Exception) else _Provider(response)
    messages = [_stored("user", str(i), f"id-{i}") for i in range(4)]
    state = AgentState(messages=messages)
    mw = SummarizationMiddleware(
        "ollama-cloud:test",
        keep=("messages", 2),
        summary_provider_factory=lambda: provider,
        summary_sink=lambda context, artifact: sink_calls.append(artifact),
    )
    result = await mw.force_summarize(state, _context())
    assert result.telemetry.status == "failed"
    assert state.messages is messages
    assert sink_calls == []


@pytest.mark.asyncio
async def test_replacement_token_counter_failure_precedes_sink_and_leaves_state_unchanged():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    sink_calls = []

    def counter(items):
        materialized = list(items)
        if any(getattr(message, "source", None) == "summarization_middleware" for message in materialized):
            raise RuntimeError("replacement count failed")
        return len(materialized)

    messages = [_stored("user", str(i), f"id-{i}") for i in range(4)]
    state = AgentState(messages=messages)
    middleware = SummarizationMiddleware(
        "ollama-cloud:test",
        keep=("messages", 2),
        token_counter=counter,
        summary_provider_factory=lambda: _Provider(),
        summary_sink=lambda context, artifact: sink_calls.append(artifact),
    )
    result = await middleware.force_summarize(state, _context())
    assert result.telemetry.status == "failed"
    assert result.telemetry.error_code == "compression_preparation_error"
    assert state.messages is messages
    assert state.extra == {}
    assert sink_calls == []


@pytest.mark.asyncio
async def test_unknown_summary_template_placeholder_is_typed_failure_without_sink():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    provider = _Provider()
    sink_calls = []
    messages = [_stored("user", str(i), f"id-{i}") for i in range(4)]
    state = AgentState(messages=messages)
    middleware = SummarizationMiddleware(
        "ollama-cloud:test",
        keep=("messages", 2),
        summary_prompt="{messages} {unknown_placeholder}",
        summary_provider_factory=lambda: provider,
        summary_sink=lambda context, artifact: sink_calls.append(artifact),
    )
    result = await middleware.force_summarize(state, _context())
    assert result.telemetry.status == "failed"
    assert state.messages is messages
    assert provider.calls == 0
    assert sink_calls == []


@pytest.mark.asyncio
async def test_sink_success_propagates_storage_id_to_summary_message():
    from src.sdk.compression import SummaryPersistenceResult
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    calls = []

    async def sink(context, artifact):
        calls.append((context, artifact))
        return SummaryPersistenceResult(status="succeeded", summary_id="summary-1")

    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(4)])
    mw = SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 2), summary_provider_factory=lambda: _Provider(), summary_sink=sink
    )
    result = await mw.force_summarize(state, _context())
    assert len(calls) == 1
    assert calls[0][0] == _context()
    assert calls[0][1].summary == "summary"
    assert result.artifact.persisted_summary_id == "summary-1"
    assert state.messages[0].storage_id == "summary-1"


@pytest.mark.asyncio
async def test_sink_cannot_mutate_snapshot_and_state_materialization_is_independent():
    from src.sdk.compression import SummaryPersistenceResult
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    mutation_errors = []

    def sink(context, artifact):
        try:
            artifact.replacement_messages[0].content_json = '"corrupt"'
        except ValidationError as exc:
            mutation_errors.append(exc)
        return SummaryPersistenceResult(status="succeeded", summary_id="summary-owned")

    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(4)])
    result = await SummarizationMiddleware(
        "ollama-cloud:test",
        keep=("messages", 2),
        summary_provider_factory=lambda: _Provider(),
        summary_sink=sink,
    ).force_summarize(state, _context())
    assert mutation_errors
    state.messages[0].content = "state mutation"
    assert result.artifact.replacement_messages[0].content != "state mutation"
    assert result.artifact.replacement_messages[0].to_message() is not state.messages[0]


@pytest.mark.asyncio
async def test_langfuse_observation_uses_unwrapped_provider_exactly_once(monkeypatch):
    from src.sdk.langfuse_tracer import LangfuseTracer
    from src.sdk.middleware_summarization import SummarizationMiddleware

    class Observation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            return None

    class Client:
        observations = 0

        def start_as_current_observation(self, **kwargs):
            self.observations += 1
            return Observation()

    class WrappedProvider:
        wrapped_calls = 0
        original_calls = 0

        async def chat(self, messages):
            self.wrapped_calls += 1
            return Message.assistant("wrapped")

        async def _original_chat(self, messages):
            self.original_calls += 1
            return Message.assistant("summary")

    client = Client()
    provider = WrappedProvider()
    monkeypatch.setattr(LangfuseTracer, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(LangfuseTracer, "_get_client", staticmethod(lambda: client))
    middleware = SummarizationMiddleware(
        "ollama-cloud:test", summary_provider_factory=lambda: provider
    )
    summary, _ = await middleware._acreate_summary([Message.user("history")])
    assert summary == "summary"
    assert client.observations == 1
    assert provider.original_calls == 1
    assert provider.wrapped_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["setup", "update"])
async def test_langfuse_failure_never_duplicates_provider_call(monkeypatch, failure_point):
    from src.sdk.langfuse_tracer import LangfuseTracer
    from src.sdk.middleware_summarization import SummarizationMiddleware

    class Observation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            if failure_point == "update":
                raise RuntimeError("trace update failed")

    class Client:
        def start_as_current_observation(self, **kwargs):
            if failure_point == "setup":
                raise RuntimeError("trace setup failed")
            return Observation()

    provider = _Provider()
    monkeypatch.setattr(LangfuseTracer, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(LangfuseTracer, "_get_client", staticmethod(lambda: Client()))
    middleware = SummarizationMiddleware(
        "ollama-cloud:test", summary_provider_factory=lambda: provider
    )
    summary, _ = await middleware._acreate_summary([Message.user("history")])
    assert summary == "summary"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_provider_failure_inside_langfuse_observation_is_not_retried(monkeypatch):
    from src.sdk.langfuse_tracer import LangfuseTracer
    from src.sdk.middleware_summarization import SummarizationMiddleware

    class Observation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Client:
        def start_as_current_observation(self, **kwargs):
            return Observation()

    provider = _Provider(error=RuntimeError("provider failed"))
    monkeypatch.setattr(LangfuseTracer, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(LangfuseTracer, "_get_client", staticmethod(lambda: Client()))
    middleware = SummarizationMiddleware(
        "ollama-cloud:test", summary_provider_factory=lambda: provider
    )
    with pytest.raises(Exception, match="summary provider failed"):
        await middleware._acreate_summary([Message.user("history")])
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_sink_failure_does_not_fail_compression():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    def sink(context, artifact):
        raise RuntimeError("database unavailable")

    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(4)])
    mw = SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 2), summary_provider_factory=lambda: _Provider(), summary_sink=sink
    )
    result = await mw.force_summarize(state, _context())
    assert result.compressed
    assert result.telemetry.persistence.status == "failed"
    assert result.artifact.persisted_summary_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_sink", [False, True])
async def test_persistence_not_requested_when_ineligible_or_sink_absent(with_sink):
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    calls = []
    sink = (lambda context, artifact: calls.append(artifact)) if with_sink else None
    state = AgentState(messages=[Message.user(str(i)) for i in range(4)])
    mw = SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 2), summary_provider_factory=lambda: _Provider(), summary_sink=sink
    )
    result = await mw.force_summarize(state, _context())
    assert result.telemetry.persistence.status == "not_requested"
    assert calls == []


@pytest.mark.asyncio
async def test_second_compression_of_persisted_summary_remains_eligible():
    from src.sdk.compression import SummaryPersistenceResult
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    sequence = iter(["summary-1", "summary-2"])

    def sink(context, artifact):
        return SummaryPersistenceResult(status="succeeded", summary_id=next(sequence))

    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(4)])
    mw = SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 2), summary_provider_factory=lambda: _Provider(), summary_sink=sink
    )
    await mw.force_summarize(state, _context())
    state.messages.extend([_stored("user", "new-1", "new-1"), _stored("user", "new-2", "new-2")])
    result = await mw.force_summarize(state, _context())
    assert result.artifact.persistence_eligible
    assert result.artifact.persisted_summary_id == "summary-2"


@pytest.mark.asyncio
async def test_summary_provider_factory_is_lazy_and_called_once():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    calls = []
    provider = _Provider()

    def factory():
        calls.append(True)
        return provider

    mw = SummarizationMiddleware("ollama-cloud:test", keep=("messages", 1), summary_provider_factory=factory)
    assert calls == []
    for offset in (0, 10):
        state = AgentState(messages=[_stored("user", str(offset + i), f"id-{offset+i}") for i in range(3)])
        await mw.force_summarize(state, _context())
    assert calls == [True]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_usage_aggregation_is_exact():
    from src.sdk.messages import Usage
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    usage = Usage(input_tokens=11, output_tokens=7, reasoning_tokens=3, cache_read_tokens=2, cache_creation_tokens=1)
    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(3)])
    result = await SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 1), summary_provider_factory=lambda: _Provider(usage=usage)
    ).force_summarize(state, _context())
    aggregate = result.telemetry.summarizer_usage
    assert aggregate.model_dump() == {
        "available": True, "calls": 1, "models": ("ollama-cloud:test",), "input_tokens": 11,
        "output_tokens": 7, "reasoning_tokens": 3, "cache_read_tokens": 2, "cache_creation_tokens": 1,
    }


@pytest.mark.asyncio
async def test_absent_usage_is_unavailable():
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    state = AgentState(messages=[_stored("user", str(i), f"id-{i}") for i in range(3)])
    result = await SummarizationMiddleware(
        "ollama-cloud:test", keep=("messages", 1), summary_provider_factory=lambda: _Provider()
    ).force_summarize(state, _context())
    assert result.telemetry.summarizer_usage.available is False


def test_prefix_trim_respects_budget_without_losing_remainder():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware(
        "ollama-cloud:test", trim_tokens_to_summarize=2, token_counter=lambda items: len(list(items))
    )
    messages = [Message.user(str(i)) for i in range(4)]
    assert mw._trim_messages_for_summary(messages) == messages[:2]


def test_prefix_trim_returns_empty_for_oversized_first_message():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    mw = SummarizationMiddleware("ollama-cloud:test", trim_tokens_to_summarize=1, token_counter=lambda items: 2)
    assert mw._trim_messages_for_summary([Message.user("oversized")]) == []


def test_prefix_trim_does_not_split_assistant_tool_pair():
    from src.sdk.middleware_summarization import SummarizationMiddleware

    messages = [Message.user("a"), _msg("assistant", tool_calls=[_tc("tc")]), _msg("tool", "r", "tc")]
    mw = SummarizationMiddleware(
        "ollama-cloud:test", trim_tokens_to_summarize=2, token_counter=lambda items: len(list(items))
    )
    assert mw._trim_messages_for_summary(messages) == messages[:1]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeded", "failed", "skipped"])
async def test_automatic_hook_stores_typed_result_for_every_outcome(outcome):
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    provider = _Provider(error=RuntimeError("boom")) if outcome == "failed" else _Provider()
    trigger = ("messages", 99) if outcome == "skipped" else ("messages", 2)
    state = AgentState(
        messages=[_stored("user", str(i), f"id-{i}") for i in range(4)],
        extra={"_compression_context": _context("threshold")},
    )
    update = await SummarizationMiddleware(
        "ollama-cloud:test", trigger=trigger, keep=("messages", 2), summary_provider_factory=lambda: provider
    ).abefore_model(state)
    assert update["extra"]["_compression_result"].telemetry.status == outcome
    assert ("messages" in update) is (outcome == "succeeded")
