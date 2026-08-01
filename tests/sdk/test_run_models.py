"""Tests for canonical run outcome contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.sdk.run_models as run_models
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

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "run_contracts"


def _evaluation() -> RubricEvaluation:
    return RubricEvaluation(
        grading_run_id="grading-1",
        attempt=1,
        result=RubricEvaluationResult.SATISFIED,
        explanation="All criteria passed.",
        criteria=(CriterionEvaluation(name="Correct answer", passed=True),),
        passed_count=1,
        total_count=1,
    )


def _verification() -> VerificationOutcome:
    return VerificationOutcome(
        availability=RubricAvailability.ON,
        status=TerminalRubricStatus.SATISFIED,
        attempts=1,
        evaluations=(_evaluation(),),
    )


def test_canonical_run_result_fixture_validates_and_round_trips() -> None:
    payload = json.loads((FIXTURE_DIR / "run_result.json").read_text())

    result = RunResult.model_validate(payload)

    assert result.run_id == "run-canonical-001"
    assert result.model == "anthropic:claude-sonnet-4"
    assert result.model_dump(mode="json") == payload
    assert RunResult.model_validate(result.model_dump(mode="json")) == result


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
            criteria=(CriterionEvaluation(name="Correct answer", passed=False, gap="Wrong"),),
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
        percentage=1200 / 128_000 * 100,
        source=ContextSource.PROVIDER_USAGE,
        freshness=ContextFreshness.LIVE,
        estimated=False,
    )
    usage = RunUsage(
        agent=UsageAggregate(
            available=True,
            calls=2,
            models=("openai:gpt-5",),
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
    assert restored.last_call_context.percentage == 1200 / 128_000 * 100
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
    usage = UsageAggregate(available=True, calls=1, models=(" openai : gpt-5 ",))
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
        UsageAggregate(available=True, calls=1, models=("openai/gpt-5",))


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


def test_canonical_model_acceptance_and_schema_are_shared_across_run_contracts() -> None:
    context = ContextSnapshot(
        model=" openrouter : anthropic/claude-sonnet-4 ",
        attempt=1,
        llm_call_index=1,
        source=ContextSource.HISTORY_ESTIMATE,
        freshness=ContextFreshness.STALE,
        estimated=True,
    )
    usage = UsageAggregate(
        available=True,
        calls=1,
        models=(" openrouter : anthropic/claude-sonnet-4 ",),
    )

    assert context.model == "openrouter:anthropic/claude-sonnet-4"
    assert usage.models == ("openrouter:anthropic/claude-sonnet-4",)
    assert run_models.CanonicalModel is not str
    assert RunResult.model_json_schema()["properties"]["model"]["pattern"] == r"^[^:/\s]+:\S+$"
    assert ContextSnapshot.model_json_schema()["properties"]["model"]["pattern"] == r"^[^:/\s]+:\S+$"
    assert UsageAggregate.model_json_schema()["properties"]["models"]["items"]["pattern"] == (
        r"^[^:/\s]+:\S+$"
    )


@pytest.mark.parametrize(
    "model",
    ["anthropic/claude", "open/router:model", "open router:model", "openai:gpt 5"],
)
def test_all_run_model_fields_reject_noncanonical_ids(model: str) -> None:
    with pytest.raises(ValidationError, match="canonical provider:model"):
        ContextSnapshot(
            model=model,
            attempt=1,
            llm_call_index=1,
            source=ContextSource.HISTORY_ESTIMATE,
            freshness=ContextFreshness.STALE,
            estimated=True,
        )
    with pytest.raises(ValidationError, match="canonical provider:model"):
        UsageAggregate(available=True, calls=1, models=(model,))


@pytest.mark.parametrize(
    ("estimated_tokens", "context_window", "percentage"),
    [(None, 100, 1.0), (10, None, 1.0), (None, None, 0.0)],
)
def test_context_percentage_must_be_null_when_inputs_are_unknown(
    estimated_tokens: int | None, context_window: int | None, percentage: float
) -> None:
    with pytest.raises(ValidationError, match="percentage must be null"):
        ContextSnapshot(
            model="openai:gpt-5",
            attempt=1,
            llm_call_index=1,
            estimated_tokens=estimated_tokens,
            context_window=context_window,
            percentage=percentage,
            source=ContextSource.HISTORY_ESTIMATE,
            freshness=ContextFreshness.STALE,
            estimated=True,
        )


def test_context_zero_window_is_valid_with_null_percentage() -> None:
    snapshot = ContextSnapshot(
        model="openai:gpt-5",
        attempt=1,
        llm_call_index=1,
        estimated_tokens=10,
        context_window=0,
        percentage=None,
        source=ContextSource.HISTORY_ESTIMATE,
        freshness=ContextFreshness.STALE,
        estimated=True,
    )

    assert snapshot.context_window == 0
    assert snapshot.percentage is None


def test_context_zero_window_rejects_percentage() -> None:
    with pytest.raises(ValidationError, match="percentage must be null"):
        ContextSnapshot(
            model="openai:gpt-5",
            attempt=1,
            llm_call_index=1,
            estimated_tokens=10,
            context_window=0,
            percentage=100.0,
            source=ContextSource.HISTORY_ESTIMATE,
            freshness=ContextFreshness.STALE,
            estimated=True,
        )


def test_context_percentage_is_required_and_derived_when_inputs_are_known() -> None:
    values = {
        "model": "openai:gpt-5",
        "attempt": 1,
        "llm_call_index": 1,
        "estimated_tokens": 125,
        "context_window": 100,
        "source": ContextSource.PROVIDER_USAGE,
        "freshness": ContextFreshness.LIVE,
        "estimated": False,
    }

    snapshot = ContextSnapshot.model_validate({**values, "percentage": 125.0})
    assert snapshot.percentage == 125.0
    with pytest.raises(ValidationError, match="percentage is required"):
        ContextSnapshot.model_validate(values)
    with pytest.raises(ValidationError, match="estimated_tokens / context_window"):
        ContextSnapshot.model_validate({**values, "percentage": 124.999})


@pytest.mark.parametrize("field", ["last_call_context", "next_context"])
@pytest.mark.parametrize(("model", "attempt"), [("anthropic:claude", 1), ("openai:gpt-5", 2)])
def test_run_result_context_identity_matches_run(
    field: str, model: str, attempt: int
) -> None:
    context = ContextSnapshot(
        model=model,
        attempt=attempt,
        llm_call_index=1,
        source=ContextSource.HISTORY_ESTIMATE,
        freshness=ContextFreshness.STALE,
        estimated=True,
    )
    with pytest.raises(ValidationError, match=f"{field} .* must match run"):
        RunResult.model_validate(
            {
                "run_id": "run-1",
                "session_id": "session-1",
                "status": RunStatus.COMPLETED,
                "attempt": 1,
                "model": "openai:gpt-5",
                "response": "Complete.",
                "usage": RunUsage(),
                "verification": VerificationOutcome(),
                field: context,
            }
        )


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


@pytest.mark.parametrize("persisted_at", [0, 1.5, "0", "2026-08-02"])
def test_run_result_rejects_non_rfc3339_persisted_at(persisted_at: object) -> None:
    with pytest.raises(ValidationError, match="datetime or RFC3339 string"):
        RunResult.model_validate(
            {
                "run_id": "run-1",
                "session_id": "session-1",
                "status": "completed",
                "attempt": 1,
                "model": "openai:gpt-5",
                "response": "Complete.",
                "usage": {},
                "verification": {},
                "persisted_at": persisted_at,
            }
        )


@pytest.mark.parametrize(
    "persisted_at",
    [
        datetime(2026, 8, 2, 8, tzinfo=timezone(timedelta(hours=-4))),
        "2026-08-02T08:00:00-04:00",
    ],
)
def test_run_result_normalizes_persisted_at_to_utc(persisted_at: datetime | str) -> None:
    result = RunResult.model_validate(
        {
            "run_id": "run-1",
            "session_id": "session-1",
            "status": RunStatus.COMPLETED,
            "attempt": 1,
            "model": "openai:gpt-5",
            "response": "Complete.",
            "usage": RunUsage(),
            "verification": VerificationOutcome(),
            "persisted_at": persisted_at,
        }
    )

    assert result.persisted_at == datetime(2026, 8, 2, 12, tzinfo=UTC)
    assert result.persisted_at.tzinfo is UTC


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
    [(0, ("openai:gpt-5",)), (1, ())],
)
def test_available_usage_requires_calls_and_models(calls: int, models: tuple[str, ...]) -> None:
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
            (CriterionEvaluation(name="Correct", passed=False, gap="Wrong"),),
        ),
        (
            RubricEvaluationResult.NEEDS_REVISION,
            (CriterionEvaluation(name="Correct", passed=True),),
        ),
    ],
)
def test_evaluation_result_must_match_criteria(
    result: RubricEvaluationResult, criteria: tuple[CriterionEvaluation, ...]
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
        (CriterionEvaluation(name="Correct", passed=False, gap="Wrong"),)
        if result is RubricEvaluationResult.NEEDS_REVISION
        else ()
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
            evaluations=(evaluation,),
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
            evaluations=(future_evaluation,),
        )
    with pytest.raises(ValidationError, match="grading_run_id"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.SATISFIED,
            attempts=2,
            max_attempts=2,
            evaluations=(evaluation, future_evaluation),
        )


def test_cancelled_verification_allows_unevaluated_final_attempt() -> None:
    outcome = VerificationOutcome(
        availability=RubricAvailability.ON,
        status=TerminalRubricStatus.CANCELLED,
        attempts=2,
        max_attempts=2,
        evaluations=(_evaluation(),),
    )

    assert len(outcome.evaluations) < outcome.attempts


@pytest.mark.parametrize(
    "status",
    [
        TerminalRubricStatus.SATISFIED,
        TerminalRubricStatus.MAX_ATTEMPTS_REACHED,
        TerminalRubricStatus.INVALID_RUBRIC,
        TerminalRubricStatus.GRADER_ERROR,
    ],
)
def test_terminal_verification_requires_evaluation(status: TerminalRubricStatus) -> None:
    with pytest.raises(ValidationError, match="at least one evaluation"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=status,
            attempts=1,
        )


def test_verification_rejects_misordered_evaluation_attempts() -> None:
    first = RubricEvaluation(
        grading_run_id="grading-2",
        attempt=2,
        result=RubricEvaluationResult.NEEDS_REVISION,
        explanation="Needs revision.",
        criteria=(CriterionEvaluation(name="Correct", passed=False, gap="Wrong"),),
        passed_count=0,
        total_count=1,
    )
    second = _evaluation()

    with pytest.raises(ValidationError, match="strictly increasing"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.SATISFIED,
            attempts=2,
            max_attempts=2,
            evaluations=(first, second),
        )


def test_terminal_verification_requires_evaluation_for_final_attempt() -> None:
    with pytest.raises(ValidationError, match="final evaluation attempt"):
        VerificationOutcome(
            availability=RubricAvailability.ON,
            status=TerminalRubricStatus.SATISFIED,
            attempts=3,
            max_attempts=3,
            evaluations=(_evaluation(),),
        )
