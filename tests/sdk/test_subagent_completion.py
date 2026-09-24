"""Durable completion outbox tests for subagent work-queue terminal transitions."""

from __future__ import annotations

import asyncio
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
async def test_blocked_terminal_result_is_durable_and_writes_completion_event(
    tmp_path, monkeypatch
) -> None:
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    db = SubagentWorkQueueDB("user")
    try:
        profile = AgentProfile(name="worker")
        task_id = await db.insert_task(
            "worker",
            "review",
            profile,
            parent_session_id="session-1",
            launch_plan={
                "plan_id": "plan-blocked",
                "effective_tools": ["skills_load"],
                "effective_skills": ["deployment"],
            },
        )
        result = SubagentResult(
            name="worker",
            task="review",
            success=False,
            output="Blocked: approval required for skills_load.",
            error="Runtime approval is required.",
            error_code="approval_required",
            terminal_reason="blocked",
            cost_usd=0.02,
            llm_calls=1,
            effective_tools=["skills_load"],
            effective_skills=["deployment"],
        )

        assert await db.set_failed(
            task_id,
            result.error,
            error_code="approval_required",
            terminal_reason="blocked",
            result=result,
        )
        row = await db.get_task(task_id)
        assert row is not None
        assert row["status"] == TaskStatus.FAILED.value
        assert row["terminal_reason"] == "blocked"
        assert row["error_code"] == "approval_required"
        stored = await db.get_result(task_id)
        assert stored is not None
        assert stored.terminal_reason == "blocked"
        assert stored.error_code == "approval_required"
        assert stored.launch_plan_id == "plan-blocked"
        assert stored.effective_tools == ["skills_load"]
        assert stored.effective_skills == ["deployment"]
        assert stored.llm_calls == 1 and stored.cost_usd == 0.02
        events = await db.list_undelivered_completion_events()
        assert len(events) == 1
        assert events[0]["status"] == TaskStatus.FAILED.value
        assert events[0]["result"]["terminal_reason"] == "blocked"
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
        task_ids = []
        for workspace_id in ("sales", "research"):
            task_id = await db.insert_task(
                "worker",
                f"review {workspace_id}",
                AgentProfile(name="worker"),
                parent_session_id="session-shared",
                launch_plan={"requested_workspace_id": workspace_id},
            )
            task_ids.append(task_id)
            await db.set_completed(
                task_id,
                SubagentResult(name="worker", task=f"review {workspace_id}", success=True, output="done"),
            )
        assert await coordinator.drain_completion_events() == 0
        assert len(await db.list_undelivered_completion_events()) == 2
        await db.close()

        # A fresh connection/coordinator models process restart and replays persisted rows.
        restarted_db = SubagentWorkQueueDB("user")
        coordinator = SubagentCoordinator("user")
        coordinator._db = restarted_db
        unsubscribe = completion_bus.subscribe("user", None, received.append)
        assert await coordinator.drain_completion_events() == 2
        assert {event.task_id for event in received} == set(task_ids)
        assert {event.workspace_id for event in received} == {"sales", "research"}
        assert await coordinator.drain_completion_events() == 0
        unsubscribe()
    finally:
        await db.close()
        if coordinator._db is not db:
            await coordinator._db.close()


@pytest.mark.asyncio
async def test_workspace_coordinators_serialize_completion_event_delivery(
    tmp_path, monkeypatch
) -> None:
    from src.sdk import coordinator as coordinator_module
    from src.sdk import subagent_completion as completion_module
    from src.sdk import subagent_work_queue as work_queue_module
    from src.sdk.coordinator import get_coordinator
    from src.sdk.subagent_completion import SubagentCompletionBus
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr(coordinator_module, "get_paths", lambda **_kwargs: paths)
    monkeypatch.setattr(work_queue_module, "get_paths", lambda _user_id: paths)
    monkeypatch.setattr(completion_module, "completion_bus", SubagentCompletionBus())
    coordinator_module._coordinators.clear()
    work_queue_module._db_cache.clear()

    sales = get_coordinator("user", workspace_id="sales")
    support = get_coordinator("user", workspace_id="support")
    db = await work_queue_module.get_work_queue("user", workspace_id="user")
    sales._db = db
    support._db = db
    task_id = await db.insert_task(
        "worker",
        "review",
        AgentProfile(name="worker"),
        parent_session_id="session-1",
        launch_plan={"requested_workspace_id": "sales"},
    )
    await db.set_completed(
        task_id,
        SubagentResult(name="worker", task="review", success=True, output="done"),
    )

    published: list[str] = []

    async def receive(event) -> None:
        published.append(event.task_id)
        await asyncio.sleep(0.05)

    unsubscribe = completion_module.completion_bus.subscribe("user", None, receive)
    try:
        delivered = await asyncio.gather(
            sales.drain_completion_events(), support.drain_completion_events()
        )
        assert sum(delivered) == 1
        assert published == [task_id]
        assert await db.list_undelivered_completion_events() == []
    finally:
        unsubscribe()
        coordinator_module._coordinators.clear()
        work_queue_module._db_cache.clear()
        await db.close()


