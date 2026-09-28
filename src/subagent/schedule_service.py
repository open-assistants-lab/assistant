"""One creation path for a subagent schedule, shared by the API and the tool.

Both surfaces must agree exactly: a schedule is only creatable from a *ready*
frozen launch plan, so an unattended job can never be registered that nobody can
answer an approval for. Duplicating that gate between an HTTP route and a tool
is how the two drift apart, so both call here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app_logging import get_logger

logger = get_logger()


class ScheduleRejected(ValueError):  # noqa: N818 - mirrors SubagentLaunchRejected
    """A schedule was refused before anything was persisted.

    ``code`` distinguishes the failure so callers can map it to a status code
    without string matching.
    """

    def __init__(self, reason: str, *, code: str = "rejected", blocked: list[str] | None = None):
        self.code = code
        self.blocked = blocked or []
        super().__init__(reason)


def _validate_trigger(run_at: Any, cron: Any) -> None:
    if bool(run_at) == bool(cron):
        raise ScheduleRejected(
            "Exactly one of run_at or cron is required — not both, not neither.",
            code="invalid_trigger",
        )


def _get_coordinator(user_id: str, workspace_id: str) -> Any:
    from src.sdk.coordinator import SubagentCoordinator

    return SubagentCoordinator(user_id, workspace_id=workspace_id)


async def create_schedule(
    *,
    user_id: str,
    subagent_name: str,
    task: str,
    workspace_id: str = "personal",
    run_at: Any = None,
    cron: Any = None,
    timezone: str = "UTC",
) -> dict[str, Any]:
    """Create a schedule from a verified frozen plan, or refuse with a reason.

    Order matters: trigger shape first (cheap, no I/O), then the launch
    preflight, then the write. Every rejection happens before persistence.
    """
    _validate_trigger(run_at, cron)

    coordinator = _get_coordinator(user_id, workspace_id)
    try:
        plan = coordinator.preflight(subagent_name)
    except ValueError as exc:
        raise ScheduleRejected(str(exc), code="unknown_subagent") from exc
    except Exception as exc:  # pragma: no cover - defensive
        from src.app_logging import get_logger

        get_logger().error(
            "schedules.preflight_failed", {"error": str(exc)}, user_id=user_id
        )
        raise ScheduleRejected(f"preflight failed: {exc}", code="preflight_error") from exc

    if not plan.ready:
        blocked = [f"{d.kind}:{d.name} ({d.status})" for d in plan.rejected_decisions]
        raise ScheduleRejected(
            "Subagent launch would be rejected before execution, so the schedule was "
            "not created. An unattended schedule must resolve to 'allow' for every "
            "declared capability. Blocked: " + ", ".join(blocked),
            code="plan_not_ready",
            blocked=blocked,
        )

    from src.subagent.schedules_store import SubagentScheduleStore

    # No explicit path: the store resolves from get_paths(), which is the seam
    # deployments and tests patch. Passing a path here would hide that.
    store = SubagentScheduleStore()
    try:
        created = await store.create(
            user_id=user_id,
            workspace_id=workspace_id,
            subagent_name=subagent_name,
            task=task,
            trigger_kind="cron" if cron else "once",
            run_at=run_at,
            cron=cron,
            timezone=timezone,
            manifest=plan.to_persisted_dict(),
            manifest_hash=plan.plan_id,
        )
    except ValueError as exc:
        # Invalid cron/timezone/instant — still before any write.
        raise ScheduleRejected(str(exc), code="invalid_trigger") from exc

    return {
        "schedule_id": str(created["schedule_id"]),
        "status": str(created["status"]),
        "manifest_hash": str(created["manifest_hash"]),
        "trigger_kind": str(created["trigger_kind"]),
    }


__all__ = [
    "ScheduleRejected",
    "create_schedule",
    "migrate_legacy_schedules",
    "LEGACY_MIGRATED_STATUS",
]


# --------------------------------------------------------------------------
# Legacy migration
# --------------------------------------------------------------------------

#: Status written to a legacy `job_results` row once it has been consumed, so
#: the migration is re-entrant and the legacy table keeps an audit trail rather
#: than being silently emptied.
LEGACY_MIGRATED_STATUS = "migrated"

_LEGACY_REASON = (
    "migrated from a legacy job_results row that stored no trigger metadata "
    "(run_at/cron/timezone), so the schedule is parked for review rather than "
    "guessed — see #46"
)


def _legacy_results_path() -> Any:
    """Reuse the scheduler's path helper so there is one seam, not two."""
    from src.subagent import scheduler as scheduler_module

    return scheduler_module._get_results_db_path()  # noqa: SLF001 - same subsystem


async def migrate_legacy_schedules() -> dict[str, int]:
    """Surface legacy `scheduled` rows as `needs_review` schedules.

    The legacy scheduler persisted only job_id/user_id/subagent/task/status — no
    trigger. A one-shot that ran at 03:00 and a daily cron look identical, so
    recovering a *plausible* time would be a guess that can fire unattended work
    at the wrong moment. These rows are therefore parked for an operator, which
    is the only honest option until someone restates the intent.

    Re-entrant: a consumed legacy row is marked ``migrated`` and never revisited.
    """
    import sqlite3

    from src.subagent.schedules_store import SubagentScheduleStore

    stats = {"migrated": 0, "needs_review": 0, "already_migrated": 0, "skipped": 0}
    try:
        path = _legacy_results_path()
        if not Path(str(path)).exists():
            return stats
    except Exception:  # pragma: no cover - paths unavailable
        return stats

    conn = sqlite3.connect(str(path))
    try:
        try:
            rows = conn.execute(
                "SELECT job_id, user_id, subagent_name, task, created_at "
                "FROM job_results WHERE status = 'scheduled' AND job_id IS NOT NULL"
            ).fetchall()
            already = conn.execute(
                "SELECT COUNT(*) FROM job_results WHERE status = ?", (LEGACY_MIGRATED_STATUS,)
            ).fetchone()[0]
        except sqlite3.Error:
            # Table absent (fresh install) — nothing to migrate.
            return stats
        stats["already_migrated"] = int(already or 0)
        if not rows:
            return stats

        store = SubagentScheduleStore()
        for job_id, user_id, subagent_name, task, created_at in rows:
            if not (user_id and subagent_name and task):
                stats["skipped"] += 1
                continue
            try:
                # trigger_kind/run_at/cron are all None: the store requires
                # exactly one, so the row is inserted with a sentinel and
                # corrected to needs_review immediately after.
                created = await store.create(
                    user_id=str(user_id),
                    workspace_id="personal",
                    subagent_name=str(subagent_name),
                    task=str(task),
                    trigger_kind="once",
                    run_at=datetime.now(UTC).isoformat(),
                    cron=None,
                    timezone="UTC",
                    manifest={"legacy": True, "job_id": str(job_id)},
                    manifest_hash=f"legacy:{job_id}",
                )
                # Park it: the trigger is unknown, so the row must not look
                # runnable. The sentinel run_at above is corrected away here.
                await store.update_status(
                    str(user_id),
                    str(created["schedule_id"]),
                    "needs_review",
                    last_error=_LEGACY_REASON,
                )
                stats["needs_review"] += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "schedules.legacy_migration_failed",
                    {"job_id": str(job_id), "error": str(exc)},
                    user_id=str(user_id),
                )
                stats["skipped"] += 1
                continue
            stats["migrated"] += 1
            conn.execute(
                "UPDATE job_results SET status = ? WHERE job_id = ?",
                (LEGACY_MIGRATED_STATUS, str(job_id)),
            )
        conn.commit()
    finally:
        conn.close()
    return stats
