"""Tests for the canonical versioned run event envelope."""

import json
import operator
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from src.sdk.history_models import ReasoningBlock, ToolBlock, TurnsResponse
from src.sdk.messages import StreamChunk, Usage
from src.sdk.run_events import (
    BlockDeltaData,
    ContextCompressedEvent,
    ContextSnapshotEvent,
    DoneEvent,
    ErrorEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    RevisionStartEvent,
    RubricEndEvent,
    RubricStartEvent,
    RunEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolEndData,
    ToolInputDeltaEvent,
    ToolInputEndEvent,
    ToolInputStartEvent,
    ToolResultData,
    ToolResultEvent,
    UsageEvent,
    UsageEventData,
    parse_run_event,
)
from src.sdk.run_models import RunResult, UsageCategory
from src.sdk.run_service import _stream_chunk_to_event

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "run_contracts"


def envelope(event_type: str, data: dict[str, Any], *, attempt: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": "event-1",
        "sequence": 1,
        "timestamp": NOW,
        "session_id": "session-1",
        "run_id": "run-1",
        "attempt": attempt,
        "type": event_type,
        "data": data,
    }


def context_snapshot(*, attempt: int = 1) -> dict[str, Any]:
    return {
        "model": " openai : gpt-5 ",
        "attempt": attempt,
        "llm_call_index": 1,
        "estimated_tokens": 10,
        "context_window": 100,
        "percentage": 10,
        "source": "provider_usage",
        "freshness": "live",
        "estimated": False,
    }


def rubric_evaluation(*, attempt: int = 1) -> dict[str, Any]:
    return {
        "grading_run_id": "grading-1",
        "attempt": attempt,
        "result": "satisfied",
        "explanation": "All criteria passed.",
        "criteria": [{"name": "correct", "passed": True}],
        "passed_count": 1,
        "total_count": 1,
    }


def run_result(
    *,
    attempt: int = 1,
    run_id: str = "run-1",
    session_id: str = "session-1",
    persisted_at: str | None = "2026-08-02T12:00:00Z",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": session_id,
        "status": "completed",
        "attempt": attempt,
        "model": "openai:gpt-5",
        "response": "Hello",
        "usage": {"agent": {"available": True, "calls": 1, "models": ["openai:gpt-5"]}},
        "verification": {"availability": "off"},
        "persisted_at": persisted_at,
    }


