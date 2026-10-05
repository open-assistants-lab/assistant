"""Governed HTTP surface for subagent schedules (#46, Task 4).

A schedule is only creatable from a *ready* frozen launch plan. `build_launch_plan`
already resolves `ask`/`deny` for every declared capability and marks the plan
not-ready when either appears, so requiring a ready plan is what guarantees an
unattended schedule can never be registered that nobody can answer an approval
for. No separate policy mode is needed, and none is added.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.app_logging import get_logger
from src.http.auth import resolve_user_id
from src.storage.paths import get_paths
from src.subagent.schedules_store import SubagentScheduleStore

logger = get_logger()

router = APIRouter(prefix="/subagents", tags=["subagent-schedules"])


def get_coordinator(user_id: str, workspace_id: str = "personal") -> Any:
    from src.sdk.coordinator import SubagentCoordinator

    return SubagentCoordinator(user_id, workspace_id=workspace_id)


def _store() -> SubagentScheduleStore:
    return SubagentScheduleStore(get_paths().subagent_schedules_db_path())


def _require_enabled() -> None:
    from src.config import get_settings

    scheduling = getattr(get_settings(), "scheduling", None)
    if not getattr(scheduling, "subagent_enabled", False):
        raise HTTPException(
            status_code=403,
            detail=(
                "Subagent scheduling is not enabled for this deployment. "
                "Set scheduling.subagent_enabled (SCHEDULING_SUBAGENT_ENABLED) to enable it."
            ),
        )


class ScheduleCreateRequest(BaseModel):
    """One of `run_at` or `cron` is required; both is an error."""

    subagent_name: str = Field(..., min_length=1, max_length=64)
    task: str = Field(..., min_length=1)
    workspace_id: str = "personal"
    run_at: str | None = None
    cron: str | None = None
    timezone: str = "UTC"


class ScheduleCreateResponse(BaseModel):
    schedule_id: str
    status: str
    manifest_hash: str


class ScheduleSummary(BaseModel):
    schedule_id: str
    subagent_name: str
    task: str
    trigger_kind: str
    run_at: str | None = None
    cron: str | None = None
    timezone: str
    status: str
    manifest_hash: str
    last_run_id: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


def _summary(row: dict[str, Any]) -> ScheduleSummary:
    return ScheduleSummary(
        schedule_id=str(row["schedule_id"]),
        subagent_name=str(row["subagent_name"]),
        task=str(row["task"]),
        trigger_kind=str(row["trigger_kind"]),
        run_at=row.get("run_at"),
        cron=row.get("cron"),
        timezone=str(row.get("timezone") or "UTC"),
        status=str(row["status"]),
        manifest_hash=str(row.get("manifest_hash") or ""),
        last_run_id=row.get("last_run_id"),
        last_error=row.get("last_error"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _rejection_detail(rejected: Any) -> str:
    parts = [
        f"{item.kind}:{item.name} ({item.status})" for item in rejected
    ]
    return (
        "Subagent launch would be rejected before execution, so the schedule was "
        "not created. An unattended schedule must resolve to 'allow' for every "
        "declared capability. Blocked: " + ", ".join(parts)
    )


@router.post("/schedules", response_model=ScheduleCreateResponse)
async def create_schedule(
    request: ScheduleCreateRequest,
    http_request: Request,
    user_id: str = Query(default="default_user"),
) -> ScheduleCreateResponse:
    _require_enabled()
    user_id = resolve_user_id(http_request, user_id)

    # One creation path shared with the subagent_schedule tool, so the two
    # surfaces cannot disagree about the frozen-manifest gate.
    from src.subagent.schedule_service import ScheduleRejected, create_schedule

    try:
        created = await create_schedule(
            user_id=user_id,
            subagent_name=request.subagent_name,
            task=request.task,
            workspace_id=request.workspace_id,
            run_at=request.run_at,
            cron=request.cron,
            timezone=request.timezone,
        )
    except ScheduleRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ScheduleCreateResponse(
        schedule_id=created["schedule_id"],
        status=created["status"],
        manifest_hash=created["manifest_hash"],
    )


@router.get("/schedules")
async def list_schedules(
    request: Request,
    user_id: str = Query(default="default_user"),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _require_enabled()
    user_id = resolve_user_id(request, user_id)
    store = _store()
    try:
        rows = await store.list_for_user(user_id, status=status, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"schedules": [_summary(row).model_dump() for row in rows]}


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    request: Request,
    user_id: str = Query(default="default_user"),
) -> dict[str, Any]:
    _require_enabled()
    user_id = resolve_user_id(request, user_id)
    store = _store()
    row = await store.get(user_id, schedule_id)
    if row is None:
        # 404 rather than 403: a schedule ID alone must not disclose that it exists.
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return _summary(row).model_dump()


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: str,
    request: Request,
    user_id: str = Query(default="default_user"),
) -> dict[str, Any]:
    """Schedule metadata joined with the work-queue task it last launched."""
    _require_enabled()
    user_id = resolve_user_id(request, user_id)
    store = _store()
    row = await store.get(user_id, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    run: dict[str, Any] | None = None
    last_run_id = row.get("last_run_id")
    if last_run_id:
        try:
            from src.sdk.coordinator import SubagentCoordinator

            coordinator = SubagentCoordinator(user_id, workspace_id=str(row.get("workspace_id") or "personal"))
            db = await coordinator._get_db()  # noqa: SLF001 - same subsystem
            task = await db.get_task(str(last_run_id))
            if task is not None:
                run = {
                    "task_id": str(last_run_id),
                    "status": str(task.get("status")),
                    "error": task.get("error"),
                    "created_at": task.get("created_at"),
                    "completed_at": task.get("completed_at"),
                }
        except Exception as exc:  # pragma: no cover - the join is best effort
            logger.warning("schedules.run_join_failed", {"error": str(exc)}, user_id=user_id)

    return {
        "schedule_id": schedule_id,
        "status": row.get("status"),
        "last_error": row.get("last_error"),
        "runs": [run] if run else [],
    }


@router.delete("/schedules/{schedule_id}")
async def cancel_schedule(
    schedule_id: str,
    request: Request,
    user_id: str = Query(default="default_user"),
) -> dict[str, Any]:
    _require_enabled()
    user_id = resolve_user_id(request, user_id)
    try:
        from src.subagent.scheduler import cancel_schedule as cancel

        if _scheduler_running():
            ok = await cancel(user_id, schedule_id)
        else:
            ok = await _store().cancel(user_id, schedule_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("schedules.cancel_failed", {"error": str(exc)}, user_id=user_id)
        raise HTTPException(status_code=500, detail="cancel failed") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"schedule_id": schedule_id, "status": "cancelled"}


def _scheduler_running() -> bool:
    from src.subagent import scheduler as scheduler_module

    return getattr(scheduler_module, "_scheduler", None) is not None