@pytest.mark.asyncio
async def test_terminal_event_without_parent_is_acknowledged_but_result_remains_queryable(
    tmp_path, monkeypatch
) -> None:
    from src.sdk.coordinator import SubagentCoordinator
    from src.storage.paths import DataPaths

    paths = DataPaths(data_path=tmp_path, data_root=tmp_path, user_id="user")
    monkeypatch.setattr("src.sdk.subagent_work_queue.get_paths", lambda _user_id: paths)
    monkeypatch.setattr("src.sdk.coordinator.get_paths", lambda **_kwargs: paths)
    db = SubagentWorkQueueDB("user")
    coordinator = SubagentCoordinator("user")
    coordinator._db = db
    try:
        task_id = await db.insert_task(
            "worker", "work", AgentProfile(name="worker"), launch_plan={"plan_id": "no-parent"}
        )
        assert await db.set_completed(
            task_id,
            SubagentResult(name="worker", task="work", success=True, output="durable result"),
        )

        assert await coordinator.drain_completion_events() == 1
        assert await db.list_undelivered_completion_events() == []
        row = await db.get_task(task_id)
        result = await db.get_result(task_id)
        assert row is not None and row["status"] == TaskStatus.COMPLETED.value
        assert result is not None and result.output == "durable result"
    finally:
        await db.close()


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
async def test_run_service_replay_is_idempotent_after_message_commit_before_ack(monkeypatch) -> None:
    from types import SimpleNamespace

    from src.sdk import run_service
    from src.sdk.subagent_completion import SubagentCompletion
    from src.sdk.subagent_models import SubagentResult

    class FakeStore:
        def __init__(self):
            self.rows = []
            self.fail_after_append_once = True

        def get_messages_by_session_id(self, *_args, **_kwargs):
            raise AssertionError("completion consumer must not scan conversation history")

        def add_message_once(self, delivery_key, role, content, metadata, session_id):
            if any(row.metadata["delivery_key"] == delivery_key for row in self.rows):
                return False
            message_metadata = {**metadata, "delivery_key": delivery_key}
            self.rows.append(
                SimpleNamespace(
                    role=role,
                    content=content,
                    metadata=message_metadata,
                    session_id=session_id,
                )
            )
            if self.fail_after_append_once:
                self.fail_after_append_once = False
                raise RuntimeError("simulated process interruption after message commit")
            return True

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

    # The outbox retries after the interrupted callback. Its storage-level key
    # recognizes the already committed message without reading conversation history.
    await run_service.handle_subagent_completion(event)

    assert len(store.rows) == 1
    assert store.rows[0].metadata["task_id"] == "task-1"
    assert store.rows[0].metadata["delivery_key"] == "subagent-completion:task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "terminal_reason", "success"),
    [
        ("completed", "completed", True),
        ("failed", "failed", False),
        ("timed_out", "timed_out", False),
        ("cancelled", "cancelled", False),
        ("failed", "blocked", False),
    ],
)
async def test_each_routable_terminal_outcome_adds_one_parent_message(
    monkeypatch, status, terminal_reason, success
):
    from types import SimpleNamespace

    from src.sdk import run_service
    from src.sdk.subagent_completion import SubagentCompletion
    from src.sdk.subagent_models import SubagentResult

    class FakeStore:
        def __init__(self):
            self.rows = []

        def add_message_once(self, delivery_key, role, content, metadata, session_id):
            if any(row.metadata["delivery_key"] == delivery_key for row in self.rows):
                return False
            self.rows.append(
                SimpleNamespace(
                    role=role,
                    content=content,
                    metadata={**metadata, "delivery_key": delivery_key},
                    session_id=session_id,
                )
            )
            return True

        def get_messages_by_session_id(self, *_args, **_kwargs):
            raise AssertionError("completion consumer must not scan conversation history")

    store = FakeStore()
    monkeypatch.setattr(run_service, "aget_message_store", lambda *_args: _async_value(store))
    monkeypatch.setattr(run_service, "get_user_loop", lambda *_args: None)
    result = SubagentResult(
        name="worker",
        task="work",
        success=success,
        output="done" if success else "terminal outcome",
        terminal_reason=terminal_reason,
    )
    event = SubagentCompletion(
        user_id="user",
        workspace_id="workspace-a",
        session_id="session-a",
        task_id=f"task-{terminal_reason}",
        agent_name="worker",
        status=status,
        result=result,
    )

    await run_service.handle_subagent_completion(event)
    await run_service.handle_subagent_completion(event)

    assert len(store.rows) == 1
    assert store.rows[0].session_id == "session-a"
    assert store.rows[0].metadata["delivery_key"] == f"subagent-completion:task-{terminal_reason}"


@pytest.mark.asyncio
async def test_active_loop_steer_and_durable_append_share_idempotency_key(monkeypatch):
    from src.sdk import run_service
    from src.sdk.subagent_completion import SubagentCompletion
    from src.sdk.subagent_models import SubagentResult

    class FakeStore:
        def __init__(self):
            self.rows = []

        def add_message_once(self, delivery_key, role, content, metadata, session_id):
            if any(row["metadata"]["delivery_key"] == delivery_key for row in self.rows):
                return False
            self.rows.append(
                {
                    "role": role,
                    "content": content,
                    "metadata": {**metadata, "delivery_key": delivery_key},
                    "session_id": session_id,
                }
            )
            return True

    class ActiveLoop:
        def __init__(self):
            self.steer_sink = None
            self.steers = []

        def set_steer_sink(self, sink):
            self.steer_sink = sink

        def steer(self, message):
            self.steers.append(message)

    store = FakeStore()
    loop = ActiveLoop()
    monkeypatch.setattr(run_service, "aget_message_store", lambda *_args: _async_value(store))
    monkeypatch.setattr(run_service, "get_user_loop", lambda *_args: loop)
    event = SubagentCompletion(
        user_id="user",
        workspace_id="workspace-a",
        session_id="session-a",
        task_id="task-active",
        agent_name="worker",
        status="completed",
        result=SubagentResult(name="worker", task="work", success=True, output="done"),
    )

    await run_service.handle_subagent_completion(event)
    assert loop.steer_sink is not None
    loop.steer_sink(event.message())
    await run_service.handle_subagent_completion(event)

    assert len(store.rows) == 1
    assert store.rows[0]["role"] == "user"
    assert store.rows[0]["metadata"]["delivery_key"] == "subagent-completion:task-active"


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
