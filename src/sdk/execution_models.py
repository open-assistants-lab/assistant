"""Shared execution lifecycle contracts for governed tool execution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Outcome(StrEnum):
    """Terminal result of an execution request."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNCERTAIN = "uncertain"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"


class ExecutorState(StrEnum):
    """What the execution boundary knows about the executor itself."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class EffectState(StrEnum):
    """What the system knows about the requested side effect."""

    NOT_APPLICABLE = "not_applicable"
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


class VerificationState(StrEnum):
    """Whether evidence established the requested condition."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    UNKNOWN = "unknown"


class ExecutionRequest(BaseModel):
    """Normalized request submitted to the shared execution kernel."""

    request_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str
    expected_effect: EffectState = EffectState.NOT_APPLICABLE
    arguments: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Observation(BaseModel):
    """Evidence observed during or after execution."""

    observation_id: str
    kind: str
    source: str
    authoritative: bool = False
    correlation_id: str | None = None
    observed_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionEvent(BaseModel):
    """Immutable lifecycle event stored for a receipt."""

    event_id: str
    receipt_id: str
    request_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ExecutionCompletion(BaseModel):
    """Executor-provided completion details consumed by the kernel."""

    outcome: Outcome = Outcome.SUCCEEDED
    effect_state: EffectState = EffectState.NOT_APPLICABLE
    verification_state: VerificationState = VerificationState.NOT_REQUESTED
    content: dict[str, Any] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)


class Receipt(BaseModel):
    """Current execution projection reconstructed from lifecycle events."""

    receipt_id: str
    request_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str
    outcome: Outcome | None = None
    executor_state: ExecutorState
    effect_state: EffectState
    verification_state: VerificationState
    termination_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)

    @property
    def legacy_executed(self) -> bool | None:
        """Return the legacy tri-state execution projection."""

        if self.executor_state is ExecutorState.NOT_STARTED:
            return False
        if self.executor_state in (ExecutorState.RUNNING, ExecutorState.TERMINAL):
            return True
        return None

    @property
    def legacy_verified(self) -> bool | None:
        """Return the legacy tri-state verification projection."""

        if self.verification_state is VerificationState.VERIFIED:
            return True
        if self.verification_state is VerificationState.NOT_VERIFIED:
            return False
        return None
