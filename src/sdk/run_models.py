"""Immutable canonical contracts for completed agent runs."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_CANONICAL_MODEL_PATTERN = r"^[^:/\s]+:\S+$"
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
# Percentages are wire values, so only insignificant floating-point rounding is tolerated.
CONTEXT_PERCENTAGE_ABS_TOLERANCE = 1e-9


def normalize_canonical_model(value: str) -> str:
    """Normalize and validate a canonical provider:model identifier."""
    if not isinstance(value, str):
        raise ValueError("model must use canonical provider:model syntax")
    provider, separator, model = value.partition(":")
    provider = provider.strip()
    model = model.strip()
    if (
        not separator
        or not provider
        or "/" in provider
        or any(character.isspace() for character in provider)
        or not model
        or any(character.isspace() for character in model)
    ):
        raise ValueError("model must use canonical provider:model syntax")
    return f"{provider}:{model}"


CanonicalModel = Annotated[
    str,
    StringConstraints(pattern=_CANONICAL_MODEL_PATTERN),
    BeforeValidator(normalize_canonical_model),
]


class ContractModel(BaseModel):
    """Base for immutable contracts with an exact schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RubricAvailability(StrEnum):
    ON = "on"
    OFF = "off"
    UNAVAILABLE = "unavailable"


class RubricUnavailableReason(StrEnum):
    MISSING_PROMPT = "missing_prompt"
    INVALID_GRADER_MODEL = "invalid_grader_model"
    MISSING_CREDENTIALS = "missing_credentials"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class RubricEvaluationResult(StrEnum):
    SATISFIED = "satisfied"
    NEEDS_REVISION = "needs_revision"
    INVALID_RUBRIC = "invalid_rubric"
    GRADER_ERROR = "grader_error"


class TerminalRubricStatus(StrEnum):
    NOT_RUN = "not_run"
    SATISFIED = "satisfied"
    MAX_ATTEMPTS_REACHED = "max_attempts_reached"
    INVALID_RUBRIC = "invalid_rubric"
    GRADER_ERROR = "grader_error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # C11: verification configured but skipped (auto mode)


class UsageCategory(StrEnum):
    AGENT = "agent"
    GRADER = "grader"
    SUMMARIZER = "summarizer"


class ContextSource(StrEnum):
    PREPARED_CONTEXT = "prepared_context"
    PROVIDER_USAGE = "provider_usage"
    POST_RUN_PROJECTION = "post_run_projection"
    HISTORY_ESTIMATE = "history_estimate"


class ContextFreshness(StrEnum):
    LIVE = "live"
    STALE = "stale"


class UsageAggregate(ContractModel):
    available: bool = False
    calls: int = Field(default=0, ge=0)
    models: tuple[CanonicalModel, ...] = ()
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_availability(self) -> UsageAggregate:
        token_total = (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )
        if not self.available and (self.calls or self.models or token_total):
            raise ValueError("unavailable usage cannot contain calls, models, or tokens")
        if self.available and (self.calls < 1 or not self.models):
            raise ValueError("available usage requires at least one call and one model")
        return self


class RunUsage(ContractModel):
    agent: UsageAggregate = Field(default_factory=UsageAggregate)
    grader: UsageAggregate = Field(default_factory=UsageAggregate)
    summarizer: UsageAggregate = Field(default_factory=UsageAggregate)


class ContextSnapshot(ContractModel):
    model: CanonicalModel
    attempt: int = Field(ge=1)
    llm_call_index: int = Field(ge=1)
    estimated_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=0)
    percentage: float | None = Field(default=None, ge=0)
    source: ContextSource
    freshness: ContextFreshness
    estimated: bool

    @model_validator(mode="after")
    def _validate_percentage(self) -> ContextSnapshot:
        if self.estimated_tokens is None or not self.context_window:
            if self.percentage is not None:
                raise ValueError("percentage must be null when token inputs are unknown")
            return self
        if self.percentage is None:
            raise ValueError("percentage is required when token inputs are known")
        expected = self.estimated_tokens / self.context_window * 100
        if not math.isclose(
            self.percentage,
            expected,
            rel_tol=0.0,
            abs_tol=CONTEXT_PERCENTAGE_ABS_TOLERANCE,
        ):
            raise ValueError("percentage must equal estimated_tokens / context_window * 100")
        return self


