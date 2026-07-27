"""Scheduler HTTP endpoints for managing the agent scheduler and notifications."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.app_logging import get_logger
from src.sdk.agent_scheduler import get_agent_scheduler
from src.sdk.tools_core.agent_scheduler_db import SchedulerMemoryDB, SchedulerNotificationDB

router = APIRouter(prefix="/scheduler", tags=["scheduler"])
logger = get_logger()


@router.get("/notifications")
async def list_notifications(
    user_id: str = Query("default_user"),
    limit: int = Query(50, ge=1, le=200),
    include_dismissed: bool = Query(False),
) -> dict[str, Any]:
    db = SchedulerNotificationDB(user_id)
    try:
        notifs = await db.list(limit=limit, include_dismissed=include_dismissed)
        return {"notifications": notifs}
    finally:
        await db.close()


@router.post("/notifications/{notif_id}/dismiss")
async def dismiss_notification(
    notif_id: str,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    db = SchedulerNotificationDB(user_id)
    try:
        ok = await db.dismiss(notif_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"status": "dismissed"}
    finally:
        await db.close()


@router.post("/pause")
async def pause_scheduler(user_id: str = Query("default_user")) -> dict[str, Any]:
    scheduler = get_agent_scheduler(user_id)
    await scheduler.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_scheduler(user_id: str = Query("default_user")) -> dict[str, Any]:
    scheduler = get_agent_scheduler(user_id)
    await scheduler.resume()
    return {"status": "resumed"}


@router.get("/status")
async def scheduler_status(user_id: str = Query("default_user")) -> dict[str, Any]:
    scheduler = get_agent_scheduler(user_id)
    return {
        "running": scheduler.is_running,
        "paused": scheduler.is_paused,
        "last_check": scheduler.last_check,
    }


@router.get("/memory")
async def list_scheduler_memory(user_id: str = Query("default_user")) -> dict[str, Any]:
    db = SchedulerMemoryDB(user_id)
    try:
        facts = await db.list_all()
        return {"facts": facts}
    finally:
        await db.close()


@router.delete("/memory/{mem_id}")
async def delete_scheduler_memory(
    mem_id: int,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    db = SchedulerMemoryDB(user_id)
    try:
        ok = await db.delete(mem_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Memory fact not found")
        return {"status": "deleted"}
    finally:
        await db.close()
