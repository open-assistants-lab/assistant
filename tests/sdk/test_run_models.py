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
        model=" openai : gpt-5 ",
        attempt=1,
        llm_call_index=2,
        estimated_tokens=1200,
        context_window=128_000,
        percentage=125.0,
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
    assert restored.last_call_context is not None
    assert restored.last_call_context.model == "openai:gpt-5"
    assert restored.last_call_context.percentage == 125.0
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


def test_collection_fields_are_immutable_tuples_serialized_as_json_arrays() -> None:
    usage = UsageAggregate(available=True, calls=1, models=[" openai : gpt-5 "])
    evaluation = _evaluation()
    verification = _verification()

    assert usage.models == ("openai:gpt-5",)
    assert evaluation.criteria == (CriterionEvaluation(name="Correct answer", passed=True),)
    assert verification.evaluations == (evaluation,)
    assert '"models":["openai:gpt-5"]' in usage.model_dump_json()
    with pytest.raises(AttributeError):
        usage.models.append("anthropic:claude")  # type: ignore[attr-defined]


def test_usage_rejects_noncanonical_model_display_syntax() -> None:
    with pytest.raises(ValidationError, match="provider:model"):
        UsageAggregate(available=True, calls=1, models=["openai/gpt-5"])


def test_run_result_normalizes_canonical_model() -> None:
    result = RunResult(
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.COMPLETED,
        attempt=1,
        model=" openai : gpt-5 ",
        response="Complete.",
        usage=RunUsage(),
        verification=VerificationOutcome(),
    )

    assert result.model == "openai:gpt-5"


def test_run_result_requires_timezone_aware_persisted_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunResult(
            run_id="run-1",
            session_id="session-1",
            status=RunStatus.COMPLETED,
            attempt=1,
            model="openai:gpt-5",
            response="Complete.",
            usage=RunUsage(),
            verification=VerificationOutcome(),
            persisted_at=datetime(2026, 8, 2, 12, 0),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"calls": 1},
        {"input_tokens": 1},
        {"models": ["openai:gpt-5"]},
    ],
)
def test_unavailable_usage_rejects_recorded_activity(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="unavailable usage"):
        UsageAggregate.model_validate(payload)


@pytest.mark.parametrize(
    ("calls", "models"),
    [(0, ["openai:gpt-5"]), (1, [])],
)
def test_available_usage_requires_calls_and_models(calls: int, models: list[str]) -> None:
    with pytest.raises(ValidationError, match="available usage"):
        UsageAggregate(available=True, calls=calls, models=models)


@pytest.mark.parametrize(
    "criterion",
    [
        {"name": "Correct", "passed": False},
        {"name": "Correct", "passed": False, "gap": "  "},
        {"name": "Correct", "passed": True, "gap": "Unexpected"},
    ],
)
def test_criterion_gap_must_match_passed_state(criterion: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="gap"):
        CriterionEvaluation.model_validate(criterion)


@pytest.mark.parametrize(
    ("result", "criteria"),
    [
        (
            RubricEvaluationResult.SATISFIED,
            [CriterionEvaluation(name="Correct", passed=False, gap="Wrong")],
        ),
        (
            RubricEvaluationResult.NEEDS_REVISION,
            [CriterionEvaluation(name="Correct", passed=True)],
        ),
    ],
)
def test_evaluation_result_must_match_criteria(
    result: RubricEvaluationResult, criteria: list[CriterionEvaluation]
) -> None:
    with pytest.raises(ValidationError, match="result"):
        RubricEvaluation(
            grading_run_id="grading-1",
            attempt=1,
            result=result,
            explanation="Contradiction.",
            criteria=criteria,
            passed_count=sum(criterion.passed for criterion in criteria),
            total_count=len(criteria),
        )


@pytest.mark.parametrize("availability", [RubricAvailability.OFF, RubricAvailability.UNAVAILABLE])
def test_inactive_verification_rejects_run_state(availability: RubricAvailability) -> None:
    reason = (
        RubricUnavailableReason.PROVIDER_UNAVAILABLE
        if availability is RubricAvailability.UNAVAILABLE
        else None
    )
    with pytest.raises(ValidationError, match="not_run"):
        VerificationOutcome(
            availability=availability,
            unavailable_reason=reason,
            status=TerminalRubricStatus.CANCELLED,
            attempts=1,
        )


@pytest.mark.parametrize(
    ("status", "result"),
    [
        (TerminalRubricStatus.SATISFIED, RubricEvaluationResult.NEEDS_REVISION),
        (TerminalRubricStatus.INVALID_RUBRIC, RubricEvaluationResult.GRADER_ERROR),
        (TerminalRubricStatus.GRADER_ERROR, RubricEvaluationResult.INVALID_RUBRIC),
        (TerminalRubricStatus.MAX_ATTEMPTS_REACHED, RubricEvaluationResult.SATISFIED),
    ],
)
def test_terminal_status_must_match_latest_evaluation(
    status: TerminalRubricStatus, result: RubricEvaluationResult
) -> None:
    criteria = (
        [CriterionEvaluation(name="Correct", passed=False, gap="Wrong")]
        if result is RubricEvaluationResult.NEEDS_REVISION
        else []
    )
    evaluation = RubricEvaluation(
        grading_run_id="grading-1",
        attempt=1,
        result=result,
        explanation="Result.",
        criteria=criteria,
        passed_count=0,
        total_count=len(criteria),
    )
    with pytest.raises(ValidationError, match="latest evaluation"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=status,
            attempts=1,
            evaluations=[evaluation],
        )


def test_max_attempts_status_requires_all_attempts() -> None:
    with pytest.raises(ValidationError, match="max_attempts"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.MAX_ATTEMPTS_REACHED,
            attempts=1,
            max_attempts=2,
        )


def test_verification_rejects_future_and_duplicate_evaluations() -> None:
    evaluation = _evaluation()
    future_evaluation = RubricEvaluation.model_validate(
        {**evaluation.model_dump(), "attempt": 2}
    )

    with pytest.raises(ValidationError, match="cannot exceed outcome attempts"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.SATISFIED,
            attempts=1,
            evaluations=[future_evaluation],
        )
    with pytest.raises(ValidationError, match="grading_run_id"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.SATISFIED,
            attempts=1,
            evaluations=[evaluation, evaluation],
        )


def test_cancelled_verification_allows_unevaluated_final_attempt() -> None:
    outcome = VerificationOutcome(
        availability=RubricAvailability.ON,
        status=TerminalRubricStatus.CANCELLED,
        attempts=2,
        max_attempts=2,
        evaluations=[_evaluation()],
    )

    assert len(outcome.evaluations) < outcome.attempts
