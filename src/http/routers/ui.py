# mypy: disable-error-code="assignment"
"""UI interaction tracking router — track and query user UI state."""

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from src.http.auth import resolve_user_id
from src.sdk.ui_state import get_state, track_event
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/ui", tags=["ui"])


class TrackEventBody(BaseModel):
    tab: str = "canvas"
    event: dict[str, Any]
    timestamp: str | None = None


@router.post("/track")
async def track_ui_event(
    body: TrackEventBody,
    user_id: str = Query(DEFAULT_USER_ID),
    request: Request = None,
) -> dict[str, str]:
    user_id = resolve_user_id(request, user_id)
    event_dict = dict(body.event)
    if body.timestamp:
        event_dict["timestamp"] = body.timestamp
    event_dict["tab"] = body.tab
    track_event(user_id, event_dict)
    return {"status": "ok"}


@router.get("/state")
async def get_ui_state(
    user_id: str = Query(DEFAULT_USER_ID),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    return get_state(user_id).to_dict()
