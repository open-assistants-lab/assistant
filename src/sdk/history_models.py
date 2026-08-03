"""Immutable canonical contracts for conversation turn history."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from src.sdk.run_models import ContractModel, NonEmptyString, RunResult, RunStatus

JSONScalar: TypeAlias = str | int | float | bool | None
FrozenJSONValue: TypeAlias = JSONScalar | tuple[object, ...] | Mapping[str, object]
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _freeze_json(value: object) -> FrozenJSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("value must be JSON-compatible")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError("value must be JSON-compatible")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class TurnMessage(ContractModel):
    id: NonEmptyString
    content: str
    timestamp: datetime | None = None

    @field_validator("timestamp", mode="before", json_schema_input_type=datetime | str | None)
    @classmethod
    def _timestamp_input(cls, value: object) -> object:
        if value is None or isinstance(value, datetime):
            return value
        if not isinstance(value, str) or _RFC3339_PATTERN.fullmatch(value) is None:
            raise ValueError("timestamp must be a datetime or RFC3339 string")
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
        except ValueError as exc:
            raise ValueError("timestamp must be a datetime or RFC3339 string") from exc

    @field_validator("timestamp")
    @classmethod
    def _timezone_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class TurnBlockBase(ContractModel):
    id: NonEmptyString
    attempt: int = Field(ge=1)
    sequence: int = Field(ge=1)


class ReasoningBlock(TurnBlockBase):
    type: Literal["reasoning"] = "reasoning"
    content: str


class ToolBlock(TurnBlockBase):
    type: Literal["tool"] = "tool"
    tool_call_id: NonEmptyString
    name: NonEmptyString
    status: Literal["completed", "failed", "cancelled"]
    arguments: Mapping[str, object]
    result: FrozenJSONValue

    @field_validator(
        "arguments", mode="plain", json_schema_input_type=dict[str, JsonValue]
    )
    @classmethod
    def _frozen_arguments(cls, value: object) -> Mapping[str, object]:
        frozen = _freeze_json(value)
        if not isinstance(frozen, Mapping):
            raise ValueError("arguments must be a JSON-compatible object")
        return frozen

    @field_validator("result", mode="plain", json_schema_input_type=JsonValue)
    @classmethod
    def _frozen_result(cls, value: object) -> FrozenJSONValue:
        return _freeze_json(value)

    @field_serializer("arguments", "result")
    def _serialize_json(self, value: object) -> object:
        return _thaw_json(value)


TurnBlock: TypeAlias = Annotated[ReasoningBlock | ToolBlock, Field(discriminator="type")]


class ConversationTurn(ContractModel):
    run_id: NonEmptyString
    status: RunStatus
    user: TurnMessage
    blocks: tuple[TurnBlock, ...]
    answer: TurnMessage | None = None
    result: RunResult | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> ConversationTurn:
        if any(
            current.sequence <= previous.sequence
            for previous, current in zip(self.blocks, self.blocks[1:])
        ):
            raise ValueError("block sequence values must be strictly increasing")
        if any(
            current.attempt < previous.attempt
            for previous, current in zip(self.blocks, self.blocks[1:])
        ):
            raise ValueError("block attempts must be nondecreasing")
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block IDs must be unique")

        if self.status is RunStatus.COMPLETED and self.answer is None:
            raise ValueError("completed turn requires an answer")
        if self.answer is not None and self.answer.id == self.user.id:
            raise ValueError("user and answer IDs must differ")
        if (
            self.answer is not None
            and self.user.timestamp is not None
            and self.answer.timestamp is not None
            and self.answer.timestamp < self.user.timestamp
        ):
            raise ValueError("answer timestamp cannot precede user timestamp")

        if self.result is None:
            return self
        if any(block.attempt > self.result.attempt for block in self.blocks):
            raise ValueError("block attempts cannot exceed result attempt")
        if self.result.run_id != self.run_id:
            raise ValueError("result run_id must match turn")
        if self.result.status is not self.status:
            raise ValueError("result status must match turn")
        if self.answer is not None and self.answer.content != self.result.response:
            raise ValueError("answer content must match result response")
        if (
            self.result.final_message_id is not None
            and (self.answer is None or self.answer.id != self.result.final_message_id)
        ):
            raise ValueError("answer id must match result final_message_id")
        return self


class TurnsResponse(ContractModel):
    turns: tuple[ConversationTurn, ...]
    next_cursor: NonEmptyString | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> TurnsResponse:
        run_ids = [turn.run_id for turn in self.turns]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run_ids must be unique")
        message_ids = [
            message.id
            for turn in self.turns
            for message in (turn.user, turn.answer)
            if message is not None
        ]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("message IDs must be unique across turns")
        return self