def test_canonical_run_events_fixture_is_contiguous_and_round_trips() -> None:
    result = RunResult.model_validate_json((FIXTURE_DIR / "run_result.json").read_text())
    payloads = json.loads((FIXTURE_DIR / "run_events.json").read_text())
    turns = TurnsResponse.model_validate_json(
        (FIXTURE_DIR / "turns_response.json").read_text()
    )

    events = [parse_run_event(payload) for payload in payloads]
    turn = turns.turns[0]
    assert turn.result == result
    reasoning = next(block for block in turn.blocks if isinstance(block, ReasoningBlock))
    tool = next(block for block in turn.blocks if isinstance(block, ToolBlock))

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)
    assert {event.run_id for event in events} == {result.run_id}
    assert {event.session_id for event in events} == {result.session_id}
    assert {event.attempt for event in events} == {result.attempt}
    assert [event.timestamp for event in events] == sorted(event.timestamp for event in events)
    assert [event.model_dump(mode="json") for event in events] == payloads
    assert result.persisted_at is not None
    assert max(event.timestamp for event in events[:-1]) <= result.persisted_at
    assert result.persisted_at <= events[-1].timestamp

    reasoning_starts = [event for event in events if isinstance(event, ReasoningStartEvent)]
    reasoning_deltas = [event for event in events if isinstance(event, ReasoningDeltaEvent)]
    reasoning_ends = [event for event in events if isinstance(event, ReasoningEndEvent)]
    assert len(reasoning_starts) == len(reasoning_ends) == 1
    assert reasoning_deltas
    reasoning_start = reasoning_starts[0]
    reasoning_end = reasoning_ends[0]
    assert reasoning_start.sequence < min(event.sequence for event in reasoning_deltas)
    assert max(event.sequence for event in reasoning_deltas) < reasoning_end.sequence
    assert {
        reasoning_start.data.block_id,
        *(event.data.block_id for event in reasoning_deltas),
        reasoning_end.data.block_id,
    } == {reasoning.id}
    reasoning_text = "".join(
        event.data.delta for event in reasoning_deltas
    )

    text_starts = [event for event in events if isinstance(event, TextStartEvent)]
    text_deltas = [event for event in events if isinstance(event, TextDeltaEvent)]
    text_ends = [event for event in events if isinstance(event, TextEndEvent)]
    assert len(text_starts) == len(text_ends) == 1
    assert text_deltas
    text_start = text_starts[0]
    text_end = text_ends[0]
    assert text_start.sequence < min(event.sequence for event in text_deltas)
    assert max(event.sequence for event in text_deltas) < text_end.sequence
    assert {
        text_start.data.block_id,
        *(event.data.block_id for event in text_deltas),
        text_end.data.block_id,
    } == {text_start.data.block_id}
    answer_text = "".join(
        event.data.delta for event in text_deltas
    )
    assert reasoning_text == reasoning.content
    assert turn.answer is not None
    assert answer_text == turn.answer.content == result.response

    usage_aggregates = {
        UsageCategory.AGENT: result.usage.agent,
        UsageCategory.GRADER: result.usage.grader,
        UsageCategory.SUMMARIZER: result.usage.summarizer,
    }
    usage_events = [event for event in events if isinstance(event, UsageEvent)]
    usage_keys = [(event.data.category, event.data.llm_call_index) for event in usage_events]
    assert len(usage_keys) == len(set(usage_keys))
    assert {event.data.category for event in usage_events} == {
        category for category, aggregate in usage_aggregates.items() if aggregate.available
    }
    usage_fields = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    )
    for category, aggregate in usage_aggregates.items():
        category_events = [event for event in usage_events if event.data.category is category]
        if not aggregate.available:
            assert not category_events
            continue
        assert aggregate.available
        assert aggregate.calls == len(category_events)
        assert aggregate.models == tuple(dict.fromkeys(event.data.model for event in category_events))
        assert all(
            getattr(aggregate, field)
            == sum(getattr(event.data.usage, field) for event in category_events)
            for field in usage_fields
        )

    rubric_starts = [event for event in events if isinstance(event, RubricStartEvent)]
    rubric_ends = [event for event in events if isinstance(event, RubricEndEvent)]
    assert len(rubric_starts) == len(rubric_ends) == 1
    rubric_start = rubric_starts[0]
    rubric_end = rubric_ends[0]
    assert rubric_start.sequence < rubric_end.sequence
    assert rubric_start.data.grading_run_id == rubric_end.data.evaluation.grading_run_id
    assert rubric_start.data.max_attempts == rubric_end.data.max_attempts
    assert rubric_end.data.max_attempts == result.verification.max_attempts
    assert rubric_end.data.evaluation == result.verification.evaluations[0]
    context = next(event for event in events if isinstance(event, ContextSnapshotEvent))
    assert context.data == result.next_context

    tool_starts = [event for event in events if isinstance(event, ToolInputStartEvent)]
    tool_deltas = [event for event in events if isinstance(event, ToolInputDeltaEvent)]
    tool_ends = [event for event in events if isinstance(event, ToolInputEndEvent)]
    tool_results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert len(tool_starts) == len(tool_ends) == len(tool_results) == 1
    assert tool_deltas
    tool_start = tool_starts[0]
    tool_end = tool_ends[0]
    tool_result = tool_results[0]
    assert tool_start.sequence < min(event.sequence for event in tool_deltas)
    assert max(event.sequence for event in tool_deltas) < tool_end.sequence < tool_result.sequence
    assert {
        tool_start.data.block_id,
        *(event.data.block_id for event in tool_deltas),
        tool_end.data.block_id,
        tool_result.data.block_id,
    } == {tool.id}
    assert {
        tool_start.data.tool_call_id,
        *(event.data.tool_call_id for event in tool_deltas),
        tool_end.data.tool_call_id,
        tool_result.data.tool_call_id,
    } == {tool.tool_call_id}
    assert tool_start.data.name == tool_result.data.name == tool.name
    assert "".join(event.data.delta for event in tool_deltas) == json.dumps(
        dict(tool.arguments), separators=(",", ":")
    )
    assert tool_end.data.arguments == tool.arguments
    assert tool_result.data.status == tool.status
    assert tool_result.data.content == tool.result

    assert isinstance(events[-1], DoneEvent)
    assert events[-1].data.result == result
    assert [parse_run_event(event.model_dump(mode="json")) for event in events] == events


