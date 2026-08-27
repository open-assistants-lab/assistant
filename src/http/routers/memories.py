"""Memory API — profile digest + clear (post-CoreMem-0.10).

CoreMem >=0.10 replaced observations/reflections with compiler + dreaming +
search, so the old observations/reflections endpoints were removed. The
profile endpoint mirrors the memory_profile tool (semantic recall digest);
clear wipes the conversation store.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter

from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/memories", tags=["memories"])


def _get_core(user_id: str, workspace_id: str) -> Any:
    from src.storage.messages import get_message_store
    return get_message_store(user_id, workspace_id).core


@router.get("/profile")
async def get_profile(
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    days: int = 30,
    limit: int = 8,
) -> dict[str, Any]:
    """Return a digest of the user's recent conversation context."""
    core = _get_core(user_id, workspace_id)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    results = core.recall(
        query=(
            "user preferences, personal facts, background, habits, work, "
            "recurring topics, relationships"
        ),
        strategy="episodic",
        limit=limit,
        session_cap=2,
        ts_after=cutoff,
    )
    profile = []
    for r in results:
        memory = getattr(r, "memory", None)
        profile.append(
            {
                "content": str(getattr(memory, "content", "") or "")[:300],
                "ts": str(getattr(memory, "ts", "") or "")[:10],
                "score": float(getattr(r, "score", 1.0) or 1.0),
            }
        )
    return {"profile": profile}


@router.delete("/clear")
async def clear_memories(
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> dict[str, Any]:
    """Delete all messages for the user."""
    core = _get_core(user_id, workspace_id)
    core.clear()
    return {"status": "cleared", "user_id": user_id, "workspace_id": workspace_id}
