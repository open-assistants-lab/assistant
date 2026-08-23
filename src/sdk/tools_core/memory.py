"""Memory tools — read from MemoryCore (post-0.10 architecture).

CoreMem >=0.10 replaced the observer/reflector pipeline with compiler +
dreaming + search. `memory_profile` now builds a digest from semantic recall
over the conversation history; `memory_reflection` was removed (insight
generation is owned by CoreMem's dream/compile path).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from src.sdk.tools import ToolAnnotations, tool


def _get_core(user_id: str, workspace_id: str) -> Any:
    from src.storage.messages import get_message_store
    return get_message_store(user_id, workspace_id).core


_PROFILE_QUERY = (
    "user preferences, personal facts, background, habits, work, "
    "recurring topics, relationships"
)
_PROFILE_DAYS = 30


@tool
def memory_profile(
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Return a digest of the user's recent conversation context.

    Uses semantic recall over the conversation history (CoreMem episodic
    strategy with cross-session diversity) to surface recurring topics,
    preferences, and personal facts. May be empty if the user has little
    history.

    Use when the user asks "what do you know about me?" or the agent needs
    to refresh its understanding of the user's context. For specific facts
    from a past conversation, use message_search instead.

    Args:
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Recent context digest, or an empty notice
    """
    core = _get_core(user_id, workspace_id)
    cutoff = (datetime.now(UTC) - timedelta(days=_PROFILE_DAYS)).isoformat()
    results = core.recall(
        query=_PROFILE_QUERY,
        strategy="episodic",
        limit=8,
        session_cap=2,
        ts_after=cutoff,
    )

    if not results:
        return (
            "No recent conversation context available. "
            "Try message_search to find specific facts from conversation history."
        )

    parts = ["## Working Memory (Recent Context)\n"]
    for i, result in enumerate(results, 1):
        memory = getattr(result, "memory", None)
        content = str(getattr(memory, "content", "") or "")[:300]
        ts = str(getattr(memory, "ts", "") or "")[:10]
        score = float(getattr(result, "score", 1.0) or 1.0)
        parts.append(f"{i}. [{ts}] (score: {score:.2f}) {content}")

    return "\n".join(parts)


memory_profile.annotations = ToolAnnotations(
    title="Get User Profile", read_only=True, idempotent=True
)