def test_parse_text_delta_preserves_typed_data() -> None:
    event = parse_run_event(envelope("text_delta", {"block_id": "block-1", "delta": "Hi"}))

    assert isinstance(event, TextDeltaEvent)
    assert isinstance(event.data, BlockDeltaData)
    assert event.data.delta == "Hi"


@pytest.mark.parametrize("legacy_name", ["messages", "ai_token", "tool_start", "tool_end", "reasoning"])
def test_parse_rejects_legacy_event_names(legacy_name: str) -> None:
    with pytest.raises(ValidationError):
        parse_run_event(envelope(legacy_name, {}))


def test_sequence_must_start_at_one() -> None:
    payload = envelope("text_start", {"block_id": "block-1"})
    payload["sequence"] = 0

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        parse_run_event(payload)


def test_run_event_schema_has_exact_canonical_discriminator_values() -> None:
    choices = TypeAdapter(RunEvent).json_schema()["discriminator"]["mapping"]

    assert set(choices) == {
        "text_start",
        "text_delta",
        "text_end",
        "reasoning_start",
        "reasoning_delta",
        "reasoning_end",
        "tool_input_start",
        "tool_input_delta",
        "tool_input_end",
        "tool_result",
        "usage",
        "rubric_evaluation_start",
        "rubric_evaluation_end",
        "response_revision_start",
        "context_snapshot",
        "context_compressed",
        "done",
        "error",
        "interrupt",
    }


def test_rejects_naive_timestamp() -> None:
    payload = envelope("text_start", {"block_id": "block-1"})
    payload["timestamp"] = datetime(2026, 8, 2, 12)

    with pytest.raises(ValidationError, match="timezone-aware"):
        parse_run_event(payload)


@pytest.mark.parametrize("timestamp", [0, 1.5, "0"])
def test_rejects_numeric_timestamp(timestamp: object) -> None:
    payload = envelope("text_start", {"block_id": "block-1"})
    payload["timestamp"] = timestamp

    with pytest.raises(ValidationError, match="datetime or RFC3339 string"):
        parse_run_event(payload)


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 2, 14, tzinfo=timezone(timedelta(hours=2))),
        "2026-08-02T14:00:00+02:00",
    ],
)
def test_timestamp_is_normalized_to_utc(timestamp: datetime | str) -> None:
    payload = envelope("text_start", {"block_id": "block-1"})
    payload["timestamp"] = timestamp

    event = parse_run_event(payload)

    assert event.timestamp == NOW
    assert event.timestamp.tzinfo is UTC


