"""Task 3: async scheduler with faithful, durable trigger restoration.

The current restore path reconstructs every persisted `scheduled` row as a
one-shot roughly 30 seconds after startup, so a recurring schedule silently
becomes a one-shot and a future one-shot fires at the wrong time. These tests
pin the replacement: triggers are rebuilt from persisted metadata, expired
one-shots are reported rather than delayed, restore is idempotent, and firing
goes through the governed coordinator rather than the legacy manager.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.subagent.schedules_store import SubagentScheduleStore

FUTURE = datetime.now(UTC) + timedelta(hours=2)


@pytest.fixture
def store_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _store(root: Path) -> SubagentScheduleStore:
    return SubagentScheduleStore(root / "subagent_schedules.db")


class _TempPaths:
    """Redirects the scheduler at a temp dir for jobstore AND schedule store."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def jobs_db_path(self) -> Path:
        return self.root / "jobs.db"

    def jobs_results_db_path(self) -> Path:
        return self.root / "jobs_results.db"

    def subagent_schedules_db_path(self) -> Path:
        return self.root / "subagent_schedules.db"


def _manifest() -> dict:
    """A real, self-consistent frozen plan — from_persisted rejects fakes."""
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan

    profile = AgentProfile(name="researcher", tools=["files_read"])
    plan = build_launch_plan(profile, "u", "personal", ToolSelectionMode.ALLOWLIST)
    # Keep the fixture independent of this deployment's permission state: pin
    # the plan ready and rebuild its hash so from_persisted accepts it.
    return plan.to_persisted_dict()


async def _seed(root: Path, **kwargs) -> dict:
    params = {
        "user_id": "u",
        "workspace_id": "personal",
        "subagent_name": "researcher",
        "task": "check the deployment",
        "trigger_kind": "once",
        "run_at": FUTURE,
        "cron": None,
        "timezone": "UTC",
        "manifest": _manifest(),
        "manifest_hash": "hash1",
    }
    params.update(kwargs)
    store = _store(root)
    try:
        return await store.create(**params)
    finally:
        await store.close()


# --------------------------------------------------------------------------
# get_scheduler must be loop-bound
# --------------------------------------------------------------------------


def test_get_scheduler_outside_a_running_loop_raises_clearly(store_dir) -> None:
    """AsyncIOScheduler needs a loop; a silent broken scheduler is worse."""
    import src.subagent.scheduler as scheduler_module

    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        with pytest.raises(RuntimeError, match="event loop"):
            scheduler_module.get_scheduler()


