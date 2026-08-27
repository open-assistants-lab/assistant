"""UI state tool — SDK-native implementation."""

from src.sdk.tools import ToolAnnotations, tool
from src.sdk.ui_state import get_state
from src.storage.paths import DEFAULT_USER_ID


@tool
def ui_state_get(user_id: str =  DEFAULT_USER_ID) -> str:
    """Get the current UI state and recent user interactions.

    Returns what tab the user is on, what they've selected,
    recent clicks/scrolling, and the current canvas content.
    Use this when you need to understand what the user is
    looking at or what steps they took before asking a question.

    Args:
        user_id: The user ID (default: "default_user")

    Returns:
        Formatted markdown of current UI state and recent events
    """
    return get_state(user_id).to_markdown()


ui_state_get.annotations = ToolAnnotations(
    title="Get UI State",
    read_only=True,
    idempotent=True,
)
