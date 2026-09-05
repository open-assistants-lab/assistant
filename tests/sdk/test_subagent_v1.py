"""Tests for subagent V1: WorkQueueDB, middlewares, coordinator, models.

Covers:
- WorkQueueDB CRUD (insert, status transitions, progress, instructions, cancel, queries)
- ProgressMiddleware (progress updates, doom loop detection)
- InstructionMiddleware (cancel signal, instruction injection)
- SubagentCoordinator (create, update, invoke, cancel, instruct, delete)
- AgentDef/SubagentResult models
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("AGENT_MODEL", "ollama:minimax-m2.5")


# -- Fixtures --


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_paths(tmp_dir):
    """Patch get_paths to use a temp directory."""
    from src.storage.paths import DataPaths

    mock = DataPaths(data_path=tmp_dir, user_id="test_user", data_root=tmp_dir)
    # Alias old user-level paths to workspace-scoped for backwards test compat
    def temp_workspace_dir(name: str):
        path = Path(tmp_dir) / "workspaces" / "personal" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

        mock.workspace_subagents_dir = lambda: temp_workspace_dir("subagents")
        mock.workspace_memory_dir = lambda: temp_workspace_dir("memory")
        mock.subagents_dir = mock.workspace_subagents_dir
        mock.user_subagents_dir = mock.workspace_subagents_dir
        mock.memory_dir = mock.workspace_memory_dir
    with patch("src.storage.paths.get_paths", return_value=mock):
        with patch("src.sdk.work_queue.get_paths", return_value=mock):
            with patch("src.sdk.coordinator.get_paths", return_value=mock):
                yield mock


@pytest.fixture
async def db(mock_paths):
    from src.sdk.work_queue import WorkQueueDB

    db = WorkQueueDB("test_user")
    yield db
    await db.close()


@pytest.fixture
def profile():
    from agentprofile.models import AgentProfile

    return AgentProfile(
        name="test_agent",
        description="Test agent",
        model="ollama:minimax-m2.5",
        tools=["time_get"],
        max_llm_calls=10,
        cost_limit_usd=0.5,
        timeout_seconds=30,
    )


async def _wait_for_no_background_tasks(coordinator):
    while coordinator._background_tasks:
        await asyncio.sleep(0)


# -- Model Tests --


class TestAgentDef:
    def test_valid_creation(self):
        from agentprofile.models import AgentProfile

        d = AgentProfile(name="my-agent", description="desc")
        assert d.name == "my-agent"
        assert d.max_llm_calls == 50
        assert "subagent_create" not in (d.tools or [])  # recursion guard via capabilities
    def test_invalid_name(self):
        from agentprofile.models import AgentProfile

        with pytest.raises(Exception):
            AgentProfile(name="has spaces")

        with pytest.raises(Exception):
            AgentProfile(name="")

    def test_custom_limits(self):
        from agentprofile.models import AgentProfile

        d = AgentProfile(name="a", max_llm_calls=5, cost_limit_usd=0.01, timeout_seconds=10)
        assert d.max_llm_calls == 5
        assert d.cost_limit_usd == 0.01
        assert d.timeout_seconds == 10

    def test_agent_def_new_fields(self):
        from agentprofile.models import AgentProfile

        d = AgentProfile(
            name="researcher",
            provider_options={"anthropic": {"thinking": {"type": "enabled"}}},
            handoff_instructions="Return concise bullets.",
        )

        assert d.provider_options == {"anthropic": {"thinking": {"type": "enabled"}}}
        assert d.handoff_instructions == "Return concise bullets."


class TestSubagentResult:
    def test_success_result(self):
        from src.sdk.subagent_models import SubagentResult

        r = SubagentResult(name="a", task="t", success=True, output="done")
        assert r.success
        assert not r.truncated
        assert r.error is None

    def test_failure_result(self):
        from src.sdk.subagent_models import SubagentResult

        r = SubagentResult(name="a", task="t", success=False, output="", error="boom")
        assert not r.success
        assert r.error == "boom"


class TestTaskStatus:
    def test_values(self):
        from src.sdk.subagent_models import TaskStatus

        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

    def test_task_status_has_cancelling(self):
        from src.sdk.subagent_models import TaskStatus

        assert TaskStatus.CANCELLING == "cancelling"


# -- WorkQueueDB Tests --


class TestWorkQueueDB:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, db, profile):
        task_id = await db.insert_task("test_agent", "do something", profile)
        assert task_id

        row = await db.get_task(task_id)
        assert row is not None
        assert row["agent_name"] == "test_agent"
        assert row["task"] == "do something"
        assert row["status"] == "pending"
        assert row["user_id"] == "test_user"
        assert row["cancel_requested"] == 0

    @pytest.mark.asyncio
    async def test_set_running(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.set_running(task_id)
        assert ok
        row = await db.get_task(task_id)
        assert row["status"] == "running"
        assert row["started_at"]
        assert row["heartbeat_at"]
        assert row["claimed_by"] is None

    @pytest.mark.asyncio
    async def test_fresh_set_running_task_not_stale_by_default(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)

        count = await db.mark_stale_running_failed()

        assert count == 0
        row = await db.get_task(task_id)
        assert row["status"] == "running"

    @pytest.mark.asyncio
    async def test_set_completed(self, db, profile):
        from src.sdk.subagent_models import SubagentResult

        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)

        result = SubagentResult(name="test_agent", task="t", success=True, output="done", cost_usd=0.01, llm_calls=3)
        ok = await db.set_completed(task_id, result)
        assert ok

        row = await db.get_task(task_id)
        assert row["status"] == "completed"
        stored = json.loads(row["result"])
        assert stored["success"] is True
        assert stored["output"] == "done"

    @pytest.mark.asyncio
    async def test_set_failed(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.set_failed(task_id, "something broke")
        assert ok

        row = await db.get_task(task_id)
        assert row["status"] == "failed"
        assert row["error"] == "something broke"

    @pytest.mark.asyncio
    async def test_set_cancelled(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.set_cancelled(task_id)
        assert ok

        row = await db.get_task(task_id)
        assert row["status"] == "cancelled"
        assert row["cancel_requested"] == 1

    @pytest.mark.asyncio
    async def test_update_progress(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.update_progress(task_id, {"steps_completed": 5, "phase": "executing"})
        assert ok

        row = await db.get_task(task_id)
        progress = json.loads(row["progress"])
        assert progress["steps_completed"] == 5

    @pytest.mark.asyncio
    async def test_add_instruction(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.add_instruction(task_id, "Focus on the top 3")
        assert ok

        row = await db.get_task(task_id)
        instructions = json.loads(row["instructions"])
        assert len(instructions) == 1
        assert instructions[0]["message"] == "Focus on the top 3"

    @pytest.mark.asyncio
    async def test_multiple_instructions(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.add_instruction(task_id, "First instruction")
        await db.add_instruction(task_id, "Second instruction")

        row = await db.get_task(task_id)
        instructions = json.loads(row["instructions"])
        assert len(instructions) == 2

    @pytest.mark.asyncio
    async def test_request_cancel_and_check(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        ok = await db.request_cancel(task_id)
        assert ok

        assert await db.is_cancel_requested(task_id)

    @pytest.mark.asyncio
    async def test_claim_pending_task_once(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)

        first = await db.claim_task(task_id, worker_id="worker-a")
        second = await db.claim_task(task_id, worker_id="worker-b")

        assert first is True
        assert second is False
        row = await db.get_task(task_id)
        assert row["status"] == "running"
        assert row["claimed_by"] == "worker-a"
        assert row["claimed_at"]
        assert row["heartbeat_at"]

    @pytest.mark.asyncio
    async def test_request_cancel_sets_cancelling_for_running_task(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.claim_task(task_id, worker_id="worker-a")

        ok = await db.request_cancel(task_id)

        assert ok
        row = await db.get_task(task_id)
        assert row["cancel_requested"] == 1
        assert row["status"] == "cancelling"

    @pytest.mark.asyncio
    async def test_set_failed_does_not_overwrite_cancellation(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.claim_task(task_id, worker_id="worker-a")
        await db.request_cancel(task_id)

        ok = await db.set_failed(task_id, "boom")

        row = await db.get_task(task_id)
        assert not ok
        assert row["status"] == "cancelling"
        assert row["cancel_requested"] == 1
        assert row["error"] is None

    @pytest.mark.asyncio
    async def test_request_cancel_active_tasks_for_agent_only(self, db, profile):
        from src.sdk.subagent_models import TaskStatus

        pending_id = await db.insert_task("test_agent", "pending", profile)
        running_id = await db.insert_task("test_agent", "running", profile)
        await db.set_running(running_id)
        completed_id = await db.insert_task("test_agent", "completed", profile)
        await db.set_status(completed_id, TaskStatus.COMPLETED)
        other_id = await db.insert_task("other_agent", "running", profile)
        await db.set_running(other_id)

        count = await db.request_cancel_active_tasks_for_agent("test_agent")

        assert count == 2
        assert await db.is_cancel_requested(pending_id)
        assert await db.is_cancel_requested(running_id)
        assert not await db.is_cancel_requested(completed_id)
        assert not await db.is_cancel_requested(other_id)
        pending = await db.get_task(pending_id)
        running = await db.get_task(running_id)
        assert pending["status"] == "cancelled"
        assert pending["completed_at"] is not None
        assert pending["error"] == "cancelled before start"
        assert json.loads(pending["result"])["error"] == "cancelled before start"
        assert running["status"] == "cancelling"
        assert (await db.get_task(completed_id))["status"] == "completed"
        assert (await db.get_task(other_id))["status"] == "running"

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.claim_task(task_id, worker_id="worker-a")
        before = (await db.get_task(task_id))["heartbeat_at"]

        ok = await db.heartbeat(task_id, worker_id="worker-a")

        after = (await db.get_task(task_id))["heartbeat_at"]

        assert ok
        assert after >= before

    @pytest.mark.asyncio
    async def test_mark_stale_running_failed(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.claim_task(task_id, worker_id="worker-a")

        count = await db.mark_stale_running_failed(max_age_seconds=-1)

        assert count >= 1
        row = await db.get_task(task_id)
        assert row["status"] == "failed"
        assert "interrupted by restart" in row["error"]

    @pytest.mark.asyncio
    async def test_is_cancel_requested_nonexistent(self, db):
        assert not await db.is_cancel_requested("nonexistent")

    @pytest.mark.asyncio
    async def test_check_progress_by_parent(self, db, profile):
        t1 = await db.insert_task("test_agent", "task1", profile, parent_id="parent-1")
        t2 = await db.insert_task("test_agent", "task2", profile, parent_id="parent-1")
        t3 = await db.insert_task("test_agent", "task3", profile, parent_id="parent-2")

        tasks = await db.check_progress(parent_id="parent-1")
        assert len(tasks) == 2
        ids = {t["id"] for t in tasks}
        assert t1 in ids
        assert t2 in ids
        assert t3 not in ids

    @pytest.mark.asyncio
    async def test_check_progress_by_status(self, db, profile):
        from src.sdk.subagent_models import TaskStatus

        t1 = await db.insert_task("test_agent", "task1", profile)
        await db.set_running(t1)
        await db.insert_task("test_agent", "task2", profile)

        running = await db.check_progress(status=TaskStatus.RUNNING)
        assert len(running) == 1
        assert running[0]["id"] == t1

    @pytest.mark.asyncio
    async def test_get_task_is_user_level_across_workspace_ids(self, mock_paths, profile):
        from src.sdk.work_queue import WorkQueueDB

        db = WorkQueueDB("test_user", workspace_id="personal")
        other_workspace = WorkQueueDB("test_user", workspace_id="other")
        other_user = WorkQueueDB("other_user", workspace_id="personal")
        try:
            task_id = await db.insert_task("test_agent", "task", profile)

            assert await db.get_task(task_id) is not None
            assert await other_workspace.get_task(task_id) is not None
            assert await other_user.get_task(task_id) is None
        finally:
            await db.close()
            await other_workspace.close()
            await other_user.close()

    @pytest.mark.asyncio
    async def test_check_progress_is_user_level_across_workspace_ids(self, mock_paths, profile):
        from src.sdk.subagent_models import TaskStatus
        from src.sdk.work_queue import WorkQueueDB

        db = WorkQueueDB("test_user", workspace_id="personal")
        other_workspace = WorkQueueDB("test_user", workspace_id="other")
        other_user = WorkQueueDB("other_user", workspace_id="personal")
        try:
            own_id = await db.insert_task("test_agent", "own", profile, parent_id="shared")
            other_workspace_id = await other_workspace.insert_task(
                "test_agent", "other workspace", profile, parent_id="shared"
            )
            other_user_id = await other_user.insert_task(
                "test_agent", "other user", profile, parent_id="shared"
            )
            await db.set_running(own_id)
            await other_workspace.set_running(other_workspace_id)
            await other_user.set_running(other_user_id)

            all_tasks = await db.check_progress()
            parent_tasks = await db.check_progress(parent_id="shared")
            status_tasks = await db.check_progress(status=TaskStatus.RUNNING)
            parent_status_tasks = await db.check_progress(
                parent_id="shared", status=TaskStatus.RUNNING
            )

            assert {t["id"] for t in all_tasks} == {own_id, other_workspace_id}
            assert {t["id"] for t in parent_tasks} == {own_id, other_workspace_id}
            assert {t["id"] for t in status_tasks} == {own_id, other_workspace_id}
            assert {t["id"] for t in parent_status_tasks} == {own_id, other_workspace_id}
        finally:
            await db.close()
            await other_workspace.close()
            await other_user.close()

    @pytest.mark.asyncio
    async def test_legacy_workspace_rows_are_visible_to_user_level_reads(self, mock_paths, profile):
        from src.sdk.subagent_models import TaskStatus
        from src.sdk.work_queue import WorkQueueDB

        db = WorkQueueDB("test_user", workspace_id="user")
        other_user = WorkQueueDB("other_user", workspace_id="user")
        try:
            conn = await db._get_db()
            await conn.execute(
                """INSERT INTO work_queue
                (id, user_id, workspace_id, agent_name, task, status, progress, config,
                 instructions, cancel_requested, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "legacy-personal",
                    "test_user",
                    "personal",
                    "test_agent",
                    "legacy personal task",
                    TaskStatus.RUNNING.value,
                    "{}",
                    profile.model_dump_json(),
                    "[]",
                    0,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
            await conn.execute(
                """INSERT INTO work_queue
                (id, user_id, workspace_id, agent_name, task, status, progress, config,
                 instructions, cancel_requested, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "legacy-project",
                    "test_user",
                    "project-x",
                    "test_agent",
                    "legacy project task",
                    TaskStatus.PENDING.value,
                    "{}",
                    profile.model_dump_json(),
                    "[]",
                    0,
                    "2026-01-01T00:00:01Z",
                    "2026-01-01T00:00:01Z",
                ),
            )
            await conn.commit()

            assert await db.get_task("legacy-personal") is not None
            assert await other_user.get_task("legacy-personal") is None
            assert {task["id"] for task in await db.check_progress()} >= {
                "legacy-personal",
                "legacy-project",
            }
            assert {task["id"] for task in await db.check_progress(status=TaskStatus.RUNNING)} == {
                "legacy-personal"
            }
        finally:
            await db.close()
            await other_user.close()

    @pytest.mark.asyncio
    async def test_legacy_workspace_rows_are_cancelled_by_user_level_agent_cancel(
        self, mock_paths, profile
    ):
        from src.sdk.subagent_models import TaskStatus
        from src.sdk.work_queue import WorkQueueDB

        db = WorkQueueDB("test_user", workspace_id="user")
        try:
            conn = await db._get_db()
            for task_id, workspace_id, status in [
                ("legacy-pending", "personal", TaskStatus.PENDING.value),
                ("legacy-running", "project-x", TaskStatus.RUNNING.value),
            ]:
                await conn.execute(
                    """INSERT INTO work_queue
                    (id, user_id, workspace_id, agent_name, task, status, progress, config,
                     instructions, cancel_requested, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_id,
                        "test_user",
                        workspace_id,
                        "test_agent",
                        "legacy task",
                        status,
                        "{}",
                        profile.model_dump_json(),
                        "[]",
                        0,
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                    ),
                )
            await conn.commit()

            assert await db.request_cancel_active_tasks_for_agent("test_agent") == 2
            assert (await db.get_task("legacy-pending"))["status"] == TaskStatus.CANCELLED.value
            assert (await db.get_task("legacy-running"))["status"] == TaskStatus.CANCELLING.value
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_get_result(self, db, profile):
        from src.sdk.subagent_models import SubagentResult

        task_id = await db.insert_task("test_agent", "t", profile)
        result = SubagentResult(name="test_agent", task="t", success=True, output="hello")
        await db.set_completed(task_id, result)

        stored = await db.get_result(task_id)
        assert stored is not None
        assert stored.success
        assert stored.output == "hello"

    @pytest.mark.asyncio
    async def test_get_result_nonexistent(self, db):
        result = await db.get_result("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_config_frozen(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        row = await db.get_task(task_id)
        config_data = json.loads(row["config"])
        assert config_data["name"] == "test_agent"
        assert config_data["max_llm_calls"] == 10


# -- SubagentContext Tests --


class TestSubagentContext:
    """Tests for SubagentContext — replaces ProgressMiddleware + InstructionMiddleware."""

    @pytest.mark.asyncio
    async def test_record_tool_call_returns_step(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        step = ctx.record_tool_call("time_get", '{"tz": "UTC"}')
        assert step == 1
        step = ctx.record_tool_call("files_read", '{"path": "/a.txt"}')
        assert step == 2

    @pytest.mark.asyncio
    async def test_doom_detected_with_same_calls(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        assert ctx.doom_detected is False

        for _ in range(3):
            ctx.record_tool_call("time_get", '{"tz": "UTC"}')

        assert ctx.doom_detected is True

    @pytest.mark.asyncio
    async def test_no_doom_with_different_args(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        ctx.record_tool_call("files_read", '{"path": "/a.txt"}')
        ctx.record_tool_call("files_read", '{"path": "/b.txt"}')
        ctx.record_tool_call("files_read", '{"path": "/c.txt"}')

        assert ctx.doom_detected is False

    @pytest.mark.asyncio
    async def test_no_doom_with_different_tools(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        ctx.record_tool_call("time_get", "{}")
        ctx.record_tool_call("files_read", '{"path": "/a.txt"}')
        ctx.record_tool_call("shell_execute", '{"command": "ls"}')

        assert ctx.doom_detected is False

    @pytest.mark.asyncio
    async def test_doom_clears_after_new_tool(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        for _ in range(3):
            ctx.record_tool_call("time_get", '{"tz": "UTC"}')
        assert ctx.doom_detected is True

        ctx.record_tool_call("files_read", '{"path": "/a.txt"}')
        assert ctx.doom_detected is False

    @pytest.mark.asyncio
    async def test_cancel_event_not_set_by_default(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        assert ctx.cancel_event.is_set() is False

    @pytest.mark.asyncio
    async def test_cancel_event_sets(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        ctx.cancel_event.set()
        assert ctx.cancel_event.is_set() is True

    @pytest.mark.asyncio
    async def test_instructions_queue_drain(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        assert ctx.instructions.empty()

        await ctx.instructions.put("Focus on the top 3")
        await ctx.instructions.put("Use Python only")

        msgs = []
        while not ctx.instructions.empty():
            msgs.append(await ctx.instructions.get())

        assert len(msgs) == 2
        assert msgs[0] == "Focus on the top 3"
        assert msgs[1] == "Use Python only"

    @pytest.mark.asyncio
    async def test_on_progress_callback(self):
        from src.sdk.subagent_context import SubagentContext

        calls: list[tuple[int, str, str]] = []

        async def mock_cb(step: int, phase: str, msg: str) -> None:
            calls.append((step, phase, msg))

        ctx = SubagentContext(on_progress=mock_cb)
        ctx.record_tool_call("time_get", '{"tz": "UTC"}')
        await ctx.on_progress(1, "executing", "Called time_get")

        assert len(calls) == 1
        assert calls[0] == (1, "executing", "Called time_get")


# -- SubagentCancelledError Tests --


class TestSubagentCancelledError:
    def test_exception_message(self):
        from src.sdk.subagent_context import SubagentCancelledError

        exc = SubagentCancelledError("task-123")
        assert exc.task_id == "task-123"
        assert "task-123" in str(exc)
        assert "cancelled by supervisor" in str(exc)

    def test_exception_with_custom_reason(self):
        from src.sdk.subagent_context import SubagentCancelledError

        exc = SubagentCancelledError("task-456", "doom loop detected")
        assert exc.task_id == "task-456"
        assert "doom loop detected" in str(exc)


# -- Coordinator Tests --


class TestSubagentCoordinator:
    def test_inline_delegate_does_not_publish_to_parent_session(self):
        import inspect

        from src.sdk.coordinator import SubagentCoordinator

        assert "parent_session_id" not in inspect.signature(SubagentCoordinator.delegate).parameters

    def test_default_tools_exclude_recursive_subagent_tools(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        class FakeTool:
            def __init__(self, name: str):
                self.name = name

        tools = [
            FakeTool("time_get"),
            FakeTool("subagent_start"),
            FakeTool("subagent_tasks"),
        ]

        with patch("src.sdk.native_tools.get_native_tools", return_value=tools):
            resolved = _build_tools_for_subagent(AgentProfile(name="researcher"))

        names = {tool.name for tool in resolved}
        assert "time_get" in names
        assert "subagent_start" not in names
        assert "subagent_tasks" not in names

    def test_build_tools_removes_subagent_and_extra_memory_tools(self, profile):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        d = AgentProfile(name="a", tools=[])
        names = {t.name for t in _build_tools_for_subagent(d)}

        assert "message_search" in names
        assert not any(n.startswith("subagent_") for n in names)
        assert "memory_profile" not in names
        assert "memory_reflection" not in names

    def test_build_tools_allowlist_still_includes_message_search(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        d = AgentProfile(name="a", tools=["time_get"])
        names = {t.name for t in _build_tools_for_subagent(d)}

        assert "time_get" in names
        assert "message_search" in names

    def test_build_tools_includes_skills_load_when_skills_configured(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        d = AgentProfile(name="a", tools=["time_get"], skills=["skill-creator"])
        names = {t.name for t in _build_tools_for_subagent(d)}

        assert "skills_load" in names

    def test_build_tools_removes_skill_management_tools(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        class FakeTool:
            def __init__(self, name: str):
                self.name = name

        tools = [
            FakeTool("time_get"),
            FakeTool("message_search"),
            FakeTool("skill_create"),
            FakeTool("skill_delete"),
            FakeTool("skills_load"),
        ]

        with patch("src.sdk.native_tools.get_native_tools", return_value=tools):
            resolved = _build_tools_for_subagent(AgentProfile(name="a", tools=[]))

        names = {tool.name for tool in resolved}
        assert "time_get" in names
        assert "message_search" in names
        assert "skill_delete" not in names

    def test_build_tools_filters_user_disabled_tools(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_tools_for_subagent

        class FakeTool:
            def __init__(self, name: str):
                self.name = name

        (mock_paths.root / "capabilities.yaml").write_text(
            "tools:\n  time_get: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        tools = [FakeTool("time_get"), FakeTool("message_search")]

        with patch("src.sdk.native_tools.get_native_tools", return_value=tools):
            names = {
                tool.name
                for tool in _build_tools_for_subagent(
                    AgentProfile(name="a", tools=["time_get", "message_search"]),
                    user_id="test_user",
                )
            }

        assert "time_get" not in names
        assert "message_search" in names

    def test_validate_agent_def_rejects_denied_memory_tool(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import validate_agent_def

        errors = validate_agent_def(AgentProfile(name="a", tools=["memory_profile"]))

        assert any("Memory tool" in e for e in errors)

    def test_validate_agent_def_rejects_unknown_skill(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import validate_agent_def

        class FakeSkillRegistry:
            def get_skill(self, name: str):
                return None

        with patch("src.skills.registry.get_skill_registry", return_value=FakeSkillRegistry()):
            errors = validate_agent_def(AgentProfile(name="a", skills=["missing-skill"]))

        assert any("Unknown skill" in e for e in errors)

    def test_validate_agent_def_rejects_non_positive_limits(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import validate_agent_def

        errors = validate_agent_def(
            AgentProfile(name="a", max_llm_calls=0, cost_limit_usd=0, timeout_seconds=0)
        )

        assert "max_llm_calls must be positive" in errors
        assert "cost_limit_usd must be positive" in errors
        assert "timeout_seconds must be positive" in errors

    def test_validate_agent_def_allows_default_future_subagent_denylist_names(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import validate_agent_def

        errors = validate_agent_def(AgentProfile(name="a"))
        assert not errors

    def test_build_system_prompt_lists_skills_without_inlining_content(self):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_system_prompt

        class FakeSkillRegistry:
            def get_skill(self, name: str):
                return {
                    "name": name,
                    "description": "Create reusable skills.",
                    "content": "SECRET FULL SKILL CONTENT",
                }

        profile = AgentProfile(name="a", skills=["skill-creator"])
        with patch("src.skills.registry.get_skill_registry", return_value=FakeSkillRegistry()):
            prompt = _build_system_prompt(profile, user_id="test_user", workspace_id="personal")

        assert "## Available Skills" in prompt
        assert "skill-creator" in prompt
        assert "Create reusable skills." in prompt
        assert "skills_load(skill_name=...)" in prompt
        assert "SECRET FULL SKILL CONTENT" not in prompt

    @pytest.mark.asyncio
    async def test_run_loop_passes_provider_options_and_user_workspace_to_agent_loop(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.messages import Message

        captured_run_config = None
        captured_workspace_id = None
        captured_provider_args = None

        class FakeAgentLoop:
            def __init__(self, **kwargs):
                nonlocal captured_run_config, captured_workspace_id
                captured_run_config = kwargs["run_config"]
                captured_workspace_id = kwargs["workspace_id"]

            async def run(self, messages, *, cost_tracker):
                return [*messages, Message.assistant("done")]

        provider_options = {"anthropic": {"thinking": {"type": "enabled"}}}
        profile = AgentProfile(name="researcher", provider_options=provider_options)
        coord = SubagentCoordinator("test_user", workspace_id="sales")

        def fake_create_model_from_config(*args, **kwargs):
            nonlocal captured_provider_args
            captured_provider_args = (args, kwargs)
            return object()

        with patch("src.sdk.providers.factory.create_model_from_config", fake_create_model_from_config):
            with patch("src.sdk.coordinator._build_tools_for_subagent", return_value=[]):
                with patch("src.sdk.coordinator._build_system_prompt", return_value="system"):
                    with patch("src.sdk.loop.AgentLoop", FakeAgentLoop):
                        await coord._run_loop("task-1", profile, "do it", object())

        assert captured_run_config is not None
        assert captured_run_config.provider_options == provider_options
        assert captured_workspace_id == "user"
        assert captured_provider_args is not None
        assert captured_provider_args[1] == {"user_id": "test_user"}

    def test_schema_validation_does_not_inject_provider_specific_options(self):
        from src.sdk.coordinator import _schema_provider_options

        class FakeProvider:
            provider_id = "openai"

        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        existing = {"openai": {"temperature": 0}}

        assert _schema_provider_options(FakeProvider(), "openai:gpt-4o", existing, schema) == existing

    @pytest.mark.asyncio
    async def test_create_reload_preserves_provider_options_and_output_schema(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        provider_options = {"anthropic": {"thinking": {"type": "enabled"}}}
        schema = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        profile = AgentProfile(
            name="structured",
            provider_options=provider_options,
            output_schema_def=schema,
        )
        coord = SubagentCoordinator("test_user", workspace_id="sales")

        await coord.create(profile)
        loaded = coord.load_def("structured")

        assert loaded is not None
        assert loaded.provider_options == provider_options
        assert loaded.output_schema_def == schema

    @pytest.mark.asyncio
    async def test_update_can_clear_companion_schema_and_provider_options(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        profile = AgentProfile(
            name="structured",
            provider_options={"anthropic": {"thinking": {"type": "enabled"}}},
            output_schema_def={"type": "object", "properties": {"answer": {"type": "string"}}},
        )
        coord = SubagentCoordinator("test_user", workspace_id="sales")
        await coord.create(profile)

        await coord.update("structured", provider_options={}, output_schema_def={})
        loaded = coord.load_def("structured")

        assert loaded is not None
        assert loaded.provider_options == {}
        assert loaded.output_schema_def is None
        assert not (coord.base_path / "structured" / "provider.json").exists()
        assert not (coord.base_path / "structured" / "output-schema.json").exists()

    @pytest.mark.asyncio
    async def test_run_loop_enforces_output_schema_with_one_retry(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.messages import Message, Usage

        captured_run_config = None
        calls = 0
        prior_call_counts = []

        class FakeProvider:
            provider_id = "openai"

        class FakeAgentLoop:
            def __init__(self, **kwargs):
                nonlocal captured_run_config
                captured_run_config = kwargs["run_config"]
                self.run_config = captured_run_config

            async def run(self, messages, *, cost_tracker):
                nonlocal calls
                calls += 1
                prior_call_counts.append(cost_tracker.llm_calls)
                if calls == 1:
                    cost_tracker.add_usage(input_tokens=1, output_tokens=1)
                    return [
                        *messages,
                        Message.assistant(
                            '{"answer": "bad", "extra": true}',
                            usage=Usage(input_tokens=1, output_tokens=1),
                        ),
                    ]
                return [*messages, Message.assistant('{"answer": "done"}')]

        schema = {
            "type": "object",
            "required": ["answer"],
            "additionalProperties": False,
            "properties": {"answer": {"type": "string", "enum": ["done"]}},
        }
        profile = AgentProfile(name="structured", output_schema_def=schema, max_llm_calls=2)
        coord = SubagentCoordinator("test_user", workspace_id="sales")

        with patch("src.sdk.providers.factory.create_model_from_config", return_value=FakeProvider()):
            with patch("src.sdk.coordinator._build_tools_for_subagent", return_value=[]):
                with patch("src.sdk.coordinator._build_system_prompt", return_value="system"):
                    with patch("src.sdk.loop.AgentLoop", FakeAgentLoop):
                        result = await coord._run_loop("task-1", profile, "do it", object())

        assert calls == 2
        assert prior_call_counts == [0, 1]
        assert captured_run_config is not None
        assert captured_run_config.provider_options is None
        assert result.output == '{"answer": "done"}'
        assert result.structured_output == {"answer": "done"}

    @pytest.mark.asyncio
    async def test_background_cancel_after_execution_publishes_cancelled_completion(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.subagent_models import SubagentResult, TaskStatus

        coordinator = SubagentCoordinator("test_user")
        await coordinator.create(profile)
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile, None)
        published = []

        async def fake_run_loop(task_id_, frozen_agent_def, task, db_, ctx=None):
            await db_.request_cancel(task_id_)
            return SubagentResult(name=frozen_agent_def.name, task=task, success=True, output="done")

        async def fake_publish(task_id_, agent_name, status, result, error, parent_session_id):
            published.append((status, result, error, parent_session_id))

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        monkeypatch.setattr(coordinator, "_publish_completion", fake_publish)

        await coordinator._run_job(task_id, parent_session_id="session-1")

        assert published == [(TaskStatus.CANCELLED.value, None, "cancelled", "session-1")]

    @pytest.mark.asyncio
    async def test_background_timeout_losing_cancel_race_publishes_cancelled_completion(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.subagent_models import TaskStatus

        coordinator = SubagentCoordinator("test_user")
        await coordinator.create(profile)
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile, None)
        published = []

        async def fake_run_loop(*args, **kwargs):
            raise TimeoutError

        async def fake_publish(task_id_, agent_name, status, result, error, parent_session_id):
            published.append((status, result, error, parent_session_id))

        original_set_failed = db.set_failed

        async def cancelled_set_failed(task_id_, error):
            await db.request_cancel(task_id_)
            return await original_set_failed(task_id_, error)

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        monkeypatch.setattr(coordinator, "_publish_completion", fake_publish)
        monkeypatch.setattr(db, "set_failed", cancelled_set_failed)

        await coordinator._run_job(task_id, parent_session_id="session-1")

        assert published == [(TaskStatus.CANCELLED.value, None, "cancelled", "session-1")]

    @pytest.mark.asyncio
    async def test_schema_retry_stops_when_first_run_exhausts_call_budget(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.messages import Message, Usage

        calls = 0

        class FakeProvider:
            provider_id = "openai"

        class FakeAgentLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self, messages, *, cost_tracker):
                nonlocal calls
                calls += 1
                cost_tracker.add_usage(input_tokens=1)
                return [
                    *messages,
                    Message.assistant('{"answer": false}', usage=Usage(input_tokens=1)),
                ]

        profile = AgentProfile(
            name="structured",
            max_llm_calls=1,
            output_schema_def={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
        )
        coord = SubagentCoordinator("test_user", workspace_id="sales")

        with patch("src.sdk.providers.factory.create_model_from_config", return_value=FakeProvider()):
            with patch("src.sdk.coordinator._build_tools_for_subagent", return_value=[]):
                with patch("src.sdk.coordinator._build_system_prompt", return_value="system"):
                    with patch("src.sdk.loop.AgentLoop", FakeAgentLoop):
                        with pytest.raises(ValueError, match="no remaining budget"):
                            await coord._run_loop("task-1", profile, "do it", object())

        assert calls == 1

    @pytest.mark.asyncio
    async def test_schema_retry_stops_when_first_run_exhausts_cost_budget(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.messages import Message, Usage
        from src.sdk.providers.base import ModelCost, ModelInfo

        calls = 0

        class FakeProvider:
            provider_id = "openai"

        class FakeAgentLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self, messages, *, cost_tracker):
                nonlocal calls
                calls += 1
                cost_tracker.add_usage(input_tokens=1)
                return [
                    *messages,
                    Message.assistant('{"answer": false}', usage=Usage(input_tokens=1)),
                ]

        profile = AgentProfile(
            name="structured",
            max_llm_calls=2,
            cost_limit_usd=0.000001,
            output_schema_def={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
        )
        coord = SubagentCoordinator("test_user", workspace_id="sales")
        model_info = ModelInfo(cost=ModelCost(input=1.0))

        with patch("src.sdk.providers.factory.create_model_from_config", return_value=FakeProvider()):
            with patch("src.sdk.registry.get_model_info", return_value=model_info):
                with patch("src.sdk.coordinator._build_tools_for_subagent", return_value=[]):
                    with patch("src.sdk.coordinator._build_system_prompt", return_value="system"):
                        with patch("src.sdk.loop.AgentLoop", FakeAgentLoop):
                            with pytest.raises(ValueError, match="cost_limit_usd"):
                                await coord._run_loop("task-1", profile, "do it", object())

        assert calls == 1

    @pytest.mark.asyncio
    async def test_run_loop_fails_after_output_schema_retry_exhausted(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.messages import Message

        calls = 0

        class FakeProvider:
            provider_id = "openai"

        class FakeAgentLoop:
            def __init__(self, **kwargs):
                self.run_config = kwargs["run_config"]

            async def run(self, messages, *, cost_tracker):
                nonlocal calls
                calls += 1
                cost_tracker.add_usage(input_tokens=1)
                return [*messages, Message.assistant('{"ok": false}')]

        schema = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        profile = AgentProfile(name="structured", output_schema_def=schema)
        coord = SubagentCoordinator("test_user", workspace_id="sales")

        with patch("src.sdk.providers.factory.create_model_from_config", return_value=FakeProvider()):
            with patch("src.sdk.coordinator._build_tools_for_subagent", return_value=[]):
                with patch("src.sdk.coordinator._build_system_prompt", return_value="system"):
                    with patch("src.sdk.loop.AgentLoop", FakeAgentLoop):
                        with pytest.raises(ValueError, match="schema validation failed"):
                            await coord._run_loop("task-1", profile, "do it", object())

        assert calls == 2

    @pytest.mark.asyncio
    async def test_start_returns_before_runner_finishes(self, mock_paths, profile, monkeypatch):
        from src.sdk.coordinator import SubagentCoordinator

        coordinator = SubagentCoordinator("test_user")
        await coordinator.create(profile)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_run_job(task_id: str, ctx=None):
            started.set()
            await finish.wait()

        monkeypatch.setattr(coordinator, "_run_job", fake_run_job)

        task_id = await coordinator.start("test_agent", "do work")

        await asyncio.wait_for(started.wait(), timeout=1)
        row = await (await coordinator._get_db()).get_task(task_id)
        assert row is not None
        assert row["status"] in {"pending", "running"}
        finish.set()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_start_retains_background_task_until_completion(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator

        coordinator = SubagentCoordinator("test_user")
        await coordinator.create(profile)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_run_job(task_id: str, ctx=None):
            started.set()
            await finish.wait()

        monkeypatch.setattr(coordinator, "_run_job", fake_run_job)

        await coordinator.start("test_agent", "do work")

        await asyncio.wait_for(started.wait(), timeout=1)
        assert len(coordinator._background_tasks) == 1

        finish.set()
        await asyncio.wait_for(_wait_for_no_background_tasks(coordinator), timeout=1)
        assert coordinator._background_tasks == set()

    @pytest.mark.asyncio
    async def test_start_freezes_config_snapshot(self, mock_paths, profile, monkeypatch):
        from src.sdk.coordinator import SubagentCoordinator

        coordinator = SubagentCoordinator("test_user")
        await coordinator.create(profile)

        async def fake_run_job(task_id: str, ctx=None):
            return None

        monkeypatch.setattr(coordinator, "_run_job", fake_run_job)
        task_id = await coordinator.start("test_agent", "do work")
        await coordinator.update("test_agent", model="changed:model")

        row = await (await coordinator._get_db()).get_task(task_id)
        config = json.loads(row["config"])
        assert config["model"] == profile.model

    @pytest.mark.asyncio
    async def test_run_job_claims_pending_task_and_marks_completed(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.subagent_models import SubagentResult

        coordinator = SubagentCoordinator("test_user")
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile)

        async def fake_run_loop(task_id_: str, frozen_agent_def, task: str, db, ctx=None):
            return SubagentResult(
                name=frozen_agent_def.name,
                task=task,
                success=True,
                output=f"completed {task_id_}",
            )

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)

        await coordinator._run_job(task_id)

        row = await db.get_task(task_id)
        assert row["status"] == "completed"
        assert row["claimed_by"] is not None
        result = json.loads(row["result"])
        assert result["output"] == f"completed {task_id}"

    @pytest.mark.asyncio
    async def test_run_job_preserves_cancel_racing_with_completion(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.subagent_models import SubagentResult

        coordinator = SubagentCoordinator("test_user")
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile)

        async def fake_run_loop(task_id_: str, frozen_agent_def, task: str, db, ctx=None):
            return SubagentResult(
                name=frozen_agent_def.name,
                task=task,
                success=True,
                output="done",
            )

        original_set_completed = db.set_completed

        async def cancel_before_complete(task_id: str, result):
            await db.request_cancel(task_id)
            return await original_set_completed(task_id, result)

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        monkeypatch.setattr(db, "set_completed", cancel_before_complete)

        await coordinator._run_job(task_id)

        row = await db.get_task(task_id)
        assert row["status"] == "cancelled"
        assert row["cancel_requested"] == 1
        result = json.loads(row["result"])
        assert result["error"] == "cancelled by supervisor"

    @pytest.mark.asyncio
    async def test_run_job_preserves_cancel_racing_with_error_failure(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator

        coordinator = SubagentCoordinator("test_user")
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile)

        async def fake_run_loop(task_id_: str, frozen_agent_def, task: str, db, ctx=None):
            raise RuntimeError("boom")

        original_set_failed = db.set_failed

        async def cancel_before_fail(task_id: str, error: str):
            await db.request_cancel(task_id)
            return await original_set_failed(task_id, error)

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        monkeypatch.setattr(db, "set_failed", cancel_before_fail)

        await coordinator._run_job(task_id)

        row = await db.get_task(task_id)
        assert row["status"] == "cancelled"
        assert row["cancel_requested"] == 1
        result = json.loads(row["result"])
        assert result["error"] == "cancelled by supervisor"

    @pytest.mark.asyncio
    async def test_run_job_preserves_cancel_racing_with_timeout_failure(
        self, mock_paths, profile, monkeypatch
    ):
        from src.sdk.coordinator import SubagentCoordinator

        coordinator = SubagentCoordinator("test_user")
        db = await coordinator._get_db()
        task_id = await db.insert_task("test_agent", "do work", profile)

        async def fake_run_loop(task_id_: str, frozen_agent_def, task: str, db, ctx=None):
            raise TimeoutError

        original_set_failed = db.set_failed

        async def cancel_before_fail(task_id: str, error: str):
            await db.request_cancel(task_id)
            return await original_set_failed(task_id, error)

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        monkeypatch.setattr(db, "set_failed", cancel_before_fail)

        await coordinator._run_job(task_id)

        row = await db.get_task(task_id)
        assert row["status"] == "cancelled"
        assert row["cancel_requested"] == 1
        result = json.loads(row["result"])
        assert result["error"] == "cancelled by supervisor"

    @pytest.mark.asyncio
    async def test_create_and_load(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        profile = AgentProfile(name="researcher", description="Web researcher", model="ollama:minimax-m2.5")
        await coord.create(profile)

        loaded = coord.load_def("researcher")
        assert loaded is not None
        assert loaded.name == "researcher"
        assert loaded.description == "Web researcher"

    @pytest.mark.asyncio
    async def test_create_persists_profile(self, mock_paths):
        from agentprofile.models import AgentProfile
        from agentprofile.parser import load_profile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        profile = AgentProfile(name="writer", description="Report writer", tools=["time_get"])
        await coord.create(profile)

        profile_path = mock_paths.user_subagents_dir() / "writer" / "PROFILE.md"
        assert profile_path.exists()
        loaded = load_profile(str(profile_path))
        assert loaded.name == "writer"
        assert loaded.tools == ["time_get"]

    @pytest.mark.asyncio
    async def test_definitions_are_user_level_across_workspace_ids(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        alpha = SubagentCoordinator("test_user", workspace_id="alpha")
        beta = SubagentCoordinator("test_user", workspace_id="beta")
        await alpha.create(AgentProfile(name="shared", description="Shared agent"))

        assert alpha.base_path == mock_paths.user_subagents_dir()
        assert beta.base_path == mock_paths.user_subagents_dir()
        loaded = beta.load_def("shared")
        assert loaded is not None
        assert loaded.description == "Shared agent"

    @pytest.mark.asyncio
    async def test_update(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        profile = AgentProfile(name="updater", description="Original", max_llm_calls=10)
        await coord.create(profile)

        updated = await coord.update("updater", description="Updated", max_llm_calls=20)
        assert updated is not None
        assert updated.description == "Updated"
        assert updated.max_llm_calls == 20

        loaded = coord.load_def("updater")
        assert loaded.description == "Updated"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, mock_paths):
        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        result = await coord.update("ghost", description="nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_defs(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        await coord.create(AgentProfile(name="a1", description="Agent 1"))
        await coord.create(AgentProfile(name="a2", description="Agent 2"))

        defs = await coord.list_defs()
        names = {d.name for d in defs}
        assert "a1" in names
        assert "a2" in names

    @pytest.mark.asyncio
    async def test_list_defs_with_scope_ignores_item_scopes(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user", workspace_id="ws1")
        await coord.create(AgentProfile(name="a1", description="Agent 1"))

        defs = await coord.list_defs_with_scope()

        assert [profile.name for profile, _scope in defs] == ["a1"]

    @pytest.mark.asyncio
    async def test_list_defs_includes_disabled_subagents_for_management(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        (mock_paths.root / "capabilities.yaml").write_text(
            "subagents:\n  disabled-agent: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        coord = SubagentCoordinator("test_user", workspace_id="ws1")
        await coord.create(AgentProfile(name="enabled-agent", description="Enabled"))
        await coord.create(AgentProfile(name="disabled-agent", description="Disabled"))

        defs = await coord.list_defs()
        scoped_defs = await coord.list_defs_with_scope()

        assert {profile.name for profile in defs} == {"enabled-agent", "disabled-agent"}
        assert {profile.name for profile, _scope in scoped_defs} == {
            "enabled-agent",
            "disabled-agent",
        }

    @pytest.mark.asyncio
    async def test_start_rejects_disabled_subagent(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        (mock_paths.root / "capabilities.yaml").write_text(
            "subagents:\n  disabled-agent: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        coord = SubagentCoordinator("test_user", workspace_id="ws1")
        await coord.create(AgentProfile(name="disabled-agent", description="Disabled"))

        with pytest.raises(ValueError, match="disabled"):
            await coord.start("disabled-agent", "do work")

    @pytest.mark.asyncio
    async def test_invoke_rejects_disabled_subagent(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        (mock_paths.root / "capabilities.yaml").write_text(
            "subagents:\n  disabled-agent: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        coord = SubagentCoordinator("test_user", workspace_id="ws1")
        await coord.create(AgentProfile(name="disabled-agent", description="Disabled"))

        with pytest.raises(ValueError, match="disabled"):
            await coord.invoke("disabled-agent", "do work")

    @pytest.mark.asyncio
    async def test_delegate_rejects_disabled_subagent(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        (mock_paths.root / "capabilities.yaml").write_text(
            "subagents:\n  disabled-agent: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        coord = SubagentCoordinator("test_user", workspace_id="ws1")
        await coord.create(AgentProfile(name="disabled-agent", description="Disabled"))

        with pytest.raises(ValueError, match="disabled"):
            await coord.delegate("disabled-agent", "do work")

    def test_build_system_prompt_omits_disabled_profile_skills(self, mock_paths, monkeypatch):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import _build_system_prompt

        class FakeRegistry:
            def get_skill(self, name: str):
                return {"name": name, "description": f"{name} description"}

        (mock_paths.root / "capabilities.yaml").write_text(
            "skills:\n  disabled-skill: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.sdk.capabilities.user_capabilities_root", lambda user_id: mock_paths.root
        )
        monkeypatch.setattr("src.skills.registry.get_skill_registry", lambda **kwargs: FakeRegistry())

        prompt = _build_system_prompt(
            AgentProfile(
                name="worker",
                description="Worker",
                skills=["enabled-skill", "disabled-skill"],
            ),
            user_id="test_user",
        )

        assert "enabled-skill" in prompt
        assert "disabled-skill" not in prompt

    def test_get_coordinator_is_cached_by_user_id_only(self, mock_paths):
        from src.sdk import coordinator as coordinator_module

        coordinator_module._coordinators.clear()

        sales = coordinator_module.get_coordinator("test_user", workspace_id="sales")
        support = coordinator_module.get_coordinator("test_user", workspace_id="support")

        assert sales is support
        assert sales.workspace_id == "user"
        assert support.workspace_id == "user"

    @pytest.mark.asyncio
    async def test_work_queue_is_user_level_across_workspace_ids(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.work_queue import _db_cache

        _db_cache.clear()
        sales = SubagentCoordinator("test_user", workspace_id="sales")
        support = SubagentCoordinator("test_user", workspace_id="support")
        profile = AgentProfile(name="a1", description="Agent 1")

        sales_db = await sales._get_db()
        task_id = await sales_db.insert_task("a1", "task", profile)
        support_db = await support._get_db()

        assert support_db is sales_db
        assert await support_db.get_task(task_id) is not None

    @pytest.mark.asyncio
    async def test_get_work_queue_is_cached_by_user_id_only(self, mock_paths):
        from src.sdk.work_queue import _db_cache, get_work_queue

        _db_cache.clear()

        sales = await get_work_queue("test_user", workspace_id="sales")
        support = await get_work_queue("test_user", workspace_id="support")

        assert sales is support
        assert set(_db_cache) == {"test_user"}

    @pytest.mark.asyncio
    async def test_delete(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        await coord.create(AgentProfile(name="deleteme", description="Temporary"))
        assert coord.load_def("deleteme") is not None

        ok = await coord.delete("deleteme")
        assert ok
        assert coord.load_def("deleteme") is None

    @pytest.mark.asyncio
    async def test_delete_requests_cancel_for_active_jobs_only(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.subagent_models import TaskStatus

        coord = SubagentCoordinator("test_user")
        await coord.create(AgentProfile(name="deleteme", description="Temporary"))
        await coord.create(AgentProfile(name="keepme", description="Persistent"))
        db = await coord._get_db()

        profile = coord.load_def("deleteme")
        other_def = coord.load_def("keepme")
        pending_id = await db.insert_task("deleteme", "pending", profile)
        running_id = await db.insert_task("deleteme", "running", profile)
        await db.set_running(running_id)
        cancelling_id = await db.insert_task("deleteme", "cancelling", profile)
        await db.set_status(cancelling_id, TaskStatus.CANCELLING)
        completed_id = await db.insert_task("deleteme", "completed", profile)
        await db.set_status(completed_id, TaskStatus.COMPLETED)
        failed_id = await db.insert_task("deleteme", "failed", profile)
        await db.set_status(failed_id, TaskStatus.FAILED)
        cancelled_id = await db.insert_task("deleteme", "cancelled", profile)
        await db.set_status(cancelled_id, TaskStatus.CANCELLED)
        other_id = await db.insert_task("keepme", "running", other_def)
        await db.set_running(other_id)

        ok = await coord.delete("deleteme")

        assert ok
        assert await db.is_cancel_requested(pending_id)
        assert await db.is_cancel_requested(running_id)
        assert await db.is_cancel_requested(cancelling_id)
        assert not await db.is_cancel_requested(completed_id)
        assert not await db.is_cancel_requested(failed_id)
        assert not await db.is_cancel_requested(cancelled_id)
        assert not await db.is_cancel_requested(other_id)
        pending = await db.get_task(pending_id)
        assert pending["status"] == "cancelled"
        assert pending["completed_at"] is not None
        assert pending["error"] == "cancelled before start"
        assert json.loads(pending["result"])["error"] == "cancelled before start"
        assert (await db.get_task(running_id))["status"] == "cancelling"
        assert (await db.get_task(cancelling_id))["status"] == "cancelling"
        assert (await db.get_task(completed_id))["status"] == "completed"
        assert (await db.get_task(failed_id))["status"] == "failed"
        assert (await db.get_task(cancelled_id))["status"] == "cancelled"
        assert (await db.get_task(other_id))["status"] == "running"

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, mock_paths):
        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        ok = await coord.delete("ghost")
        assert not ok

    @pytest.mark.asyncio
    async def test_cancel_and_instruct(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        await coord.create(AgentProfile(name="canceller", description="Test"))

        db = await coord._get_db()
        task_id = await db.insert_task("canceller", "task", coord.load_def("canceller"))
        await db.set_running(task_id)

        ok = await coord.cancel(task_id)
        assert ok
        assert await db.is_cancel_requested(task_id)

        task_id2 = await db.insert_task("canceller", "task2", coord.load_def("canceller"))
        await db.set_running(task_id2)
        ok = await coord.instruct(task_id2, "Change focus")
        assert ok

        row = await db.get_task(task_id2)
        instructions = json.loads(row["instructions"])
        assert len(instructions) == 1
        assert instructions[0]["message"] == "Change focus"

    @pytest.mark.asyncio
    async def test_check_progress(self, mock_paths):
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        await coord.create(AgentProfile(name="progressor", description="Test"))

        db = await coord._get_db()
        await db.insert_task("progressor", "task1", coord.load_def("progressor"), parent_id="p1")
        await db.insert_task("progressor", "task2", coord.load_def("progressor"), parent_id="p1")

        tasks = await coord.check_progress(parent_id="p1")
        assert len(tasks) == 2


# -- Integration: Full lifecycle --


class TestSubagentLifecycle:
    @pytest.mark.asyncio
    async def test_config_frozen_at_invoke(self, mock_paths):
        """Verify that config is frozen into work_queue when task is inserted,
        even if AgentDef is updated afterwards."""
        from agentprofile.models import AgentProfile

        from src.sdk.coordinator import SubagentCoordinator

        coord = SubagentCoordinator("test_user")
        original = AgentProfile(name="freeze_test", description="Original", max_llm_calls=10)
        await coord.create(original)

        db = await coord._get_db()
        task_id = await db.insert_task("freeze_test", "task", original)
        row = await db.get_task(task_id)
        frozen = json.loads(row["config"])
        assert frozen["max_llm_calls"] == 10

        await coord.update("freeze_test", max_llm_calls=50)
        row = await db.get_task(task_id)
        frozen = json.loads(row["config"])
        assert frozen["max_llm_calls"] == 10

    @pytest.mark.asyncio
    async def test_doom_loop_detection(self, db, profile):
        """SubagentContext doom loop detection via record_tool_call."""
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        for _ in range(4):
            ctx.record_tool_call("time_get", '{"tz": "UTC"}')

        assert ctx.doom_detected is True
        assert ctx._step == 4

    @pytest.mark.asyncio
    async def test_cancel_then_check_status(self, db, profile):
        """Full cancel flow: insert -> cancel_requested -> set_cancelled."""
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)

        await db.request_cancel(task_id)
        assert await db.is_cancel_requested(task_id)

        await db.set_cancelled(task_id)

        row = await db.get_task(task_id)
        assert row["status"] == "cancelled"


# -- Stale Job Recovery --


class TestStaleJobRecovery:
    @pytest.mark.asyncio
    async def test_coordinator_recovers_stale_jobs_on_init(self, tmp_path):
        from datetime import UTC, datetime, timedelta
        from unittest import mock

        import aiosqlite

        from src.sdk.coordinator import SubagentCoordinator

        db_dir = tmp_path / "subagents"
        db_dir.mkdir()

        # Direct DB: insert a stale RUNNING task
        db = await aiosqlite.connect(str(db_dir / "work_queue.db"))
        db.row_factory = aiosqlite.Row
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS work_queue (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT 'personal',
                agent_name TEXT NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress TEXT DEFAULT '{}',
                result TEXT,
                error TEXT,
                instructions TEXT DEFAULT '[]',
                config TEXT DEFAULT '{}',
                cancel_requested INTEGER DEFAULT 0,
                claimed_by TEXT,
                claimed_at TEXT,
                heartbeat_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        stale_time = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
        await db.execute(
            "INSERT INTO work_queue (id, user_id, workspace_id, agent_name, task, status, heartbeat_at, created_at, updated_at) "
            "VALUES ('stale-job', 'test_user', 'user', 'helper', 'do thing', 'running', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (stale_time,)
        )
        await db.commit()
        await db.close()

        # Patch get_paths to use tmp dir (both coordinator and work_queue paths)
        mock_paths = mock.MagicMock()
        mock_paths.workspace_subagents_dir = mock.MagicMock(return_value=db_dir)
        mock_paths.work_queue_db = mock.MagicMock(return_value=db_dir / "work_queue.db")

        with mock.patch('src.sdk.coordinator.get_paths', return_value=mock_paths), \
             mock.patch('src.sdk.work_queue.get_paths', return_value=mock_paths):
            coordinator = SubagentCoordinator("test_user", "test_ws")
            db = await coordinator._get_db()
            await coordinator._recovery_task
            task = await db.get_task("stale-job")
            assert task is not None
            assert task["status"] == "failed"




class TestDoomLoopDetection:
    """Verify doom loop detection uses tool call args via SubagentContext."""

    @pytest.mark.asyncio
    async def test_doom_loop_detects_same_tool_with_same_args(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()
        args_json = '{"path": "/a.txt"}'
        for _ in range(3):
            ctx.record_tool_call("files_read", args_json)

        assert ctx._step == 3
        assert ctx.doom_detected is True

    @pytest.mark.asyncio
    async def test_doom_loop_distinguishes_same_tool_with_different_args(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()

        ctx.record_tool_call("files_read", '{"path": "/a.txt"}')
        ctx.record_tool_call("files_read", '{"path": "/b.txt"}')
        ctx.record_tool_call("files_read", '{"path": "/c.txt"}')

        assert ctx.doom_detected is False

    @pytest.mark.asyncio
    async def test_doom_loop_distinguishes_string_returning_tools(self):
        from src.sdk.subagent_context import SubagentContext

        ctx = SubagentContext()

        ctx.record_tool_call("shell_execute", '{"command": "ls"}')
        ctx.record_tool_call("shell_execute", '{"command": "pwd"}')
        ctx.record_tool_call("shell_execute", '{"command": "date"}')

        assert ctx.doom_detected is False
