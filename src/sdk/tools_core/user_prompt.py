"""User prompt tools — per-user custom instructions across all workspaces."""

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.sdk.user_prompt import load_user_prompt, save_user_prompt
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()


@tool
def user_prompt_get(user_id: str =  DEFAULT_USER_ID) -> str:
    """Get the current user's custom prompt.

    Returns the prompt if set, or a message saying none is configured.

    Args:
        user_id: User identifier (injected automatically)

    Returns:
        User prompt text or empty notice
    """
    prompt = load_user_prompt(user_id)
    if not prompt:
        return "No custom prompt configured for this user."
    return prompt


user_prompt_get.annotations = ToolAnnotations(
    title="Get User Prompt", read_only=True, idempotent=True
)


@tool
def user_prompt_set(prompt: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Set the user's custom prompt (persistent instructions for all workspaces).

    This prompt is injected into the system prompt before workspace-specific
    instructions. Use it for instructions that should apply everywhere, e.g.
    preferred communication style, timezone, naming conventions.

    Pass an empty string to clear the prompt.

    Args:
        prompt: The custom prompt text. Empty string to clear.
        user_id: User identifier (injected automatically)

    Returns:
        Confirmation message
    """
    save_user_prompt(user_id, prompt)
    logger.info(
        "user_prompt.set",
        {"length": len(prompt), "set": bool(prompt)},
        user_id=user_id,
    )
    if not prompt:
        return "User prompt cleared."
    return f"User prompt saved ({len(prompt)} chars)."


user_prompt_set.annotations = ToolAnnotations(
    title="Set User Prompt", destructive=True
)
