"""Deterministic acceptance checks for subagent reliability contracts.

Provider-backed evaluations remain separately gated by RUN_HTTP_EVALS=1.
"""

from __future__ import annotations

from src.sdk.subagent_models import SubagentResult, TaskStatus


def test_terminal_vocabulary_keeps_timeout_cancellation_and_failure_distinct() -> None:
    assert TaskStatus.TIMED_OUT.value == "timed_out"
    assert TaskStatus.CANCELLED.value == "cancelled"
    assert TaskStatus.FAILED.value == "failed"


def test_result_contract_carries_terminal_reason_and_launch_plan() -> None:
    result = SubagentResult(
        name="worker",
        task="inspect",
        success=False,
        output="",
        terminal_reason="timed_out",
        error_code="timeout",
        launch_plan_id="launch-123",
        verified=False,
    )

    assert result.terminal_reason == "timed_out"
    assert result.error_code == "timeout"
    assert result.launch_plan_id == "launch-123"
    assert result.verified is False


def test_legacy_result_payload_remains_backward_compatible() -> None:
    result = SubagentResult.model_validate(
        {"name": "worker", "task": "inspect", "success": True, "output": "done"}
    )

    assert result.terminal_reason == "completed"
    assert result.error_code is None
    assert result.launch_plan_id is None
