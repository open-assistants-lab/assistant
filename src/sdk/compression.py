"""Typed contracts for lossless conversation context compression."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Any, cast

from pydantic import Field, StringConstraints, computed_field, field_validator, model_validator

from src.sdk.messages import Message, Role, ToolCall, Usage
from src.sdk.run_models import CanonicalModel, ContextSnapshot, ContractModel, UsageAggregate

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CompressionReason(StrEnum):
    THRESHOLD = "threshold"
    PROVIDER_OVERFLOW = "provider_overflow"
    MANUAL = "manual"


class CompressionStatus(StrEnum):
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PersistenceStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CompressionContext(ContractModel):
    session_id: NonEmptyString
    model: CanonicalModel
    attempt: int = Field(ge=1)
    llm_call_index: int = Field(ge=1)
    reason: CompressionReason
    before: ContextSnapshot | None = None

    @model_validator(mode="after")
    def _validate_snapshot_identity(self) -> CompressionContext:
        if self.before is not None and (
            self.before.model != self.model
            or self.before.attempt != self.attempt
            or self.before.llm_call_index != self.llm_call_index
        ):
            raise ValueError("before snapshot identity must match compression context")
        return self


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class CompressionToolCall(ContractModel):
    """Deeply owned immutable snapshot of a tool call."""

    id: str
    name: str
    arguments_json: str

    @field_validator("arguments_json")
    @classmethod
    def _validate_arguments_json(cls, value: str) -> str:
        if not isinstance(json.loads(value), dict):
            raise ValueError("tool arguments must encode a JSON object")
        return value

    @classmethod
    def from_tool_call(cls, tool_call: ToolCall) -> CompressionToolCall:
        return cls(
            id=tool_call.id,
            name=tool_call.name,
            arguments_json=_dump_json(tool_call.arguments),
        )

    def to_tool_call(self) -> ToolCall:
        return ToolCall(id=self.id, name=self.name, arguments=json.loads(self.arguments_json))

    @property
    def arguments(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.arguments_json))


class CompressionMessage(ContractModel):
    """Deeply owned immutable snapshot that materializes fresh SDK messages."""

    role: Role
    content_json: str
    tool_calls: tuple[CompressionToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    reasoning: str | None = None
    provider_metadata_json: str = "{}"
    usage_json: str | None = None
    storage_id: str | None = None
    source: str | None = None

    @field_validator("content_json")
    @classmethod
    def _validate_content_json(cls, value: str) -> str:
        content = json.loads(value)
        if not isinstance(content, (str, list)):
            raise ValueError("message content must encode a string or list")
        return value

    @field_validator("provider_metadata_json")
    @classmethod
    def _validate_provider_metadata_json(cls, value: str) -> str:
        if not isinstance(json.loads(value), dict):
            raise ValueError("provider metadata must encode a JSON object")
        return value

    @field_validator("usage_json")
    @classmethod
    def _validate_usage_json(cls, value: str | None) -> str | None:
        if value is not None:
            Usage.model_validate_json(value)
        return value

    @classmethod
    def from_message(cls, message: Message) -> CompressionMessage:
        usage = message.usage.model_dump(mode="json") if message.usage is not None else None
        return cls(
            role=message.role,
            content_json=_dump_json(message.content),
            tool_calls=tuple(CompressionToolCall.from_tool_call(call) for call in message.tool_calls),
            tool_call_id=message.tool_call_id,
            name=message.name,
            reasoning=message.reasoning,
            provider_metadata_json=_dump_json(message.provider_metadata),
            usage_json=_dump_json(usage) if usage is not None else None,
            storage_id=getattr(message, "storage_id", None),
            source=getattr(message, "source", None),
        )

    def to_message(self) -> Message:
        data: dict[str, Any] = {
            "role": self.role,
            "content": json.loads(self.content_json),
            "tool_calls": [call.to_tool_call() for call in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "reasoning": self.reasoning,
            "provider_metadata": json.loads(self.provider_metadata_json),
            "usage": Usage.model_validate_json(self.usage_json) if self.usage_json else None,
        }
        if self.storage_id is not None:
            data["storage_id"] = self.storage_id
        if self.source is not None:
            data["source"] = self.source
        return Message.model_validate(data)

    @property
    def content(self) -> str | list[dict[str, Any]]:
        return cast(str | list[dict[str, Any]], json.loads(self.content_json))


class CompressionArtifact(ContractModel):
    summary: NonEmptyString
    replacement_messages: tuple[CompressionMessage, ...]
    summarized_message_ids: tuple[NonEmptyString, ...]
    preserved_message_ids: tuple[NonEmptyString, ...] = ()
    persistence_eligible: bool
    persisted_summary_id: NonEmptyString | None = None

    @field_validator("summarized_message_ids", "preserved_message_ids")
    @classmethod
    def _validate_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("message IDs must be unique")
        return value

    @model_validator(mode="after")
    def _validate_persistence(self) -> CompressionArtifact:
        if not self.replacement_messages:
            raise ValueError("replacement_messages must not be empty")
        if not self.summarized_message_ids and self.persistence_eligible:
            raise ValueError("persistence eligibility requires summarized message IDs")
        if self.persisted_summary_id is not None and not self.persistence_eligible:
            raise ValueError("persisted summary ID requires persistence eligibility")
        return self


class SummaryPersistenceResult(ContractModel):
    status: PersistenceStatus
    summary_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_summary_id(self) -> SummaryPersistenceResult:
        if self.status is PersistenceStatus.SUCCEEDED and self.summary_id is None:
            raise ValueError("successful persistence requires summary_id")
        if self.status is not PersistenceStatus.SUCCEEDED and self.summary_id is not None:
            raise ValueError("non-successful persistence forbids summary_id")
        return self


class CompressionTelemetry(ContractModel):
    status: CompressionStatus
    reason: CompressionReason
    before_message_count: int = Field(default=0, ge=0)
    after_message_count: int = Field(default=0, ge=0)
    before_token_count: int = Field(default=0, ge=0)
    after_token_count: int = Field(default=0, ge=0)
    summarized_message_count: int = Field(default=0, ge=0)
    preserved_message_count: int = Field(default=0, ge=0)
    replacement_message_count: int = Field(default=0, ge=0)
    summary_model: CanonicalModel
    summarizer_usage: UsageAggregate = Field(default_factory=UsageAggregate)
    persistence: SummaryPersistenceResult
    error_code: NonEmptyString | None = None
    error_message: NonEmptyString | None = None
    before_context: ContextSnapshot | None = None
    after_context: ContextSnapshot | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> CompressionTelemetry:
        if self.status is CompressionStatus.SUCCEEDED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("successful compression forbids compression errors")
        if self.status is CompressionStatus.FAILED:
            if self.error_code is None:
                raise ValueError("failed compression requires error_code")
            if self.after_context is not None:
                raise ValueError("failed compression forbids after_context")
        if self.status is CompressionStatus.SKIPPED and self.after_context is not None:
            raise ValueError("skipped compression forbids after_context")
        return self


class CompressionResult(ContractModel):
    artifact: CompressionArtifact | None = None
    telemetry: CompressionTelemetry

    @model_validator(mode="after")
    def _validate_artifact_status(self) -> CompressionResult:
        succeeded = self.telemetry.status is CompressionStatus.SUCCEEDED
        if succeeded != (self.artifact is not None):
            raise ValueError("only successful compression may contain an artifact")
        if self.artifact is None:
            return self
        artifact = self.artifact
        telemetry = self.telemetry
        if telemetry.summarized_message_count != len(artifact.summarized_message_ids):
            raise ValueError("summarized count must match artifact IDs")
        if telemetry.preserved_message_count != len(artifact.preserved_message_ids):
            raise ValueError("preserved count must match artifact IDs")
        if telemetry.replacement_message_count != len(artifact.replacement_messages):
            raise ValueError("replacement count must match artifact messages")
        if telemetry.after_message_count != len(artifact.replacement_messages):
            raise ValueError("after message count must match artifact messages")
        persistence = telemetry.persistence
        if persistence.status is PersistenceStatus.SUCCEEDED:
            if not artifact.persistence_eligible or artifact.persisted_summary_id is None:
                raise ValueError("successful persistence requires an eligible persisted artifact")
            if not artifact.replacement_messages:
                raise ValueError("persisted artifact requires a replacement summary")
            if artifact.replacement_messages[0].storage_id != artifact.persisted_summary_id:
                raise ValueError("replacement summary ID must match persisted summary ID")
            if persistence.summary_id != artifact.persisted_summary_id:
                raise ValueError("persistence result ID must match artifact ID")
        elif artifact.persisted_summary_id is not None:
            raise ValueError("non-successful persistence forbids persisted artifact ID")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def compressed(self) -> bool:
        return self.telemetry.status is CompressionStatus.SUCCEEDED and self.artifact is not None


SummarySink = Callable[
    [CompressionContext, CompressionArtifact],
    SummaryPersistenceResult | Awaitable[SummaryPersistenceResult],
]
CompressionObserver = Callable[[CompressionTelemetry], Awaitable[None] | None]


__all__ = [
    "CompressionArtifact",
    "CompressionContext",
    "CompressionMessage",
    "CompressionObserver",
    "CompressionReason",
    "CompressionResult",
    "CompressionStatus",
    "CompressionTelemetry",
    "CompressionToolCall",
    "PersistenceStatus",
    "SummaryPersistenceResult",
    "SummarySink",
]
