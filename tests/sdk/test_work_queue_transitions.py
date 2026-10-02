"""Work-queue transition integrity: #53, #54, #55."""

import asyncio
import json

import pytest
from agentprofile.models import AgentProfile

from src.sdk.subagent_models import SubagentResult, TaskStatus
from src.sdk.subagent_work_queue import SubagentWorkQueueDB


@pytest.fixture
async def db(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    from src.sdk.subagent_work_queue import _db_cache

    _db_cache.clear()
    queue = SubagentWorkQueueDB("test_user")
    yield queue
    await queue.close()
    _db_cache.clear()


@pytest.fixture
def profile():
    return AgentProfile(name="test_agent", description="d", model="test", tools=[], skills=[])


def _result(success: bool = True) -> SubagentResult:
    return SubagentResult(
        name="test_agent", task="t", success=success, output="done", cost_usd=0.0, llm_calls=1
    )


class TestTerminalStatesAreNotResurrected:
    """#54: a late start must not undo a cancellation or a completion."""

    @pytest.mark.asyncio
    async def test_start_is_refused_for_a_cancelled_task(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.request_cancel(task_id)
        assert await db.set_running(task_id) is False
        assert (await db.get_task(task_id))["status"] == TaskStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_start_is_refused_while_cancellation_is_pending(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        await db.request_cancel(task_id)
        assert (await db.get_task(task_id))["status"] == TaskStatus.CANCELLING.value
        assert await db.set_running(task_id) is False
        assert (await db.get_task(task_id))["status"] == TaskStatus.CANCELLING.value

    @pytest.mark.asyncio
    async def test_start_is_refused_for_a_finished_task(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        await db.set_completed(task_id, _result())
        assert await db.set_running(task_id) is False
        assert (await db.get_task(task_id))["status"] == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_start_of_a_pending_task_still_works(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        assert await db.set_running(task_id) is True

    @pytest.mark.asyncio
    async def test_terminal_status_cannot_be_set_directly(self, db, profile):
        """A terminal row must be written by the transactional path, with its
        completion event - not by a bare status update."""
        task_id = await db.insert_task("test_agent", "t", profile)
        assert await db.set_status(task_id, TaskStatus.COMPLETED) is False
        assert (await db.get_task(task_id))["status"] == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_a_terminal_row_cannot_be_moved_back_to_running(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        await db.set_completed(task_id, _result())
        assert await db.set_status(task_id, TaskStatus.RUNNING) is False
        assert (await db.get_task(task_id))["status"] == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_a_live_start_after_cancel_keeps_the_cancellation_winner(self, db, profile):
        """Cancellation before start wins: the body must not run afterwards."""
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.request_cancel(task_id)
        started = await db.set_running(task_id)
        if not started:
            assert (await db.get_task(task_id))["status"] == TaskStatus.CANCELLED.value
            assert await db.set_completed(task_id, _result()) is False


class TestInstructionsAreAtomic:
    """#55: a read-modify-write lost concurrent instructions."""

    @pytest.mark.asyncio
    async def test_concurrent_instructions_all_survive(self, db, profile, monkeypatch):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)

        original_get = db.get_task
        reads = 0
        both_read = asyncio.Event()

        async def interleaving_get(tid):
            """Force the first two callers to read the same snapshot."""
            nonlocal reads
            row = await original_get(tid)
            reads += 1
            if reads <= 2:
                if reads == 2:
                    both_read.set()
                await both_read.wait()
            return row

        monkeypatch.setattr(db, "get_task", interleaving_get)
        results = await asyncio.gather(
            db.add_instruction(task_id, "first"),
            db.add_instruction(task_id, "second"),
        )
        assert all(results)
        stored = json.loads((await original_get(task_id))["instructions"])
        assert [entry["message"] for entry in stored] == ["first", "second"]

    @pytest.mark.asyncio
    async def test_a_terminal_task_rejects_an_instruction(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        await db.add_instruction(task_id, "while running")
        await db.set_completed(task_id, _result())
        assert await db.add_instruction(task_id, "too late") is False
        stored = json.loads((await db.get_task(task_id))["instructions"])
        assert [entry["message"] for entry in stored] == ["while running"]


class TestLiveTasksSurviveRecovery:
    """#53: another coordinator's stale sweep must not fail a live in-process run."""

    @pytest.mark.asyncio
    async def test_a_running_in_process_task_is_not_recovered(self, db, profile, monkeypatch):
        import src.sdk.coordinator as coordinator_mod
        from src.sdk.subagent_context import SubagentContext

        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        # Age the heartbeat well past the recovery window.
        await db._get_db()
        db_handle = await db._get_db()
        await db_handle.execute(
            "UPDATE work_queue SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (task_id,),
        )
        await db_handle.commit()

        monkeypatch.setitem(coordinator_mod._active, task_id, SubagentContext())
        try:
            assert await db.mark_stale_running_failed(max_age_seconds=300) == 0
            assert (await db.get_task(task_id))["status"] == TaskStatus.RUNNING.value
        finally:
            coordinator_mod._active.pop(task_id, None)

    @pytest.mark.asyncio
    async def test_an_abandoned_stale_task_is_still_recovered(self, db, profile):
        task_id = await db.insert_task("test_agent", "t", profile)
        await db.set_running(task_id)
        handle = await db._get_db()
        await handle.execute(
            "UPDATE work_queue SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (task_id,),
        )
        await handle.commit()
        assert await db.mark_stale_running_failed(max_age_seconds=300) == 1
        assert (await db.get_task(task_id))["status"] == TaskStatus.FAILED.value


class TestInvokeClaimsItsTask:
    """#53: an in-process invoke must be identifiable as live."""

    @pytest.mark.asyncio
    async def test_invoke_claims_the_task_with_an_owner_and_heartbeat(self, db, profile, monkeypatch):
        import src.sdk.coordinator as coordinator_mod

        coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")
        monkeypatch.setattr(coordinator, "_get_db", _async_return(db))
        monkeypatch.setattr(coordinator, "preflight", lambda name: _ReadyPlan())
        monkeypatch.setattr(coordinator, "load_def", lambda name: profile)
        monkeypatch.setattr(coordinator_mod, "_subagent_enabled", lambda *a: True)
        seen: dict = {}

        async def fake_run_loop(task_id, *args, **kwargs):
            seen.update(await db.get_task(task_id))
            return _result()

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        task_id = await coordinator.invoke("test_agent", "t")
        assert task_id
        assert seen["status"] == TaskStatus.RUNNING.value
        assert seen["claimed_by"], (
            "invoke() left the task unclaimed, so recovery cannot tell it is live"
        )
        assert seen["heartbeat_at"], "invoke() started a task with no heartbeat"

    @pytest.mark.asyncio
    async def test_invoke_registers_the_task_as_live_in_process(self, db, profile, monkeypatch):
        """Recovery must be able to see that the task is running here."""
        import src.sdk.coordinator as coordinator_mod

        coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")
        monkeypatch.setattr(coordinator, "_get_db", _async_return(db))
        monkeypatch.setattr(coordinator, "preflight", lambda name: _ReadyPlan())
        monkeypatch.setattr(coordinator, "load_def", lambda name: profile)
        monkeypatch.setattr(coordinator_mod, "_subagent_enabled", lambda *a: True)
        active_during_run: list[bool] = []

        async def fake_run_loop(task_id, *args, **kwargs):
            active_during_run.append(task_id in coordinator_mod._active)
            return _result()

        monkeypatch.setattr(coordinator, "_run_loop", fake_run_loop)
        task_id = await coordinator.invoke("test_agent", "t")
        assert active_during_run == [True]
        assert task_id not in coordinator_mod._active, "the task stayed registered after invoke"


class _ReadyPlan:
    ready = True
    effective_tools: list[str] = []
    effective_skills: list[str] = []
    resolved_workspace_id = "personal"

    def to_persisted_dict(self) -> dict:
        return {"plan_id": "p1"}


def _async_return(value):
    async def _get():
        return value

    return _get
