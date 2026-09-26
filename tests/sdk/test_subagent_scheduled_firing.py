"""Frozen-manifest subagent launch (issue #46, Task 2).

A schedule is created from a plan resolved once; every fire must launch from
that same frozen authority, and must refuse to run if the profile has since
drifted rather than silently broadening or narrowing what it may do.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from agentprofile.models import AgentProfile

os.environ.setdefault("AGENT_MODEL", "ollama:minimax-m2.5")


@pytest.fixture
def mock_paths():
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        paths = DataPaths(data_path=d, user_id="test_user", data_root=d)

        def temp_workspace_dir(name: str) -> Path:
            path = Path(d) / "workspaces" / "personal" / name
            path.mkdir(parents=True, exist_ok=True)
            return path

        paths.workspace_subagents_dir = lambda: temp_workspace_dir("subagents")
        paths.workspace_memory_dir = lambda: temp_workspace_dir("memory")
        paths.subagents_dir = paths.workspace_subagents_dir
        paths.user_subagents_dir = paths.workspace_subagents_dir
        paths.memory_dir = paths.workspace_memory_dir
        with patch("src.storage.paths.get_paths", return_value=paths), patch(
            "src.sdk.subagent_work_queue.get_paths", return_value=paths
        ), patch("src.sdk.coordinator.get_paths", return_value=paths):
            yield paths


def _profile(name: str = "researcher") -> AgentProfile:
    return AgentProfile(name=name, description="test", tools=["files_read"])


async def _coordinator_with_profile(mock_paths, name: str = "researcher"):
    """A coordinator holding one valid profile, with an empty tool allowlist mode."""
    from src.sdk.coordinator import SubagentCoordinator

    coord = SubagentCoordinator("test_user", workspace_id="personal")
    await coord.create(_profile(name))
    return coord


async def _queued_task_count(coord) -> int:
    """Count work-queue rows directly — there is no list_tasks() helper."""
    db = await coord._get_db()
    cursor = await db._get_db()
    result = await cursor.execute("SELECT COUNT(*) FROM work_queue")
    row = await result.fetchone()
    return int(row[0])


# --------------------------------------------------------------------------
# from_persisted round-trip + tamper detection
# --------------------------------------------------------------------------


def test_from_persisted_round_trips_a_real_plan() -> None:
    from src.sdk.subagent_capabilities import (
        SubagentLaunchPlan,
        ToolSelectionMode,
        build_launch_plan,
    )

    plan = build_launch_plan(
        _profile(), "test_user", "personal", ToolSelectionMode.ALLOWLIST
    )
    restored = SubagentLaunchPlan.from_persisted(plan.to_persisted_dict())

    assert restored.plan_id == plan.plan_id
    assert restored.effective_tools == plan.effective_tools
    assert restored.effective_skills == plan.effective_skills
    assert restored.resolved_workspace_id == plan.resolved_workspace_id
    assert restored.canonical_manifest_json == plan.canonical_manifest_json


def test_from_persisted_rejects_a_tampered_row() -> None:
    from src.sdk.subagent_capabilities import (
        SubagentLaunchPlan,
        SubagentLaunchPlanTampered,
        ToolSelectionMode,
        build_launch_plan,
    )

    plan = build_launch_plan(
        _profile(), "test_user", "personal", ToolSelectionMode.ALLOWLIST
    )
    payload = plan.to_persisted_dict()
    # Grant the frozen plan a capability it never resolved.
    payload["effective_tools"] = [*payload["effective_tools"], "shell_execute"]

    with pytest.raises(SubagentLaunchPlanTampered):
        SubagentLaunchPlan.from_persisted(payload)


def test_from_persisted_rejects_a_row_without_a_canonical_manifest() -> None:
    from src.sdk.subagent_capabilities import SubagentLaunchPlan

    with pytest.raises(ValueError, match="canonical manifest"):
        SubagentLaunchPlan.from_persisted({"plan_id": "x", "agent_name": "a"})


# --------------------------------------------------------------------------
# start_with_plan
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_with_plan_launches_from_the_frozen_manifest(mock_paths) -> None:
    from src.sdk.coordinator import SubagentCoordinator
    from src.sdk.subagent_capabilities import SubagentLaunchPlan

    coord = await _coordinator_with_profile(mock_paths)
    plan = SubagentLaunchPlan.from_persisted(coord.preflight("researcher").to_persisted_dict())

    with patch.object(SubagentCoordinator, "_run_job") as run_job:
        task_id = await coord.start_with_plan("researcher", "check the logs", plan)

    assert task_id
    run_job.assert_called_once()
    row = await (await coord._get_db()).get_task(task_id)
    assert row is not None
    assert row["task"] == "check the logs"
    stored = json.loads(row["launch_plan"])
    assert stored["canonical_manifest"]["effective_tools"] == list(plan.effective_tools)
    assert stored["plan_id"] == plan.plan_id


@pytest.mark.asyncio
async def test_start_with_plan_refuses_a_not_ready_plan(mock_paths) -> None:
    from src.sdk.subagent_capabilities import SubagentLaunchRejected

    coord = await _coordinator_with_profile(mock_paths)
    ready = coord.preflight("researcher")
    unready = ready.model_copy(update={"ready": False})

    with pytest.raises(SubagentLaunchRejected):
        await coord.start_with_plan("researcher", "nope", unready)

    # Nothing may be queued.
    assert await _queued_task_count(coord) == 0


@pytest.mark.asyncio
async def test_start_with_plan_rejects_manifest_drift(mock_paths) -> None:
    """A profile edited after the schedule was frozen must not silently re-author."""
    from src.sdk.subagent_capabilities import SubagentLaunchPlan, SubagentManifestDrift

    coord = await _coordinator_with_profile(mock_paths)
    frozen = SubagentLaunchPlan.from_persisted(coord.preflight("researcher").to_persisted_dict())

    # Operator widens the profile after the schedule was created.
    await coord.update("researcher", tools=["files_read", "files_write"])

    with pytest.raises(SubagentManifestDrift) as excinfo:
        await coord.start_with_plan("researcher", "sneaky", frozen)
    assert excinfo.value.code == "manifest_drift"

    assert await _queued_task_count(coord) == 0


@pytest.mark.asyncio
async def test_start_with_plan_rejects_a_deleted_subagent(mock_paths) -> None:
    from src.sdk.subagent_capabilities import SubagentLaunchPlan

    coord = await _coordinator_with_profile(mock_paths)
    frozen = SubagentLaunchPlan.from_persisted(coord.preflight("researcher").to_persisted_dict())
    await coord.delete("researcher")

    with pytest.raises(ValueError, match="not found"):
        await coord.start_with_plan("researcher", "ghost", frozen)

    assert await _queued_task_count(coord) == 0


@pytest.mark.asyncio
async def test_start_with_plan_rejects_a_disabled_subagent(mock_paths) -> None:
    from src.sdk.subagent_capabilities import SubagentLaunchPlan

    coord = await _coordinator_with_profile(mock_paths)
    frozen = SubagentLaunchPlan.from_persisted(coord.preflight("researcher").to_persisted_dict())

    with patch("src.sdk.coordinator._subagent_enabled", return_value=False):
        with pytest.raises(ValueError, match="disabled"):
            await coord.start_with_plan("researcher", "nope", frozen)


@pytest.mark.asyncio
async def test_drift_message_names_the_changed_authority(mock_paths) -> None:
    from src.sdk.subagent_capabilities import SubagentLaunchPlan

    coord = await _coordinator_with_profile(mock_paths)
    frozen = SubagentLaunchPlan.from_persisted(coord.preflight("researcher").to_persisted_dict())
    await coord.update("researcher", tools=["files_read", "files_write"])

    with pytest.raises(ValueError) as excinfo:
        await coord.start_with_plan("researcher", "sneaky", frozen)
    message = str(excinfo.value)
    assert "files_write" in message
    assert "tools" in message


@pytest.mark.asyncio
async def test_start_delegates_to_the_shared_launch_tail(mock_paths) -> None:
    """`start` and `start_with_plan` must not drift into two execution paths."""
    from src.sdk.coordinator import SubagentCoordinator

    coord = await _coordinator_with_profile(mock_paths)

    with patch.object(SubagentCoordinator, "start_with_plan") as delegated:
        plan = coord.preflight("researcher")
        delegated.return_value = "task-xyz"
        task_id = await coord.start("researcher", "hello")

    assert task_id == "task-xyz"
    assert delegated.await_count == 1
    assert delegated.await_args.args[0] == "researcher"
    assert delegated.await_args.args[1] == "hello"
    assert delegated.await_args.args[2].plan_id == plan.plan_id
