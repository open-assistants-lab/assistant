"""Contract tests for preflight result metadata before an LLM can start."""

from __future__ import annotations

import pytest

from src.sdk.subagent_models import SubagentResult


def test_subagent_result_defaults_are_backward_compatible() -> None:
    result = SubagentResult(name="worker", task="review", success=True, output="done")

    assert result.terminal_reason == "completed"
    assert result.verified is None
    assert result.launch_plan_id is None


async def test_delegate_rejects_preflight_before_queue_or_llm(monkeypatch, tmp_path) -> None:
    from agentprofile.models import AgentProfile

    from src.sdk.coordinator import SubagentCoordinator
    from src.sdk.subagent_capabilities import (
        CapabilityDecision,
        SubagentLaunchPlan,
        ToolSelectionMode,
    )

    coordinator = SubagentCoordinator("user")
    profile = AgentProfile(name="writer", tools=["files_write"])
    monkeypatch.setattr(coordinator, "load_def", lambda _name: profile)
    blocked_plan = SubagentLaunchPlan(
        plan_id="plan-blocked",
        agent_name="writer",
        user_id="user",
        workspace_id="user",
        tool_selection_mode=ToolSelectionMode.ALLOWLIST,
        requested_tools=("files_write",),
        decisions=(
            CapabilityDecision(
                kind="tool",
                name="files_write",
                status="permission_ask",
                reason="permission policy resolved to ask",
            ),
        ),
        ready=False,
    )
    monkeypatch.setattr(coordinator, "preflight", lambda _name: blocked_plan)

    result = await coordinator.delegate("writer", "write a file")

    assert result.is_error is True
    assert result.structured_content == {
        "status": "approval_required_before_start",
        "reason": "capability_unavailable",
        "tools": ["files_write"],
        "skills": [],
        "llm_started": False,
        "queue_inserted": False,
    }


async def test_start_rejects_preflight_before_queue_insertion(monkeypatch) -> None:
    from agentprofile.models import AgentProfile

    from src.sdk.coordinator import SubagentCoordinator
    from src.sdk.subagent_capabilities import (
        CapabilityDecision,
        SubagentLaunchPlan,
        SubagentLaunchRejected,
        ToolSelectionMode,
    )

    coordinator = SubagentCoordinator("user")
    profile = AgentProfile(name="writer", tools=["files_write"])
    monkeypatch.setattr(coordinator, "load_def", lambda _name: profile)
    monkeypatch.setattr(coordinator, "_get_db", lambda: (_ for _ in ()).throw(AssertionError("DB")))
    monkeypatch.setattr(
        coordinator,
        "preflight",
        lambda _name: SubagentLaunchPlan(
            plan_id="blocked",
            agent_name="writer",
            user_id="user",
            workspace_id="user",
            tool_selection_mode=ToolSelectionMode.ALLOWLIST,
            decisions=(
                CapabilityDecision(
                    kind="tool", name="files_write", status="permission_deny", reason="deny"
                ),
            ),
            ready=False,
        ),
    )

    with pytest.raises(SubagentLaunchRejected):
        await coordinator.start("writer", "write a file")


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
