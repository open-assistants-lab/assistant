"""Session listing endpoint."""

from typing import Any

from fastapi import APIRouter

from src.storage.messages import get_message_store

router = APIRouter(tags=["sessions"])


@router.get("/sessions")
async def list_sessions(user_id: str = "default_user", limit: int = 50) -> dict[str, Any]:
    """List all chat sessions for a user."""
    store = get_message_store(user_id)
    sessions = store.list_sessions(limit)
    return {"sessions": sessions}
