"""Canonical versioned event contracts for agent runs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import Field, TypeAdapter, field_validator, model_validator

from src.sdk.run_models import (
    ContextSnapshot,
    ContractModel,
    NonEmptyString,
    RubricEvaluation,
    RunResult,
    UsageCategory,
    _validate_canonical_model,
)


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
    arguments: dict[str, Any]


class ToolResultData(BlockData):
    tool_call_id: NonEmptyString
    name: NonEmptyString
    status: Literal["completed", "failed", "cancelled"]
    content: str


class UsageEventData(ContractModel):
    category: UsageCategory
    model: str
    llm_call_index: int = Field(ge=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)

    @field_validator("model")
    @classmethod
    def _canonical_model(cls, value: str) -> str:
        return _validate_canonical_model(value)


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
    error: str | None = None


class DoneData(ContractModel):
    result: RunResult


class ErrorData(ContractModel):
    code: NonEmptyString
    message: str
    retryable: bool
    result: RunResult | None = None


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

    @field_validator("timestamp")
    @classmethod
    def _timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


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


class RubricEndEvent(RunEventBase[RubricEndData]):
    type: Literal["rubric_evaluation_end"] = "rubric_evaluation_end"

    @model_validator(mode="after")
    def _evaluation_matches_envelope(self) -> RubricEndEvent:
        if self.data.evaluation.attempt != self.attempt:
            raise ValueError("evaluation attempt must equal envelope attempt")
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
        return self


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
    | ErrorEvent,
    Field(discriminator="type"),
]

_RUN_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)


def parse_run_event(data: object) -> RunEvent:
    """Parse and validate one canonical run event."""
    return _RUN_EVENT_ADAPTER.validate_python(data)