def test_usage_normalizes_model_and_rejects_negative_single_call_usage() -> None:
    event = parse_run_event(
        envelope(
            "usage",
            {
                "category": "agent",
                "model": " openai : gpt-5 ",
                "llm_call_index": 1,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )
    )
    assert isinstance(event, UsageEvent)
    assert event.data.model == "openai:gpt-5"
    assert event.data.usage.input_tokens == 2
    with pytest.raises(ValidationError, match="frozen"):
        event.data.usage.input_tokens = 4

    payload = envelope(
        "usage",
        {
            "category": "agent",
            "model": "openai:gpt-5",
            "llm_call_index": 1,
            "usage": {"input_tokens": -1},
        },
    )
    with pytest.raises(ValidationError):
        parse_run_event(payload)


def test_usage_event_wire_schema_is_nested_and_uses_canonical_model_pattern() -> None:
    schema = UsageEventData.model_json_schema()

    assert set(schema["properties"]) == {"category", "model", "llm_call_index", "usage"}
    assert set(schema["required"]) == {"category", "model", "llm_call_index", "usage"}
    assert schema["properties"]["model"]["pattern"] == r"^[^:/\s]+:\S+$"
    with pytest.raises(ValidationError):
        UsageEventData.model_validate(
            {
                "category": "agent",
                "model": "openai:gpt-5",
                "llm_call_index": 1,
                "input_tokens": 1,
            }
        )


@pytest.mark.parametrize("event_type", ["rubric_evaluation_start", "rubric_evaluation_end"])
def test_rubric_event_attempt_cannot_exceed_max_attempts(event_type: str) -> None:
    data: dict[str, Any] = {"grading_run_id": "grading-1", "max_attempts": 2}
    if event_type == "rubric_evaluation_end":
        data = {"evaluation": rubric_evaluation(attempt=3), "max_attempts": 2}

    with pytest.raises(ValidationError, match="envelope attempt cannot exceed max_attempts"):
        parse_run_event(envelope(event_type, data, attempt=3))


def test_rubric_end_evaluation_attempt_matches_envelope() -> None:
    valid = parse_run_event(
        envelope(
            "rubric_evaluation_end",
            {"evaluation": rubric_evaluation(attempt=2), "max_attempts": 3},
            attempt=2,
        )
    )
    assert isinstance(valid, RubricEndEvent)

    with pytest.raises(ValidationError, match="evaluation attempt must equal envelope attempt"):
        parse_run_event(
            envelope(
                "rubric_evaluation_end",
                {"evaluation": rubric_evaluation(attempt=1), "max_attempts": 3},
                attempt=2,
            )
        )


@pytest.mark.parametrize(
    ("envelope_attempt", "previous_attempt", "new_attempt", "max_attempts"),
    [(1, 1, 2, 3), (2, 1, 3, 3), (2, 1, 2, 1)],
)
def test_revision_attempts_are_consistent(
    envelope_attempt: int, previous_attempt: int, new_attempt: int, max_attempts: int
) -> None:
    with pytest.raises(ValidationError):
        parse_run_event(
            envelope(
                "response_revision_start",
                {
                    "previous_attempt": previous_attempt,
                    "new_attempt": new_attempt,
                    "max_attempts": max_attempts,
                },
                attempt=envelope_attempt,
            )
        )


def test_revision_accepts_consecutive_attempt_matching_envelope() -> None:
    event = parse_run_event(
        envelope(
            "response_revision_start",
            {"previous_attempt": 1, "new_attempt": 2, "max_attempts": 3},
            attempt=2,
        )
    )
    assert isinstance(event, RevisionStartEvent)


def test_context_snapshot_attempt_matches_envelope() -> None:
    event = parse_run_event(
        envelope("context_snapshot", context_snapshot(attempt=2), attempt=2)
    )
    assert isinstance(event, ContextSnapshotEvent)
    assert event.data.model == "openai:gpt-5"

    with pytest.raises(ValidationError, match="snapshot attempt must equal envelope attempt"):
        parse_run_event(envelope("context_snapshot", context_snapshot(attempt=1), attempt=2))


def test_context_compressed_snapshot_attempts_match_envelope() -> None:
    event = parse_run_event(
        envelope(
            "context_compressed",
            {
                "before": context_snapshot(attempt=2),
                "after": context_snapshot(attempt=2),
                "status": "succeeded",
                "error": None,
            },
            attempt=2,
        )
    )

    assert isinstance(event, ContextCompressedEvent)


@pytest.mark.parametrize("mismatched_snapshot", ["before", "after"])
def test_context_compressed_rejects_snapshot_attempt_mismatch(
    mismatched_snapshot: str,
) -> None:
    data = {
        "before": context_snapshot(attempt=2),
        "after": context_snapshot(attempt=2),
        "status": "succeeded",
        "error": None,
    }
    data[mismatched_snapshot] = context_snapshot(attempt=1)

    with pytest.raises(ValidationError, match="compressed context attempts must equal envelope attempt"):
        parse_run_event(envelope("context_compressed", data, attempt=2))


@pytest.mark.parametrize(
    ("status", "error"),
    [("succeeded", "compression warning"), ("failed", None), ("failed", "   ")],
)
def test_context_compressed_rejects_inconsistent_error(status: str, error: str | None) -> None:
    with pytest.raises(ValidationError):
        parse_run_event(
            envelope(
                "context_compressed",
                {
                    "before": context_snapshot(),
                    "after": context_snapshot(),
                    "status": status,
                    "error": error,
                },
            )
        )


@pytest.mark.parametrize(("status", "error"), [("succeeded", None), ("failed", "Too large")])
def test_context_compressed_accepts_consistent_error(status: str, error: str | None) -> None:
    parse_run_event(
        envelope(
            "context_compressed",
            {
                "before": context_snapshot(),
                "after": context_snapshot(),
                "status": status,
                "error": error,
            },
        )
    )


def test_tool_payloads_are_recursively_frozen_and_detached_from_input() -> None:
    arguments: dict[str, Any] = {"options": {"tags": ["a", {"enabled": True}]}}
    event = parse_run_event(
        envelope(
            "tool_input_end",
            {"block_id": "block-1", "tool_call_id": "tool-1", "arguments": arguments},
        )
    )
    assert isinstance(event, ToolInputEndEvent)
    nested = event.data.arguments["options"]

    assert isinstance(event.data.arguments, Mapping)
    assert isinstance(nested, Mapping)
    assert isinstance(nested["tags"], tuple)
    with pytest.raises(TypeError):
        operator.setitem(cast(MutableMapping[str, object], event.data.arguments), "new", True)
    with pytest.raises(TypeError):
        operator.setitem(cast(MutableMapping[str, object], nested), "new", True)

    arguments["options"]["tags"].append("mutated")
    assert nested["tags"] == ("a", {"enabled": True})


def test_tool_result_content_is_recursively_frozen() -> None:
    event = parse_run_event(
        envelope(
            "tool_result",
            {
                "block_id": "block-1",
                "tool_call_id": "tool-1",
                "name": "search",
                "status": "completed",
                "content": {"items": [{"id": 1}]},
            },
        )
    )
    assert isinstance(event, ToolResultEvent)

    assert isinstance(event.data.content, Mapping)
    items = event.data.content["items"]
    assert isinstance(items, tuple)
    first_item = items[0]
    assert isinstance(first_item, Mapping)
    with pytest.raises(TypeError):
        operator.setitem(cast(MutableMapping[str, object], first_item), "id", 2)


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        (
            "tool_input_end",
            {"block_id": "block-1", "tool_call_id": "tool-1", "arguments": {"bad": object()}},
        ),
        (
            "tool_result",
            {
                "block_id": "block-1",
                "tool_call_id": "tool-1",
                "name": "search",
                "status": "completed",
                "content": object(),
            },
        ),
    ],
)
def test_tool_payloads_reject_non_json_values(event_type: str, data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="JSON-compatible"):
        parse_run_event(envelope(event_type, data))