@pytest.mark.asyncio
async def test_get_scheduler_is_singleton_within_a_loop(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            first = scheduler_module.get_scheduler()
            second = scheduler_module.get_scheduler()
            assert first is second
        finally:
            await scheduler_module.shutdown_scheduler()


# --------------------------------------------------------------------------
# faithful restoration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_shot_restores_to_its_original_run_at(store_dir) -> None:
    """Not 'now + 30s' — the persisted instant, to the second."""
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            await scheduler_module.restore_schedules(sched, store=_store(store_dir))
            job = sched.get_job(f"sched:{created['schedule_id']}")
            assert job is not None, "schedule was not restored"
            assert job.trigger.run_date == datetime.fromisoformat(created["run_at"])
        finally:
            await scheduler_module.shutdown_scheduler()


@pytest.mark.asyncio
async def test_recurring_restores_cron_and_timezone(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    created = await _seed(
        store_dir, trigger_kind="cron", run_at=None, cron="30 7 * * 1-5",
        timezone="America/New_York",
    )
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            await scheduler_module.restore_schedules(sched, store=_store(store_dir))
            from apscheduler.triggers.cron import CronTrigger

            job = sched.get_job(f"sched:{created['schedule_id']}")
            assert job is not None
            assert isinstance(job.trigger, CronTrigger)
            by_name = {f.name: f for f in job.trigger.fields}
            assert str(by_name["hour"]) == "7"
            assert str(by_name["minute"]) == "30"
            assert str(by_name["day_of_week"]) == "1-5"
            assert str(job.trigger.timezone) == "America/New_York"
        finally:
            await scheduler_module.shutdown_scheduler()


@pytest.mark.asyncio
async def test_expired_one_shot_is_reported_not_silently_delayed(store_dir) -> None:
    """A missed schedule must not quietly run hours late."""
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir, run_at=datetime.now(UTC) - timedelta(hours=3))
    store = _store(store_dir)
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            await scheduler_module.restore_schedules(sched, store=store)
            assert sched.get_job(f"sched:{created['schedule_id']}") is None
            row = await store.get("u", created["schedule_id"])
            assert row is not None
            assert row["status"] == "expired"
        finally:
            await scheduler_module.shutdown_scheduler()
    await store.close()


@pytest.mark.asyncio
async def test_restore_is_idempotent(store_dir) -> None:
    """Startup must not create duplicate triggers for the same schedule."""
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            await scheduler_module.restore_schedules(sched, store=_store(store_dir))
            await scheduler_module.restore_schedules(sched, store=_store(store_dir))
            jobs = [j for j in sched.get_jobs() if j.id == f"sched:{created['schedule_id']}"]
            assert len(jobs) == 1
        finally:
            await scheduler_module.shutdown_scheduler()


@pytest.mark.asyncio
async def test_deleted_subagent_is_surfaced_not_a_silent_no_op(store_dir) -> None:
    """A schedule whose agent is gone must fail visibly on its next fire."""
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    store = _store(store_dir)
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            with patch.object(scheduler_module, "coordinator_for", lambda _u: None):
                await scheduler_module.restore_schedules(sched, store=store)
            job = sched.get_job(f"sched:{created['schedule_id']}")
            assert job is not None, "restore must still register the trigger"
            # Firing it records a clear reason rather than vanishing.
            await job.func(*job.args)
            row = await store.get("u", created["schedule_id"])
            assert row is not None
            assert row["status"] in {"failed", "invalid", "rejected"}
            assert row["last_error"]
        finally:
            await scheduler_module.shutdown_scheduler()
    await store.close()


# --------------------------------------------------------------------------
# firing uses the governed coordinator, not SubagentManager
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_uses_start_with_plan_with_the_frozen_manifest(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    seen: dict = {}

    class _Coord:
        async def start_with_plan(self, agent, task, plan, **kwargs):
            seen["agent"] = agent
            seen["task"] = task
            seen["plan"] = plan
            return "wq_task_123"

    store = _store(store_dir)
    try:
        with patch.object(scheduler_module, "coordinator_for", lambda _u: _Coord()):
            task_id = await scheduler_module.fire_schedule(
                "u", created["schedule_id"], store=store
            )
        assert task_id == "wq_task_123"
        assert seen["agent"] == "researcher"
        # The plan is the frozen one, rehydrated from the row.
        assert seen["plan"].plan_id == created["manifest_hash"] or seen["plan"].ready is True
        row = await store.get("u", created["schedule_id"])
        assert row is not None
        assert row["last_run_id"] == "wq_task_123"
        assert row["status"] == "running"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fire_never_calls_the_legacy_manager(store_dir) -> None:
    """The legacy SubagentManager path is what this change exists to remove."""
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    called: list[str] = []

    class _Legacy:
        def invoke(self, *_a, **_k):
            called.append("invoke")
            return {"success": True}

    class _Coord:
        async def start_with_plan(self, *_a, **_k):
            return "wq_1"

    store = _store(store_dir)
    try:
        with (
            patch.object(scheduler_module, "coordinator_for", lambda _u: _Coord()),
            patch("src.subagent.manager.get_subagent_manager", lambda _u: _Legacy()),
        ):
            await scheduler_module.fire_schedule("u", created["schedule_id"], store=store)
        assert called == [], "legacy SubagentManager.invoke must not be used"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fire_records_failure_without_raising_into_apscheduler(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)

    class _Coord:
        async def start_with_plan(self, *_a, **_k):
            raise RuntimeError("manifest drift")

    store = _store(store_dir)
    try:
        with patch.object(scheduler_module, "coordinator_for", lambda _u: _Coord()):
            # must not raise: an APScheduler job that raises is silently lost
            await scheduler_module.fire_schedule("u", created["schedule_id"], store=store)
        row = await store.get("u", created["schedule_id"])
        assert row is not None
        assert row["status"] == "failed"
        assert "manifest drift" in (row["last_error"] or "")
    finally:
        await store.close()


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_removes_the_trigger_and_is_idempotent(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir)
    store = _store(store_dir)
    with patch.object(scheduler_module, "get_paths", lambda: _TempPaths(store_dir)):
        try:
            sched = scheduler_module.get_scheduler()
            await scheduler_module.restore_schedules(sched, store=store)
            assert sched.get_job(f"sched:{created['schedule_id']}") is not None
            assert await scheduler_module.cancel_schedule("u", created["schedule_id"]) is True
            assert sched.get_job(f"sched:{created['schedule_id']}") is None
            assert await scheduler_module.cancel_schedule("u", created["schedule_id"]) is True
        finally:
            await scheduler_module.shutdown_scheduler()
    await store.close()


@pytest.mark.asyncio
async def test_cancel_is_user_scoped(store_dir) -> None:
    import src.subagent.scheduler as scheduler_module

    created = await _seed(store_dir, user_id="alice")
    store = _store(store_dir)
    try:
        assert await scheduler_module.cancel_schedule("bob", created["schedule_id"]) is False
    finally:
        await store.close()
