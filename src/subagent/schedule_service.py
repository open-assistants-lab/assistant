"""One creation path for a subagent schedule, shared by the API and the tool.

Both surfaces must agree exactly: a schedule is only creatable from a *ready*
frozen launch plan, so an unattended job can never be registered that nobody can
answer an approval for. Duplicating that gate between an HTTP route and a tool
is how the two drift apart, so both call here.
"""

from __future__ import annotations

from typing import Any


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


__all__ = ["ScheduleRejected", "create_schedule"]