def test_tool_end_arguments_requires_json_object_shape() -> None:
    with pytest.raises(ValidationError, match="JSON-compatible object"):
        parse_run_event(
            envelope(
                "tool_input_end",
                {"block_id": "block-1", "tool_call_id": "tool-1", "arguments": [1, 2]},
            )
        )


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        (
            "tool_input_end",
            {
                "block_id": "block-1",
                "tool_call_id": "tool-1",
                "arguments": {"options": {"tags": ["a", "b"]}},
            },
        ),
        (
            "tool_result",
            {
                "block_id": "block-1",
                "tool_call_id": "tool-1",
                "name": "search",
                "status": "completed",
                "content": {"items": [1, 2]},
            },
        ),
    ],
)
def test_tool_payload_json_roundtrip(event_type: str, data: dict[str, Any]) -> None:
    event = parse_run_event(envelope(event_type, data))
    serialized = json.loads(event.model_dump_json())

    assert serialized["data"] == data
    assert parse_run_event(serialized) == event


def test_tool_event_json_schema_describes_object_arguments_and_json_content() -> None:
    arguments_schema = ToolEndData.model_json_schema()["properties"]["arguments"]
    content_schema = ToolResultData.model_json_schema()["properties"]["content"]

    assert arguments_schema["type"] == "object"
    assert arguments_schema["additionalProperties"] == {"$ref": "#/$defs/JsonValue"}
    assert content_schema["$ref"] == "#/$defs/JsonValue"


@pytest.mark.parametrize(
    ("run_id", "session_id", "attempt"),
    [("other-run", "session-1", 1), ("run-1", "other-session", 1), ("run-1", "session-1", 2)],
)
def test_done_result_identity_matches_envelope(run_id: str, session_id: str, attempt: int) -> None:
    with pytest.raises(ValidationError, match="result .* must match envelope"):
        parse_run_event(envelope("done", {"result": run_result(attempt=attempt, run_id=run_id, session_id=session_id)}))


