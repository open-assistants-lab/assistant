"""Tests for canonical run outcome contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.sdk.run_models import (
    ContextFreshness,
    ContextSnapshot,
    ContextSource,
    CriterionEvaluation,
    RubricAvailability,
    RubricEvaluation,
    RubricEvaluationResult,
    RubricUnavailableReason,
    RunResult,
    RunStatus,
    RunUsage,
    TerminalRubricStatus,
    UsageAggregate,
    VerificationOutcome,
)


def _evaluation() -> RubricEvaluation:
    return RubricEvaluation(
        grading_run_id="grading-1",
        attempt=1,
        result=RubricEvaluationResult.SATISFIED,
        explanation="All criteria passed.",
        criteria=[CriterionEvaluation(name="Correct answer", passed=True)],
        passed_count=1,
        total_count=1,
    )


def _verification() -> VerificationOutcome:
    return VerificationOutcome(
        availability=RubricAvailability.ON,
        status=TerminalRubricStatus.SATISFIED,
        attempts=1,
        evaluations=[_evaluation()],
    )


def test_rubric_evaluation_accepts_consistent_derived_counts() -> None:
    evaluation = _evaluation()

    assert evaluation.total_count == len(evaluation.criteria)
    assert evaluation.passed_count == sum(criterion.passed for criterion in evaluation.criteria)


def test_rubric_evaluation_rejects_incorrect_passed_count() -> None:
    with pytest.raises(ValidationError, match="passed_count"):
        RubricEvaluation(
            grading_run_id="grading-1",
            attempt=1,
            result=RubricEvaluationResult.NEEDS_REVISION,
            explanation="One criterion failed.",
            criteria=[CriterionEvaluation(name="Correct answer", passed=False, gap="Wrong")],
            passed_count=1,
            total_count=1,
        )


def test_run_result_round_trips_all_canonical_outcomes() -> None:
    context = ContextSnapshot(
        model="openai:gpt-5",
        attempt=1,
        llm_call_index=2,
        estimated_tokens=1200,
        context_window=128_000,
        percentage=0.9375,
        source=ContextSource.PROVIDER_USAGE,
        freshness=ContextFreshness.LIVE,
        estimated=False,
    )
    usage = RunUsage(
        agent=UsageAggregate(
            available=True,
            calls=2,
            models=["openai:gpt-5"],
            input_tokens=1000,
            output_tokens=200,
            reasoning_tokens=50,
            cache_read_tokens=100,
            cache_creation_tokens=25,
        )
    )
    result = RunResult(
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.COMPLETED,
        attempt=1,
        model="openai:gpt-5",
        response="Complete.",
        final_message_id="message-1",
        usage=usage,
        verification=_verification(),
        last_call_context=context,
        next_context=context.model_copy(update={"source": ContextSource.POST_RUN_PROJECTION}),
        persisted_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )

    restored = RunResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.persisted_at is not None
    assert restored.persisted_at.tzinfo is not None


def test_run_result_rejects_provider_slash_model_display_syntax() -> None:
    with pytest.raises(ValidationError, match="provider:model"):
        RunResult(
            run_id="run-1",
            session_id="session-1",
            status=RunStatus.COMPLETED,
            attempt=1,
            model="openai/gpt-5",
            response="Complete.",
            usage=RunUsage(),
            verification=VerificationOutcome(),
        )


def test_unavailable_verification_requires_reason() -> None:
    with pytest.raises(ValidationError, match="unavailable_reason"):
        VerificationOutcome(availability=RubricAvailability.UNAVAILABLE)


def test_verification_rejects_attempts_exceeding_maximum() -> None:
    with pytest.raises(ValidationError, match="attempts"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.MAX_ATTEMPTS_REACHED,
            attempts=2,
            max_attempts=1,
        )


def test_contracts_are_frozen_and_forbid_extra_fields() -> None:
    usage = UsageAggregate()

    with pytest.raises(ValidationError, match="frozen"):
        usage.calls = 1
    with pytest.raises(ValidationError, match="extra"):
        UsageAggregate.model_validate({"unknown": True})


def test_non_unavailable_verification_forbids_reason() -> None:
    with pytest.raises(ValidationError, match="unavailable_reason"):
        VerificationOutcome(
            availability=RubricAvailability.OFF,
            unavailable_reason=RubricUnavailableReason.MISSING_PROMPT,
        )
