"""Durable completion outbox tests for subagent work-queue terminal transitions."""

from __future__ import annotations

import json

import pytest
from agentprofile.models import AgentProfile

from src.sdk.subagent_models import SubagentResult, TaskStatus
from src.sdk.subagent_work_queue import SubagentWorkQueueDB


@pytest.mark.asyncio
async def test_completion_transition_writes_one_durable_outbox_event(tmp_path, monkeypatch) -> None:
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    db = SubagentWorkQueueDB("user")
    try:
        task_id = await db.insert_task(
            "worker",
            "review",
            AgentProfile(name="worker"),
            parent_session_id="session-1",
            launch_plan={"plan_id": "plan-001", "effective_tools": ["files_read"]},
        )
        assert await db.set_completed(
            task_id,
            SubagentResult(name="worker", task="review", success=True, output="done"),
        )
        assert not await db.set_completed(
            task_id,
            SubagentResult(name="worker", task="review", success=True, output="second"),
        )
        assert not await db.set_cancelled(task_id)
        task_state = await db.get_task(task_id)
        assert task_state is not None and task_state["status"] == "completed"

        events = await db.list_undelivered_completion_events()
        assert len(events) == 1
        assert events[0]["task_id"] == task_id
        assert events[0]["parent_session_id"] == "session-1"
        assert events[0]["status"] == "completed"
        assert events[0]["result"]["output"] == "done"
        task_row = await db.get_task(task_id)
        assert task_row is not None
        assert json.loads(task_row["launch_plan"])["plan_id"] == "plan-001"
        assert events[0]["result"]["launch_plan_id"] == "plan-001"
        assert await db.record_completion_delivery_attempt(task_id)
        assert await db.mark_completion_event_delivered(task_id)
        assert not await db.mark_completion_event_delivered(task_id)
        assert await db.list_undelivered_completion_events() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_coordinator_replays_event_and_acknowledges_only_with_subscriber(
    tmp_path, monkeypatch
) -> None:
    from src.sdk import subagent_completion as completion_module
    from src.sdk.coordinator import SubagentCoordinator
    from src.sdk.subagent_completion import SubagentCompletionBus

    isolated_bus = SubagentCompletionBus()
    monkeypatch.setattr(completion_module, "completion_bus", isolated_bus)
    completion_bus = isolated_bus
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    monkeypatch.setattr("src.sdk.coordinator.get_paths", lambda user_id: paths)
    db = SubagentWorkQueueDB("user")
    coordinator = SubagentCoordinator("user")
    coordinator._db = db
    received = []
    try:
        task_id = await db.insert_task(
            "worker", "review", AgentProfile(name="worker"), parent_session_id="session-1"
        )
        await db.set_completed(
            task_id,
            SubagentResult(name="worker", task="review", success=True, output="done"),
        )
        assert await coordinator.drain_completion_events() == 0
        assert len(await db.list_undelivered_completion_events()) == 1
        await db.close()

        # A fresh connection/coordinator models process restart and replays persisted rows.
        restarted_db = SubagentWorkQueueDB("user")
        coordinator = SubagentCoordinator("user")
        coordinator._db = restarted_db
        unsubscribe = completion_bus.subscribe("user", "session-1", received.append)
        assert await coordinator.drain_completion_events(session_id="session-1") == 1
        assert received[0].task_id == task_id
        assert await coordinator.drain_completion_events(session_id="session-1") == 0
        unsubscribe()
    finally:
        await db.close()
        if coordinator._db is not db:
            await coordinator._db.close()


