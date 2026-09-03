"""Canonical versioned event contracts for agent runs."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Generic, Literal, TypeAlias, TypeVar

from pydantic import (
    Field,
    JsonValue,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from src.sdk.run_models import (
    CanonicalModel,
    ContextSnapshot,
    ContractModel,
    NonEmptyString,
    RubricEvaluation,
    RunResult,
    UsageCategory,
)

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


class BlockData(ContractModel):
    block_id: NonEmptyString


class BlockDeltaData(BlockData):
    delta: str


class ToolStartData(BlockData):
    tool_call_id: NonEmptyString
    name: NonEmptyString


class ToolDeltaData(BlockData):
    tool_call_id: NonEmptyString
    delta: str


class ToolEndData(BlockData):
    tool_call_id: NonEmptyString
    arguments: Mapping[str, object]

    @field_validator(
        "arguments", mode="plain", json_schema_input_type=dict[str, JsonValue]
    )
    @classmethod
    def _frozen_arguments(cls, value: object) -> Mapping[str, object]:
        frozen = _freeze_json(value)
        if not isinstance(frozen, Mapping):
            raise ValueError("arguments must be a JSON-compatible object")
        return frozen

    @field_serializer("arguments")
    def _serialize_arguments(self, value: Mapping[str, object]) -> object:
        return _thaw_json(value)


class ToolResultData(BlockData):
    tool_call_id: NonEmptyString
    name: NonEmptyString
    status: Literal["completed", "failed", "cancelled"]
    content: FrozenJSONValue

    @field_validator("content", mode="plain", json_schema_input_type=JsonValue)
    @classmethod
    def _frozen_content(cls, value: object) -> FrozenJSONValue:
        return _freeze_json(value)

    @field_serializer("content")
    def _serialize_content(self, value: FrozenJSONValue) -> object:
        return _thaw_json(value)


class SingleCallUsage(ContractModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)


class UsageEventData(ContractModel):
    category: UsageCategory
    model: CanonicalModel
    llm_call_index: int = Field(ge=1)
    usage: SingleCallUsage


class RubricStartData(ContractModel):
    grading_run_id: NonEmptyString
    max_attempts: int = Field(ge=1, le=3)


class RubricEndData(ContractModel):
    evaluation: RubricEvaluation
    max_attempts: int = Field(ge=1, le=3)


class RevisionStartData(ContractModel):
    previous_attempt: int = Field(ge=1)
    new_attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=3)


class ContextCompressedData(ContractModel):
    before: ContextSnapshot
    after: ContextSnapshot
    status: Literal["succeeded", "failed"]
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_error(self) -> ContextCompressedData:
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("succeeded compression must not have an error")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed compression requires a nonempty error")
        return self


class DoneData(ContractModel):
    result: RunResult


class ErrorData(ContractModel):
    code: NonEmptyString
    message: str
    retryable: bool
    result: RunResult | None = None


class UserPromptData(ContractModel):
    content: NonEmptyString


class SystemPromptData(ContractModel):
    content: NonEmptyString


class InjectionData(ContractModel):
    kind: Literal["steer", "inject", "supervisor"]
    content: NonEmptyString


class InterruptData(ContractModel):
    tool: NonEmptyString
    call_id: NonEmptyString
    args: Mapping[str, object] = Field(default_factory=dict)


EventDataT = TypeVar("EventDataT", bound=ContractModel)


class RunEventBase(ContractModel, Generic[EventDataT]):
    schema_version: Literal[1] = 1
    event_id: NonEmptyString
    sequence: int = Field(ge=1)
    timestamp: datetime
    session_id: NonEmptyString
    run_id: NonEmptyString
    attempt: int = Field(ge=1)
    data: EventDataT

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp_input(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or _RFC3339_PATTERN.fullmatch(value) is None:
            raise ValueError("timestamp must be a datetime or RFC3339 string")
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
        except ValueError as exc:
            raise ValueError("timestamp must be a datetime or RFC3339 string") from exc

    @field_validator("timestamp")
    @classmethod
    def _timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class TextStartEvent(RunEventBase[BlockData]):
    type: Literal["text_start"] = "text_start"


class TextDeltaEvent(RunEventBase[BlockDeltaData]):
    type: Literal["text_delta"] = "text_delta"


class TextEndEvent(RunEventBase[BlockData]):
    type: Literal["text_end"] = "text_end"


class ReasoningStartEvent(RunEventBase[BlockData]):
    type: Literal["reasoning_start"] = "reasoning_start"


class ReasoningDeltaEvent(RunEventBase[BlockDeltaData]):
    type: Literal["reasoning_delta"] = "reasoning_delta"


class ReasoningEndEvent(RunEventBase[BlockData]):
    type: Literal["reasoning_end"] = "reasoning_end"


class ToolInputStartEvent(RunEventBase[ToolStartData]):
    type: Literal["tool_input_start"] = "tool_input_start"


class ToolInputDeltaEvent(RunEventBase[ToolDeltaData]):
    type: Literal["tool_input_delta"] = "tool_input_delta"


class ToolInputEndEvent(RunEventBase[ToolEndData]):
    type: Literal["tool_input_end"] = "tool_input_end"


class ToolResultEvent(RunEventBase[ToolResultData]):
    type: Literal["tool_result"] = "tool_result"


class UsageEvent(RunEventBase[UsageEventData]):
    type: Literal["usage"] = "usage"


class RubricStartEvent(RunEventBase[RubricStartData]):
    type: Literal["rubric_evaluation_start"] = "rubric_evaluation_start"

    @model_validator(mode="after")
    def _attempt_within_limit(self) -> RubricStartEvent:
        if self.attempt > self.data.max_attempts:
            raise ValueError("envelope attempt cannot exceed max_attempts")
        return self


class RubricEndEvent(RunEventBase[RubricEndData]):
    type: Literal["rubric_evaluation_end"] = "rubric_evaluation_end"

    @model_validator(mode="after")
    def _evaluation_matches_envelope(self) -> RubricEndEvent:
        if self.data.evaluation.attempt != self.attempt:
            raise ValueError("evaluation attempt must equal envelope attempt")
        if self.attempt > self.data.max_attempts:
            raise ValueError("envelope attempt cannot exceed max_attempts")
        return self


class RevisionStartEvent(RunEventBase[RevisionStartData]):
    type: Literal["response_revision_start"] = "response_revision_start"

    @model_validator(mode="after")
    def _attempts_are_consistent(self) -> RevisionStartEvent:
        if self.attempt != self.data.new_attempt:
            raise ValueError("envelope attempt must equal new_attempt")
        if self.data.new_attempt != self.data.previous_attempt + 1:
            raise ValueError("new_attempt must immediately follow previous_attempt")
        if self.data.new_attempt > self.data.max_attempts:
            raise ValueError("new_attempt cannot exceed max_attempts")
        return self


class ContextSnapshotEvent(RunEventBase[ContextSnapshot]):
    type: Literal["context_snapshot"] = "context_snapshot"

    @model_validator(mode="after")
    def _snapshot_matches_envelope(self) -> ContextSnapshotEvent:
        if self.data.attempt != self.attempt:
            raise ValueError("snapshot attempt must equal envelope attempt")
        return self


class ContextCompressedEvent(RunEventBase[ContextCompressedData]):
    type: Literal["context_compressed"] = "context_compressed"

    @model_validator(mode="after")
    def _snapshots_match_envelope(self) -> ContextCompressedEvent:
        if self.data.before.attempt != self.attempt or self.data.after.attempt != self.attempt:
            raise ValueError("compressed context attempts must equal envelope attempt")
        return self


class DoneEvent(RunEventBase[DoneData]):
    type: Literal["done"] = "done"

    @model_validator(mode="after")
    def _result_matches_envelope(self) -> DoneEvent:
        if self.data.result.run_id != self.run_id:
            raise ValueError("result run_id must match envelope")
        if self.data.result.session_id != self.session_id:
            raise ValueError("result session_id must match envelope")
        if self.data.result.attempt != self.attempt:
            raise ValueError("result attempt must match envelope")
        if self.data.result.persisted_at is None:
            raise ValueError("done result must be persisted")
        return self


class ErrorEvent(RunEventBase[ErrorData]):
    type: Literal["error"] = "error"

    @model_validator(mode="after")
    def _result_matches_envelope(self) -> ErrorEvent:
        if self.data.result is None:
            return self
        if self.data.result.run_id != self.run_id:
            raise ValueError("result run_id must match envelope")
        if self.data.result.session_id != self.session_id:
            raise ValueError("result session_id must match envelope")
        if self.data.result.attempt != self.attempt:
            raise ValueError("result attempt must match envelope")
        return self


class InterruptEvent(RunEventBase[InterruptData]):
    type: Literal["interrupt"] = "interrupt"


class UserPromptEvent(RunEventBase[UserPromptData]):
    type: Literal["user_prompt"] = "user_prompt"


class SystemPromptEvent(RunEventBase[SystemPromptData]):
    type: Literal["system_prompt"] = "system_prompt"


class InjectionEvent(RunEventBase[InjectionData]):
    type: Literal["injection"] = "injection"


RunEvent = Annotated[
    TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ReasoningStartEvent
    | ReasoningDeltaEvent
    | ReasoningEndEvent
    | ToolInputStartEvent
    | ToolInputDeltaEvent
    | ToolInputEndEvent
    | ToolResultEvent
    | UsageEvent
    | RubricStartEvent
    | RubricEndEvent
    | RevisionStartEvent
    | ContextSnapshotEvent
    | ContextCompressedEvent
    | DoneEvent
    | ErrorEvent
    | InterruptEvent
    | UserPromptEvent
    | SystemPromptEvent
    | InjectionEvent,
    Field(discriminator="type"),
]

_RUN_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)


def parse_run_event(data: object) -> RunEvent:
    """Parse and validate one canonical run event."""
    return _RUN_EVENT_ADAPTER.validate_python(data)
