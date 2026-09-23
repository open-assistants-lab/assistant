"""Contract tests for preflight result metadata before an LLM can start."""

from __future__ import annotations

from src.sdk.subagent_models import SubagentResult


def test_subagent_result_defaults_are_backward_compatible() -> None:
    result = SubagentResult(name="worker", task="review", success=True, output="done")

    assert result.terminal_reason == "completed"
    assert result.verified is None
    assert result.launch_plan_id is None


def test_subagent_result_can_describe_preflight_block_without_llm_output() -> None:
    result = SubagentResult(
        name="worker",
        task="write production file",
        success=False,
        output="",
        error="files_write requires approval before start",
        terminal_reason="blocked",
        launch_plan_id="plan-123",
    )

    assert result.success is False
    assert result.terminal_reason == "blocked"
    assert result.launch_plan_id == "plan-123"
    assert result.llm_calls == 0
