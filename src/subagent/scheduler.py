"""Subagent scheduler using APScheduler."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.app_logging import get_logger
from src.sdk.subagent_capabilities import SubagentLaunchPlan
from src.storage.paths import get_paths
from src.subagent.schedules_store import SubagentScheduleStore

logger = get_logger()


def _get_jobs_db_path() -> Path:
    return get_paths().jobs_db_path()


def _get_results_db_path() -> Path:
    return get_paths().jobs_results_db_path()


_jobstores: dict[str, Any] = {}
_scheduler: AsyncIOScheduler | None = None


def _init_results_db() -> None:
    """Initialize results database."""
    results_path = _get_results_db_path()
    results_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(results_path)
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


def _save_job_result(
    job_id: str,
    user_id: str,
    subagent_name: str,
    task: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Save job result to database. Preserves created_at on updates."""
    conn = sqlite3.connect(_get_results_db_path())
    existing = conn.execute(
        "SELECT created_at FROM job_results WHERE job_id = ?", (job_id,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE job_results
            SET user_id = ?, subagent_name = ?, task = ?, status = ?,
                result = ?, error = ?, completed_at = ?
            WHERE job_id = ?
        """,
            (
                user_id,
                subagent_name,
                task,
                status,
                json.dumps(result) if result else None,
                error,
                datetime.now().isoformat(),
                job_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO job_results
            (job_id, user_id, subagent_name, task, status, result, error, completed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                job_id,
                user_id,
                subagent_name,
                task,
                status,
                json.dumps(result) if result else None,
                error,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Get status of a job from database."""
    conn = sqlite3.connect(_get_results_db_path())
    cursor = conn.execute(
        "SELECT job_id, user_id, subagent_name, task, status, result, error, completed_at FROM job_results WHERE job_id = ?",
        (job_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "job_id": row[0],
        "user_id": row[1],
        "subagent_name": row[2],
        "task": row[3],
        "status": row[4],
        "result": json.loads(row[5]) if row[5] else None,
        "error": row[6],
        "completed_at": row[7],
    }


def _get_jobstores() -> dict[str, Any]:
    """Get jobstores dict with SQLite persistence."""
    global _jobstores
    if not _jobstores:
        jobs_path = _get_jobs_db_path()
        jobs_path.parent.mkdir(parents=True, exist_ok=True)
        _jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{jobs_path}"),
        }
        _init_results_db()
    return _jobstores


def coordinator_for(user_id: str) -> Any | None:
    """Return the governed coordinator for a user, or None when unavailable.

    Scheduled runs go through ``SubagentCoordinator`` and its frozen launch
    manifest — never the legacy ``SubagentManager``, which has no capability
    preflight, no receipts and no work queue.
    """
    try:
        from src.sdk.coordinator import SubagentCoordinator

        return SubagentCoordinator(user_id)
    except Exception as exc:  # pragma: no cover - construction is cheap but may fail
        logger.error(
            "subagent.scheduler_coordinator_unavailable",
            {"error": str(exc)},
            user_id=user_id,
        )
        return None


def _job_id(schedule_id: str) -> str:
    return f"sched:{schedule_id}"


def _default_store() -> SubagentScheduleStore:
    return SubagentScheduleStore(get_paths().subagent_schedules_db_path())


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the process-local scheduler.

    Must be called from inside a running event loop: ``AsyncIOScheduler``
    schedules onto the loop it is started on, so constructing it without one
    produces a scheduler that silently never fires. Failing loudly here is
    strictly better than that.
    """
    global _scheduler
    if _scheduler is None:
        try:
            asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "get_scheduler() requires a running event loop; start it from "
                "the application lifespan (an async context), not at import time."
            ) from exc
        _scheduler = AsyncIOScheduler(
            jobstores=_get_jobstores(),
            misfire_grace_time=300,
        )

        def job_missed(event: Any) -> None:
            logger.warning(
                "subagent.job_missed",
                {"job_id": event.job_id, "scheduled_run_time": str(event.scheduled_run_time)},
                user_id="system",
            )

        def job_executed(event: Any) -> None:
            if event.exception:
                logger.error(
                    "subagent.job_failed",
                    {"job_id": event.job_id, "exception": str(event.exception)},
                    user_id="system",
                )

        _scheduler.add_listener(job_missed, EVENT_JOB_MISSED)
        _scheduler.add_listener(job_executed, EVENT_JOB_EXECUTED)
        _scheduler.start()
        logger.info("subagent.scheduler.started", {}, user_id="system")
    return _scheduler


async def shutdown_scheduler() -> None:
    """Stop the scheduler and release the process-local singleton.

    The jobstore cache is cleared too: it is keyed on nothing, so leaving it
    behind would make a later get_scheduler() reuse a jobstore built for a
    different data path.
    """
    global _scheduler, _jobstores
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:  # pragma: no cover - shutdown is best effort
            logger.warning(
                "subagent.scheduler_shutdown_failed", {"error": str(exc)}, user_id="system"
            )
        _scheduler = None
    _jobstores = {}


def _build_trigger(row: dict[str, Any]) -> Any | None:
    """Reconstruct a real trigger from persisted metadata.

    The previous implementation discarded the persisted trigger entirely and
    scheduled every row as a one-shot ~30s after startup, which silently turned
    recurring schedules into one-shots and fired future one-shots early.
    """
    kind = row.get("trigger_kind")
    if kind == "once":
        raw = row.get("run_at")
        if not raw:
            return None
        try:
            run_at = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=UTC)
        return DateTrigger(run_date=run_at)
    if kind == "cron":
        cron = row.get("cron")
        if not cron:
            return None
        try:
            return CronTrigger.from_crontab(
                str(cron), timezone=ZoneInfo(str(row.get("timezone") or "UTC"))
            )
        except (ValueError, ZoneInfoNotFoundError, KeyError):
            return None
    return None


async def restore_schedules(
    scheduler: AsyncIOScheduler, *, store: SubagentScheduleStore | None = None
) -> None:
    """Rebuild triggers for every restorable schedule. Idempotent.

    The job function is the module-level ``fire_schedule`` coroutine and the
    user/schedule ids are passed as APScheduler *args*, so a trigger restored
    from the SQLAlchemy jobstore after a restart is a normal call with its
    arguments intact. A closure would not survive that round trip.
    """
    store = store or _default_store()
    for row in await store.all_due_for_restore():
        schedule_id = str(row.get("schedule_id") or "")
        if not schedule_id:
            continue
        user_id = str(row.get("user_id"))
        if str(row.get("trigger_kind")) == "once" and _is_past(row.get("run_at")):
            await store.update_status(
                user_id,
                schedule_id,
                "expired",
                last_error="run_at passed while the process was not running",
            )
            logger.warning(
                "subagent.schedule_expired",
                {"schedule_id": schedule_id, "run_at": row.get("run_at")},
                user_id=user_id,
            )
            continue
        trigger = _build_trigger(row)
        if trigger is None:
            await store.update_status(
                user_id,
                schedule_id,
                "invalid",
                last_error="trigger metadata is missing or unparseable",
            )
            continue
        scheduler.add_job(
            fire_schedule,
            trigger=trigger,
            id=_job_id(schedule_id),
            args=[user_id, schedule_id],
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(
            "subagent.schedule_restored",
            {"schedule_id": schedule_id, "trigger_kind": row.get("trigger_kind")},
            user_id=user_id,
        )


def _is_past(raw: Any) -> bool:
    if not raw:
        return False
    try:
        run_at = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=UTC)
    return run_at < datetime.now(UTC)


async def fire_schedule(
    user_id: str, schedule_id: str, *, store: SubagentScheduleStore | None = None
) -> str | None:
    """Run one schedule through the governed coordinator.

    Never raises: an APScheduler job that raises is lost silently, so every
    failure is recorded on the schedule row with a reason.
    """
    store = store or _default_store()
    try:
        row = await store.get(user_id, schedule_id)
        if row is None:
            logger.warning(
                "subagent.schedule_missing",
                {"schedule_id": schedule_id},
                user_id=user_id,
            )
            return None
        status = str(row.get("status"))
        if status not in {"scheduled", "running", "failed"}:
            logger.info(
                "subagent.schedule_skipped",
                {"schedule_id": schedule_id, "status": status},
                user_id=user_id,
            )
            return None

        manifest = row.get("manifest")
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError("schedule has no frozen manifest to launch from")
        plan = SubagentLaunchPlan.from_persisted(manifest)

        coordinator = coordinator_for(user_id)
        if coordinator is None:
            raise RuntimeError("no coordinator available for user")

        task_id = await coordinator.start_with_plan(
            str(row.get("subagent_name")), str(row.get("task")), plan
        )
        await store.update_status(user_id, schedule_id, "running", last_run_id=task_id)
        logger.info(
            "subagent.schedule_fired",
            {"schedule_id": schedule_id, "task_id": task_id},
            user_id=user_id,
        )
        return str(task_id) if task_id else None
    except Exception as exc:
        logger.error(
            "subagent.schedule_fire_failed",
            {"schedule_id": schedule_id, "error": str(exc)},
            user_id=user_id,
        )
        try:
            await store.update_status(
                user_id,
                schedule_id,
                "failed",
                last_error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # pragma: no cover - the store itself is broken
            pass
        return None


async def cancel_schedule(user_id: str, schedule_id: str) -> bool:
    """Remove the trigger and mark the schedule cancelled. Idempotent."""
    scheduler = _scheduler
    if scheduler is not None:
        try:
            job = scheduler.get_job(_job_id(schedule_id))
            if job is not None:
                job.remove()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "subagent.schedule_cancel_trigger_failed",
                {"schedule_id": schedule_id, "error": str(exc)},
                user_id=user_id,
            )
    store = _default_store()
    return await store.cancel(user_id, schedule_id)
