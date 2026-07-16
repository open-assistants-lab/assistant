"""UI interaction tracking router — track and query user UI state."""

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.sdk.ui_state import get_state, track_event

router = APIRouter(prefix="/ui", tags=["ui"])


class TrackEventBody(BaseModel):
    tab: str = "canvas"
    event: dict[str, Any]
    timestamp: str | None = None


@router.post("/track")
async def track_ui_event(
    body: TrackEventBody,
    user_id: str = Query("default_user"),
) -> dict[str, str]:
    event_dict = dict(body.event)
    if body.timestamp:
        event_dict["timestamp"] = body.timestamp
    event_dict["tab"] = body.tab
    track_event(user_id, event_dict)
    return {"status": "ok"}


@router.get("/state")
async def get_ui_state(
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    return get_state(user_id).to_dict()