class CriterionEvaluation(ContractModel):
    name: NonEmptyString
    passed: bool
    gap: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_gap(self) -> CriterionEvaluation:
        if self.passed and self.gap is not None:
            raise ValueError("passed criteria must not have a gap")
        if not self.passed and self.gap is None:
            raise ValueError("failed criteria require a nonempty gap")
        return self


class RubricEvaluation(ContractModel):
    grading_run_id: NonEmptyString
    attempt: int = Field(ge=1)
    result: RubricEvaluationResult
    explanation: str
    criteria: tuple[CriterionEvaluation, ...]
    passed_count: int = Field(ge=0)
    total_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_derived_counts(self) -> RubricEvaluation:
        if self.total_count != len(self.criteria):
            raise ValueError("total_count must equal len(criteria)")
        if self.passed_count != sum(criterion.passed for criterion in self.criteria):
            raise ValueError("passed_count must equal the number of passed criteria")
        has_failed_criterion = any(not criterion.passed for criterion in self.criteria)
        if self.result is RubricEvaluationResult.SATISFIED and has_failed_criterion:
            raise ValueError("satisfied result cannot contain failed criteria")
        if self.result is RubricEvaluationResult.NEEDS_REVISION and not has_failed_criterion:
            raise ValueError("needs_revision result requires at least one failed criterion")
        return self


class VerificationOutcome(ContractModel):
    availability: RubricAvailability = RubricAvailability.OFF
    unavailable_reason: RubricUnavailableReason | None = None
    status: TerminalRubricStatus = TerminalRubricStatus.NOT_RUN
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1, le=3)
    evaluations: tuple[RubricEvaluation, ...] = ()

    @model_validator(mode="after")
    def _validate_outcome(self) -> VerificationOutcome:
        if self.availability is RubricAvailability.UNAVAILABLE:
            if self.unavailable_reason is None:
                raise ValueError("unavailable_reason is required when availability is unavailable")
        elif self.unavailable_reason is not None:
            raise ValueError("unavailable_reason is forbidden unless availability is unavailable")

        if self.availability is not RubricAvailability.ON:
            if (
                self.status is not TerminalRubricStatus.NOT_RUN
                or self.attempts != 0
                or self.evaluations
            ):
                raise ValueError("off or unavailable verification must be not_run with no attempts")

        if self.attempts > self.max_attempts:
            raise ValueError("attempts cannot exceed max_attempts")
        if self.status is TerminalRubricStatus.NOT_RUN:
            if self.attempts != 0:
                raise ValueError("not_run status requires zero attempts")
            if self.evaluations:
                raise ValueError("not_run status must not have evaluations")
        elif self.attempts < 1:
            raise ValueError("a terminal rubric status other than not_run requires at least one attempt")

        statuses_requiring_evaluation = {
            TerminalRubricStatus.SATISFIED,
            TerminalRubricStatus.MAX_ATTEMPTS_REACHED,
            TerminalRubricStatus.INVALID_RUBRIC,
            TerminalRubricStatus.GRADER_ERROR,
        }
        if self.status in statuses_requiring_evaluation and not self.evaluations:
            raise ValueError(f"{self.status} status requires at least one evaluation")

        if any(evaluation.attempt > self.attempts for evaluation in self.evaluations):
            raise ValueError("evaluation attempts cannot exceed outcome attempts")
        if any(
            current.attempt <= previous.attempt
            for previous, current in zip(self.evaluations, self.evaluations[1:])
        ):
            raise ValueError("evaluation attempts must be in strictly increasing order")
        grading_run_ids = [evaluation.grading_run_id for evaluation in self.evaluations]
        if len(grading_run_ids) != len(set(grading_run_ids)):
            raise ValueError("grading_run_id values must be unique")
        if (
            self.status in statuses_requiring_evaluation
            and self.evaluations[-1].attempt != self.attempts
        ):
            raise ValueError("final evaluation attempt must equal outcome attempts")

        if self.status is TerminalRubricStatus.MAX_ATTEMPTS_REACHED:
            if self.attempts != self.max_attempts:
                raise ValueError("max_attempts_reached requires attempts to equal max_attempts")

        expected_latest_results = {
            TerminalRubricStatus.SATISFIED: RubricEvaluationResult.SATISFIED,
            TerminalRubricStatus.MAX_ATTEMPTS_REACHED: RubricEvaluationResult.NEEDS_REVISION,
            TerminalRubricStatus.INVALID_RUBRIC: RubricEvaluationResult.INVALID_RUBRIC,
            TerminalRubricStatus.GRADER_ERROR: RubricEvaluationResult.GRADER_ERROR,
        }
        expected_latest_result = expected_latest_results.get(self.status)
        if (
            expected_latest_result is not None
            and self.evaluations[-1].result is not expected_latest_result
        ):
            raise ValueError("terminal rubric status must match the latest evaluation result")
        return self