@pytest.mark.asyncio
async def test_stale_recovery_preserves_requested_cancellation_and_emits_event(
    tmp_path, monkeypatch
) -> None:
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    db = SubagentWorkQueueDB("user")
    try:
        task_id = await db.insert_task(
            "worker", "work", AgentProfile(name="worker"), parent_session_id="session-1"
        )
        await db.set_running(task_id)
        conn = await db._get_db()
        await conn.execute(
            "UPDATE work_queue SET cancel_requested = 1, status = 'cancelling', heartbeat_at = NULL WHERE id = ?",
            (task_id,),
        )
        await conn.commit()

        assert await db.mark_stale_running_failed(max_age_seconds=0) == 1
        row = await db.get_task(task_id)
        assert row is not None and row["status"] == "cancelled"
        assert row["terminal_reason"] == "cancelled"
        events = await db.list_undelivered_completion_events()
        assert len(events) == 1 and events[0]["status"] == "cancelled"
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_run_service_completion_consumer_deduplicates_replayed_task(monkeypatch) -> None:
    from types import SimpleNamespace

    from src.sdk import run_service
    from src.sdk.subagent_completion import SubagentCompletion
    from src.sdk.subagent_models import SubagentResult

    class FakeStore:
        def __init__(self):
            self.rows = []
            self.fail_after_append_once = True

        def get_messages_by_session_id(self, _session_id, limit):
            return self.rows[-limit:]

        def add_message(self, role, content, metadata=None, session_id=None):
            self.rows.append(
                SimpleNamespace(role=role, content=content, metadata=metadata, session_id=session_id)
            )
            if self.fail_after_append_once:
                self.fail_after_append_once = False
                raise RuntimeError("simulated process interruption after message commit")

    store = FakeStore()
    monkeypatch.setattr(run_service, "aget_message_store", lambda *_args: _async_value(store))
    monkeypatch.setattr(run_service, "get_user_loop", lambda *_args: None)
    event = SubagentCompletion(
        user_id="user",
        workspace_id="personal",
        session_id="session",
        task_id="task-1",
        agent_name="worker",
        status="completed",
        result=SubagentResult(name="worker", task="work", success=True, output="done"),
    )

    with pytest.raises(RuntimeError, match="after message commit"):
        await run_service.handle_subagent_completion(event)
    assert len(store.rows) == 1

    # The outbox retries after the interrupted callback; current history scan
    # deduplicates the already-persisted parent message.
    await run_service.handle_subagent_completion(event)

    assert len(store.rows) == 1
    assert store.rows[0].metadata["task_id"] == "task-1"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_delete_pending_agent_task_emits_completion_event(tmp_path, monkeypatch) -> None:
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    db = SubagentWorkQueueDB("user")
    try:
        task_id = await db.insert_task(
            "worker", "work", AgentProfile(name="worker"), parent_session_id="session-1"
        )
        assert await db.request_cancel_active_tasks_for_agent("worker") == 1
        row = await db.get_task(task_id)
        events = await db.list_undelivered_completion_events()
        assert row is not None and row["status"] == "cancelled"
        assert len(events) == 1 and events[0]["task_id"] == task_id
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "expected_status"),
    [("failed", "failed"), ("cancelled", "cancelled"), ("timed_out", "timed_out")],
)
async def test_failure_and_cancellation_write_one_durable_outbox_event(
    tmp_path, monkeypatch, terminal, expected_status
) -> None:
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    db = SubagentWorkQueueDB("user")
    try:
        task_id = await db.insert_task("worker", terminal, AgentProfile(name="worker"))
        if terminal == "failed":
            assert await db.set_failed(task_id, "provider error")
        elif terminal == "timed_out":
            assert await db.set_failed(
                task_id, "timeout", terminal_status=TaskStatus.TIMED_OUT
            )
        else:
            assert await db.set_cancelled(task_id)

        events = await db.list_undelivered_completion_events()
        assert len(events) == 1
        assert events[0]["task_id"] == task_id
        assert events[0]["status"] == expected_status
        task_row = await db.get_task(task_id)
        assert task_row is not None
        assert task_row["terminal_reason"] == expected_status
        assert events[0]["error"] in {"provider error", "cancelled by supervisor", "timeout"}
    finally:
        await db.close()
