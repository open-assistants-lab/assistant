"""Tests for immutable conversation turn history contracts."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.sdk.history_models import (
    ConversationTurn,
    ReasoningBlock,
    ToolBlock,
    TurnMessage,
    TurnsResponse,
)
from src.sdk.run_models import RunStatus

_UNSET = object()


def run_result(
    *,
    run_id: str = "run-1",
    status: str = "completed",
    attempt: int = 2,
    response: str = "Final answer",
    final_message_id: str | None = "answer-1",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "session_id": "session-1",
        "status": status,
        "attempt": attempt,
        "model": "openai:gpt-5",
        "response": response,
        "final_message_id": final_message_id,
        "usage": {"agent": {"available": True, "calls": 1, "models": ["openai:gpt-5"]}},
        "verification": {"availability": "off"},
    }


def turn_payload(
    *,
    run_id: str = "run-1",
    status: str = "completed",
    blocks: list[dict[str, Any]] | None = None,
    answer: dict[str, Any] | None = None,
    result: object = _UNSET,
) -> dict[str, Any]:
    if blocks is None:
        blocks = [
            {
                "type": "reasoning",
                "id": "reasoning-1",
                "attempt": 1,
                "sequence": 1,
                "content": "Considering the request.",
            },
            {
                "type": "tool",
                "id": "tool-1",
                "attempt": 2,
                "sequence": 2,
                "tool_call_id": "call-1",
                "name": "time_get",
                "status": "completed",
                "arguments": {"zone": "UTC"},
                "result": {"hour": 12},
            },
        ]
    if answer is None and status == "completed":
        answer = {"id": "answer-1", "content": "Final answer", "timestamp": None}
    if result is _UNSET and status == "completed":
        result = run_result(run_id=run_id, status=status)
    elif result is _UNSET:
        result = None
    return {
        "run_id": run_id,
        "status": status,
        "user": {"id": "user-1", "content": "What time is it?", "timestamp": None},
        "blocks": blocks,
        "answer": answer,
        "result": result,
    }


def test_parses_complete_ordered_turn_with_typed_blocks_and_status() -> None:
    turn = ConversationTurn.model_validate(turn_payload())

    assert turn.status is RunStatus.COMPLETED
    assert isinstance(turn.blocks[0], ReasoningBlock)
    assert isinstance(turn.blocks[1], ToolBlock)
    assert turn.blocks[0].content == "Considering the request."


def test_cancelled_turn_may_omit_answer() -> None:
    turn = ConversationTurn.model_validate(
        turn_payload(status="cancelled", answer=None, result=None)
    )

    assert turn.status is RunStatus.CANCELLED
    assert turn.answer is None


def test_failed_turn_may_omit_answer() -> None:
    turn = ConversationTurn.model_validate(
        turn_payload(
            status="failed",
            answer=None,
            result=run_result(status="failed", response="", final_message_id=None),
        )
    )

    assert turn.status is RunStatus.FAILED
    assert turn.answer is None


def test_completed_turn_may_have_answer_without_result() -> None:
    turn = ConversationTurn.model_validate(turn_payload(result=None))

    assert turn.status is RunStatus.COMPLETED
    assert turn.answer is not None
    assert turn.result is None


def test_completed_turn_requires_answer() -> None:
    payload = turn_payload()
    payload["answer"] = None

    with pytest.raises(ValidationError, match="completed turn requires an answer"):
        ConversationTurn.model_validate(payload)


@pytest.mark.parametrize(
    "blocks, message",
    [
        (
            [
                {"type": "reasoning", "id": "b1", "attempt": 1, "sequence": 2, "content": "a"},
                {"type": "reasoning", "id": "b2", "attempt": 1, "sequence": 1, "content": "b"},
            ],
            "strictly increasing",
        ),
        (
            [
                {"type": "reasoning", "id": "b1", "attempt": 1, "sequence": 1, "content": "a"},
                {"type": "reasoning", "id": "b2", "attempt": 1, "sequence": 1, "content": "b"},
            ],
            "strictly increasing",
        ),
        (
            [
                {"type": "reasoning", "id": "b1", "attempt": 1, "sequence": 1, "content": "a"},
                {"type": "reasoning", "id": "b1", "attempt": 1, "sequence": 2, "content": "b"},
            ],
            "block IDs must be unique",
        ),
    ],
)
def test_rejects_invalid_block_order_or_duplicate_ids(
    blocks: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ConversationTurn.model_validate(turn_payload(blocks=blocks))


@pytest.mark.parametrize(
    "result_changes, answer_changes, message",
    [
        ({"run_id": "other-run"}, {}, "result run_id must match"),
        ({"status": "failed"}, {}, "result status must match"),
        ({"response": "Other answer"}, {}, "answer content must match"),
        ({"final_message_id": "other-answer"}, {}, "answer id must match"),
    ],
)
def test_rejects_mismatched_result_and_answer(
    result_changes: dict[str, Any], answer_changes: dict[str, Any], message: str
) -> None:
    result = run_result()
    result.update(result_changes)
    answer = {"id": "answer-1", "content": "Final answer", "timestamp": None}
    answer.update(answer_changes)

    with pytest.raises(ValidationError, match=message):
        ConversationTurn.model_validate(turn_payload(result=result, answer=answer))


def test_result_without_final_message_id_allows_answer() -> None:
    turn = ConversationTurn.model_validate(
        turn_payload(result=run_result(final_message_id=None))
    )

    assert turn.answer is not None


def test_rejects_block_attempt_beyond_result_attempt() -> None:
    with pytest.raises(ValidationError, match="block attempts cannot exceed result attempt"):
        ConversationTurn.model_validate(turn_payload(result=run_result(attempt=1)))


def test_rejects_decreasing_block_attempts_in_sequence_order() -> None:
    blocks = [
        {"type": "reasoning", "id": "b1", "attempt": 2, "sequence": 1, "content": "a"},
        {"type": "reasoning", "id": "b2", "attempt": 1, "sequence": 2, "content": "b"},
    ]

    with pytest.raises(ValidationError, match="block attempts must be nondecreasing"):
        ConversationTurn.model_validate(turn_payload(blocks=blocks))


def test_tool_json_is_recursively_immutable_and_serializes_as_json() -> None:
    arguments: dict[str, Any] = {"filters": [{"active": True}]}
    result: dict[str, Any] = {"items": [1, {"name": "Ada"}]}
    block = ToolBlock(
        id="tool-1",
        attempt=1,
        sequence=1,
        tool_call_id="call-1",
        name="contacts_search",
        status="completed",
        arguments=arguments,
        result=result,
    )
    arguments["filters"][0]["active"] = False
    result["items"][1]["name"] = "Grace"

    filters = block.arguments["filters"]
    assert isinstance(filters, tuple)
    assert isinstance(filters[0], Mapping)
    assert filters[0]["active"] is True
    assert isinstance(block.result, Mapping)
    items = block.result["items"]
    assert isinstance(items, tuple)
    assert isinstance(items[1], Mapping)
    assert items[1]["name"] == "Ada"
    with pytest.raises(TypeError):
        block.arguments["new"] = "value"  # type: ignore[index]

    dumped = json.loads(block.model_dump_json())
    assert dumped["arguments"] == {"filters": [{"active": True}]}
    assert dumped["result"] == {"items": [1, {"name": "Ada"}]}
    assert ToolBlock.model_validate_json(block.model_dump_json()) == block


@pytest.mark.parametrize("field", ["arguments", "result"])
def test_tool_rejects_non_json_values(field: str) -> None:
    values: dict[str, Any] = {
        "id": "tool-1",
        "attempt": 1,
        "sequence": 1,
        "tool_call_id": "call-1",
        "name": "time_get",
        "status": "completed",
        "arguments": {},
        "result": None,
    }
    values[field] = object()

    with pytest.raises(ValidationError, match="JSON-compatible"):
        ToolBlock(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("arguments", {"value": float("nan")}),
        ("result", float("inf")),
        ("result", [float("-inf")]),
    ],
)
def test_tool_rejects_nonfinite_json_floats(field: str, value: object) -> None:
    values: dict[str, Any] = {
        "id": "tool-1",
        "attempt": 1,
        "sequence": 1,
        "tool_call_id": "call-1",
        "name": "time_get",
        "status": "completed",
        "arguments": {},
        "result": None,
    }
    values[field] = value

    with pytest.raises(ValidationError, match="JSON-compatible"):
        ToolBlock(**values)


def test_tool_json_schema_describes_object_arguments_and_json_result() -> None:
    schema = ToolBlock.model_json_schema()
    arguments_schema = schema["properties"]["arguments"]
    result_schema = schema["properties"]["result"]

    assert arguments_schema["type"] == "object"
    assert arguments_schema["additionalProperties"] == {"$ref": "#/$defs/JsonValue"}
    assert result_schema["$ref"] == "#/$defs/JsonValue"
    assert schema["$defs"]["JsonValue"] == {}


def test_turn_message_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TurnMessage(id="message-1", content="Hello", timestamp=datetime(2026, 8, 2, 12))


@pytest.mark.parametrize("timestamp", [0, 1.5, "2026-08-02"])
def test_turn_message_rejects_non_rfc3339_timestamp_inputs(timestamp: object) -> None:
    with pytest.raises(ValidationError, match="datetime or RFC3339 string"):
        TurnMessage.model_validate({"id": "message-1", "content": "Hello", "timestamp": timestamp})


def test_turn_message_normalizes_offset_timestamp_to_utc() -> None:
    timestamp = datetime(2026, 8, 2, 8, tzinfo=timezone(timedelta(hours=-4)))

    message = TurnMessage(id="message-1", content="Hello", timestamp=timestamp)

    assert message.timestamp == datetime(2026, 8, 2, 12, tzinfo=UTC)
    assert message.timestamp.tzinfo is UTC


def test_turn_message_normalizes_rfc3339_timestamp_to_utc() -> None:
    message = TurnMessage.model_validate(
        {"id": "message-1", "content": "Hello", "timestamp": "2026-08-02T08:00:00-04:00"}
    )

    assert message.timestamp == datetime(2026, 8, 2, 12, tzinfo=UTC)
    assert message.timestamp.tzinfo is UTC


def test_turn_rejects_matching_user_and_answer_ids() -> None:
    payload = turn_payload()
    payload["answer"] = {"id": "user-1", "content": "Final answer", "timestamp": None}

    with pytest.raises(ValidationError, match="user and answer IDs must differ"):
        ConversationTurn.model_validate(payload)


def test_turn_rejects_answer_timestamp_before_user_timestamp() -> None:
    payload = turn_payload(result=None)
    payload["user"]["timestamp"] = "2026-08-02T12:00:00Z"
    payload["answer"]["timestamp"] = "2026-08-02T11:59:59Z"

    with pytest.raises(ValidationError, match="answer timestamp cannot precede user timestamp"):
        ConversationTurn.model_validate(payload)


def test_turns_response_rejects_duplicate_run_ids() -> None:
    with pytest.raises(ValidationError, match="run_ids must be unique"):
        TurnsResponse.model_validate(
            {"turns": [turn_payload(), turn_payload()], "next_cursor": None}
        )


@pytest.mark.parametrize("duplicate_role", ["user", "answer"])
def test_turns_response_rejects_duplicate_message_ids_across_turns(
    duplicate_role: str,
) -> None:
    first = turn_payload()
    second = turn_payload(run_id="run-2")
    second["user"]["id"] = "user-2"
    second["answer"]["id"] = "answer-2"
    second["result"]["final_message_id"] = "answer-2"
    second[duplicate_role]["id"] = first[duplicate_role]["id"]
    if duplicate_role == "answer":
        second["result"]["final_message_id"] = first["answer"]["id"]

    with pytest.raises(ValidationError, match="message IDs must be unique"):
        TurnsResponse.model_validate({"turns": [first, second], "next_cursor": None})


@pytest.mark.parametrize("cursor", ["", "   "])
def test_turns_response_rejects_blank_next_cursor(cursor: str) -> None:
    with pytest.raises(ValidationError):
        TurnsResponse(turns=(), next_cursor=cursor)


def test_turns_response_json_roundtrip_preserves_canonical_contract() -> None:
    response = TurnsResponse.model_validate(
        {"turns": [turn_payload()], "next_cursor": "cursor-2"}
    )

    restored = TurnsResponse.model_validate_json(response.model_dump_json())

    assert restored == response
    assert isinstance(restored.turns[0].blocks[0], ReasoningBlock)


def test_contracts_are_immutable_and_require_nonempty_ids() -> None:
    message = TurnMessage(id="message-1", content="Hello", timestamp=None)

    with pytest.raises(ValidationError):
        message.content = "Changed"
    with pytest.raises(ValidationError):
        TurnMessage(id=" ", content="Hello", timestamp=None)
