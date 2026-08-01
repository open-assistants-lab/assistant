"""Tests for the canonical versioned run event envelope."""

import json
import operator
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from src.sdk.run_events import (
    BlockDeltaData,
    ContextCompressedEvent,
    ContextSnapshotEvent,
    DoneEvent,
    ErrorEvent,
    RevisionStartEvent,
    RubricEndEvent,
    RunEvent,
    TextDeltaEvent,
    ToolInputEndEvent,
    ToolResultEvent,
    UsageEvent,
    parse_run_event,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


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


def run_result(*, attempt: int = 1, run_id: str = "run-1", session_id: str = "session-1") -> dict[str, Any]:
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
    }


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
                "input_tokens": 2,
                "output_tokens": 3,
            },
        )
    )
    assert isinstance(event, UsageEvent)
    assert event.data.model == "openai:gpt-5"

    payload = envelope(
        "usage",
        {"category": "agent", "model": "openai:gpt-5", "llm_call_index": 1, "input_tokens": -1},
    )
    with pytest.raises(ValidationError):
        parse_run_event(payload)


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