class RunResult(ContractModel):
    schema_version: Literal[1] = 1
    run_id: NonEmptyString
    session_id: NonEmptyString
    status: RunStatus
    attempt: int = Field(ge=1)
    model: CanonicalModel
    response: str
    reasoning: str | None = None
    final_message_id: str | None = None
    usage: RunUsage
    verification: VerificationOutcome
    last_call_context: ContextSnapshot | None = None
    next_context: ContextSnapshot | None = None
    persisted_at: datetime | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator(
        "persisted_at", mode="before", json_schema_input_type=datetime | str | None
    )
    @classmethod
    def _persisted_at_input(cls, value: object) -> object:
        if value is None or isinstance(value, datetime):
            return value
        if not isinstance(value, str) or _RFC3339_PATTERN.fullmatch(value) is None:
            raise ValueError("persisted_at must be a datetime or RFC3339 string")
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
        except ValueError as exc:
            raise ValueError("persisted_at must be a datetime or RFC3339 string") from exc

    @field_validator("persisted_at")
    @classmethod
    def _timezone_aware_persisted_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("persisted_at must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def _contexts_match_run(self) -> RunResult:
        for field_name in ("last_call_context", "next_context"):
            context = getattr(self, field_name)
            if context is not None and (
                context.model != self.model or context.attempt != self.attempt
            ):
                raise ValueError(f"{field_name} model and attempt must match run")
        return self


# Display names for models whose models.dev entry uses the raw id as its
# name (most ollama-cloud models). models.dev gives a few models explicit
# display names (e.g. "DeepSeek V4 Flash 0731") — those are kept as-is;
# everything else gets a consistent readable title so the picker doesn't
# mix styles.
_MODEL_BRAND_NAMES = {
    "deepseek": "DeepSeek",
    "minimax": "MiniMax",
    "kimi": "Kimi",
    "nemotron": "Nemotron",
    "mistral": "Mistral",
    "gemma": "Gemma",
    "qwen": "Qwen",
    "glm": "GLM",
    "gpt": "GPT",
    "oss": "OSS",
    "ai": "AI",
    "api": "API",
    "ui": "UI",
}


def display_model_name(model_id: str, name: str) -> str:
    """Consistent picker display name: models.dev's explicit name when it
    provides one, otherwise a title-case derived from the id."""
    if name != model_id or not model_id:
        return name
    segments = re.split(r"[-:]", model_id)
    parts: list[str] = []
    for seg in segments:
        if not seg:
            continue
        lowered = seg.lower()
        if lowered in _MODEL_BRAND_NAMES:
            parts.append(_MODEL_BRAND_NAMES[lowered])
        else:
            # "deepseek" -> "Deepseek", "20b" -> "20B", "m3" -> "M3"
            head = seg[:1].upper() + seg[1:]
            if len(head) >= 2 and head[-1] == "b" and head[-2].isdigit():
                head = head[:-1] + "B"
            parts.append(head)
    return " ".join(parts)
