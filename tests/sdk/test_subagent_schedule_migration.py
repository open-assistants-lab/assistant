"""#46 Task 6 — scheduler lifecycle and legacy migration.

The legacy `job_results` table never stored trigger metadata (no run_at, no
cron, no timezone), so a legacy `scheduled` row's trigger **cannot** be
recovered. Migration therefore surfaces those rows for review rather than
guessing a time and firing unattended work at it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

MIGRATED = "migrated"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _legacy_db(root: Path) -> Path:
    """Create the legacy job_results DB the way the old scheduler did."""
    path = root / "jobs_results.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_results (
            job_id TEXT PRIMARY KEY,
            user_id TEXT,
            subagent_name TEXT,
            task TEXT,
            status TEXT,
            result TEXT,
            error TEXT,
            completed_at TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return path


def _legacy_row(
    path: Path,
    job_id: str,
    *,
    status: str = "scheduled",
    user_id: str = "alice",
    subagent: str = "researcher",
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT OR REPLACE INTO job_results "
        "(job_id, user_id, subagent_name, task, status, result, error,"
        " completed_at, created_at) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
        (
            job_id,
            user_id,
            subagent,
            f"legacy task {job_id}",
            status,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp root wired into both the scheduler and the schedule store."""
    from src.storage.paths import DataPaths

    per_test = tmp_path / uuid.uuid4().hex
    per_test.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_db(per_test)

    def paths(*_a, **_k):
        return DataPaths(data_path=str(per_test), data_root=str(per_test), user_id="alice")

    monkeypatch.setattr("src.subagent.scheduler.get_paths", paths)
    monkeypatch.setattr("src.subagent.schedules_store.get_paths", paths)
    return legacy


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------


def test_legacy_scheduled_rows_become_needs_review_not_guessed_times(env) -> None:
    """The trigger cannot be recovered, so the row is surfaced, not invented."""
    from src.subagent.schedule_service import migrate_legacy_schedules
    from src.subagent.schedules_store import SubagentScheduleStore

    _legacy_row(env, "subagent_aaa", status="scheduled")
    _legacy_row(env, "subagent_bbb", status="scheduled", user_id="bob")

    result = asyncio.run(migrate_legacy_schedules())
    assert result["migrated"] == 2
    assert result["needs_review"] == 2

    store = SubagentScheduleStore()
    rows = asyncio.run(store.list_for_user("alice"))
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "needs_review"
    assert row["subagent_name"] == "researcher"
    # Nothing invented: the trigger is unrecoverable, and the row says so.
    assert "legacy" in (row["last_error"] or "").lower()
    assert "trigger" in (row["last_error"] or "").lower()
    # The store requires exactly one trigger field, so a sentinel run_at is
    # unavoidable; safety comes from the status, not the timestamp. What matters
    # is that a parked row can never be picked up and fired.
    restorable = {r["schedule_id"] for r in asyncio.run(store.due_for_restore("alice"))}
    assert row["schedule_id"] not in restorable
    # Provenance survives so an operator can reconcile it.
    assert row["manifest"].get("job_id") == "subagent_aaa"


def test_migration_is_reentrant(env) -> None:
    """Running twice must not duplicate; the legacy row is consumed once."""
    from src.subagent.schedule_service import migrate_legacy_schedules
    from src.subagent.schedules_store import SubagentScheduleStore

    _legacy_row(env, "subagent_aaa")
    first = asyncio.run(migrate_legacy_schedules())
    second = asyncio.run(migrate_legacy_schedules())

    assert first["migrated"] == 1
    assert second["migrated"] == 0
    assert second["already_migrated"] == 1

    store = SubagentScheduleStore()
    assert len(asyncio.run(store.list_for_user("alice"))) == 1


def test_non_scheduled_legacy_rows_are_left_alone(env) -> None:
    from src.subagent.schedule_service import migrate_legacy_schedules
    from src.subagent.schedules_store import SubagentScheduleStore

    _legacy_row(env, "done_1", status="completed")
    _legacy_row(env, "fail_1", status="failed")
    _legacy_row(env, "canc_1", status="cancelled")

    result = asyncio.run(migrate_legacy_schedules())
    assert result["migrated"] == 0
    assert asyncio.run(SubagentScheduleStore().list_for_user("alice")) == []


def test_migration_marks_the_legacy_row_as_consumed(env) -> None:
    """The legacy table keeps an audit trail instead of being silently dropped."""
    from src.subagent.schedule_service import migrate_legacy_schedules

    _legacy_row(env, "subagent_aaa")
    asyncio.run(migrate_legacy_schedules())

    conn = sqlite3.connect(env)
    status = conn.execute(
        "SELECT status FROM job_results WHERE job_id = 'subagent_aaa'"
    ).fetchone()[0]
    conn.close()
    assert status == MIGRATED


def test_migration_tolerates_a_missing_legacy_database(tmp_path, monkeypatch) -> None:
    from src.storage.paths import DataPaths
    from src.subagent.schedule_service import migrate_legacy_schedules

    per_test = tmp_path / uuid.uuid4().hex
    per_test.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "src.subagent.scheduler.get_paths",
        lambda *_a, **_k: DataPaths(
            data_path=str(per_test), data_root=str(per_test), user_id="alice"
        ),
    )
    result = asyncio.run(migrate_legacy_schedules())
    assert result["migrated"] == 0


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def test_lifecycle_restore_is_idempotent_and_shutdown_releases(env) -> None:
    """Startup may restore more than once; shutdown must release the singleton."""
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan
    from src.subagent import scheduler as scheduler_module
    from src.subagent.schedule_service import create_schedule

    class _Coord:
        def preflight(self, name):
            return build_launch_plan(
                AgentProfile(name="researcher", tools=["files_read"]),
                "alice",
                "personal",
                ToolSelectionMode.ALLOWLIST,
            )

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    monkey.setattr(
        "src.sdk.subagent_capabilities.resolve_permission", lambda *_a: "allow"
    )

    async def run():
        # A FUTURE instant: restore() correctly expires past one-shots, so a
        # past timestamp would prove nothing about restore working.
        run_at = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat()
        await create_schedule(
            user_id="alice",
            subagent_name="researcher",
            task="lifecycle",
            run_at=run_at,
        )
        sched = scheduler_module.get_scheduler()
        try:
            await scheduler_module.restore_schedules(sched)
            await scheduler_module.restore_schedules(sched)
            jobs = [j for j in sched.get_jobs() if j.id.startswith("sched:")]
            # One trigger per schedule despite restoring twice.
            assert len(jobs) == len({j.id for j in jobs})
            assert jobs
        finally:
            await scheduler_module.shutdown_scheduler()
        assert scheduler_module._scheduler is None
        assert scheduler_module._jobstores == {}

    try:
        asyncio.run(run())
    finally:
        monkey.undo()


def test_the_companion_scheduler_routes_remain_absent() -> None:
    """The companion /scheduler/* surface was removed and must not return."""
    from src.http.main import app

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert not [p for p in paths if p.startswith("/scheduler")], (
        "the removed companion /scheduler/* routes must not reappear"
    )