def test_done_accepts_matching_result() -> None:
    event = parse_run_event(envelope("done", {"result": run_result()}))
    assert isinstance(event, DoneEvent)


def test_done_rejects_provisional_result_without_persisted_at() -> None:
    with pytest.raises(ValidationError, match="done result must be persisted"):
        parse_run_event(envelope("done", {"result": run_result(persisted_at=None)}))


def test_error_accepts_provisional_result_without_persisted_at() -> None:
    event = parse_run_event(
        envelope(
            "error",
            {
                "code": "provider_error",
                "message": "Provider failed",
                "retryable": True,
                "result": run_result(persisted_at=None),
            },
        )
    )

    assert isinstance(event, ErrorEvent)
    assert event.data.result is not None
    assert event.data.result.persisted_at is None


@pytest.mark.parametrize(("run_id", "session_id"), [("other-run", "session-1"), ("run-1", "other-session")])
def test_error_result_ids_match_envelope(run_id: str, session_id: str) -> None:
    with pytest.raises(ValidationError, match="result .* must match envelope"):
        parse_run_event(
            envelope(
                "error",
                {
                    "code": "provider_error",
                    "message": "Provider failed",
                    "retryable": True,
                    "result": run_result(run_id=run_id, session_id=session_id),
                },
            )
        )


def test_error_result_attempt_matches_envelope() -> None:
    with pytest.raises(ValidationError, match="result attempt must match envelope"):
        parse_run_event(
            envelope(
                "error",
                {
                    "code": "provider_error",
                    "message": "Provider failed",
                    "retryable": False,
                    "result": run_result(attempt=2),
                },
            )
        )


def test_error_accepts_no_result() -> None:
    event = parse_run_event(
        envelope(
            "error",
            {"code": "provider_error", "message": "Provider failed", "retryable": True, "result": None},
        )
    )
    assert isinstance(event, ErrorEvent)


def test_event_json_roundtrip() -> None:
    event = parse_run_event(envelope("text_delta", {"block_id": "block-1", "delta": "Hi"}))

    assert parse_run_event(json.loads(event.model_dump_json())) == event


def _emit_recorder():
    emitted = []

    def emit(event_cls, data, attempt=1):
        emitted.append((event_cls, data, attempt))
        return parse_run_event(_envelope(event_cls, data, attempt))
    return emit, emitted


def _envelope(event_cls, data, attempt=1):
    return {
        "schema_version": 1,
        "event_id": "event-1",
        "sequence": 1,
        "timestamp": NOW,
        "session_id": "session-1",
        "run_id": "run-1",
        "attempt": attempt,
        "type": event_cls.model_fields["type"].default,
        "data": data,
    }


@pytest.mark.parametrize(
    "chunk_type",
    ["tool_end", "rubric_evaluation_start", "rubric_evaluation_end", "done", "error"],
)
def test_stream_chunk_drops_unmapped_terminal_types(chunk_type: str) -> None:
    """Unknown/unmapped canonical types are dropped, never empty text deltas."""
    emit, emitted = _emit_recorder()
    chunk = StreamChunk(type=chunk_type, content="ignored")
    result = _stream_chunk_to_event(chunk, emit, attempt=1, model_id="openai:gpt-5")
    assert result is None
    assert emitted == []


def test_stream_chunk_usage_llm_call_index_is_running_counter() -> None:
    """Usage events carry a running llm_call_index, not a hardcoded 1."""
    emit, emitted = _emit_recorder()
    for index in (1, 2, 3):
        chunk = StreamChunk.usage_event(
            Usage(input_tokens=10 * index, output_tokens=5 * index)
        )
        _stream_chunk_to_event(
            chunk, emit, attempt=1, model_id="openai:gpt-5", llm_call_index=index
        )
    usage_events = [ev for ev in emitted if ev[0].__name__ == "UsageEvent"]
    assert len(usage_events) == 3
    indices = [ev[1]["llm_call_index"] for ev in usage_events]
    assert indices == [1, 2, 3]
