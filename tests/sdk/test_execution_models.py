from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.sdk.execution_models import (
    EffectState,
    ExecutionRequest,
    ExecutorState,
    Outcome,
    Receipt,
    VerificationState,
)


def test_receipt_derives_legacy_execution_projection() -> None:
    receipt = Receipt(
        receipt_id="r1",
        request_id="req1",
        tool_name="shell_execute",
        profile="build",
        outcome=Outcome.TIMED_OUT,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.UNKNOWN,
        verification_state=VerificationState.UNKNOWN,
    )

    assert receipt.legacy_executed is True
    assert receipt.legacy_verified is None


def test_rejected_receipt_projects_not_executed() -> None:
    receipt = Receipt(
        receipt_id="r1",
        request_id="req1",
        tool_name="connector.write",
        profile="use",
        outcome=Outcome.REJECTED,
        executor_state=ExecutorState.NOT_STARTED,
        effect_state=EffectState.NOT_APPLICABLE,
        verification_state=VerificationState.NOT_REQUESTED,
    )

    assert receipt.legacy_executed is False
    assert receipt.legacy_verified is None


def test_timeout_and_uncertain_are_distinct_outcomes() -> None:
    assert Outcome.TIMED_OUT.value == "timed_out"
    assert Outcome.UNCERTAIN.value == "uncertain"
    assert Outcome.TIMED_OUT != Outcome.UNCERTAIN


def test_execution_request_has_typed_defaults() -> None:
    request = ExecutionRequest(
        request_id="req1",
        tool_name="files_read",
        profile="build",
        created_at=datetime.now(UTC),
    )

    assert request.arguments == {}
    assert request.run_id is None
    assert request.tool_call_id is None


def test_invalid_executor_state_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Receipt(
            receipt_id="r1",
            request_id="req1",
            tool_name="files_read",
            profile="build",
            executor_state="complete",
            effect_state=EffectState.NOT_APPLICABLE,
            verification_state=VerificationState.NOT_REQUESTED,
        )
