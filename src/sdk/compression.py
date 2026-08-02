"""Typed contracts for lossless conversation context compression."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, computed_field, field_validator, model_validator

from src.sdk.messages import Message
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


class CompressionArtifact(ContractModel):
    summary: NonEmptyString
    replacement_messages: tuple[Message, ...]
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
        return self


class CompressionResult(ContractModel):
    artifact: CompressionArtifact | None = None
    telemetry: CompressionTelemetry

    @model_validator(mode="after")
    def _validate_artifact_status(self) -> CompressionResult:
        succeeded = self.telemetry.status is CompressionStatus.SUCCEEDED
        if succeeded != (self.artifact is not None):
            raise ValueError("only successful compression may contain an artifact")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def compressed(self) -> bool:
        return self.telemetry.status is CompressionStatus.SUCCEEDED and self.artifact is not None


SummarySink = Callable[
    [CompressionArtifact, CompressionContext],
    SummaryPersistenceResult | Awaitable[SummaryPersistenceResult],
]
CompressionObserver = Callable[[CompressionResult], Any | Awaitable[Any]]


__all__ = [
    "CompressionArtifact",
    "CompressionContext",
    "CompressionObserver",
    "CompressionReason",
    "CompressionResult",
    "CompressionStatus",
    "CompressionTelemetry",
    "PersistenceStatus",
    "SummaryPersistenceResult",
    "SummarySink",
]
