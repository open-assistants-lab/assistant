"""Summarization tool — manual /summarize command for the agent loop."""

from src.sdk.compression import CompressionReason, CompressionStatus
from src.sdk.tools import ToolAnnotations, tool


@tool
async def summarize_session(
    user_id: str,
    workspace_id: str = "personal",
    instructions: str | None = None,
) -> str:
    """Manually compact the conversation by summarizing old messages.

    Use this when the conversation is getting long and you want to
    free up context space. Old tool outputs are pruned and the
    conversation history is summarized.

    Args:
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        instructions: Optional focus instructions for the summary
            (e.g. "preserve all file paths and error messages")

    Returns:
        Confirmation message with token savings
    """
    from src.sdk.loop import get_current_agent_loop

    loop = get_current_agent_loop()
    if loop is None:
        return "Error: No active agent session. Summarization is only available during a conversation."

    result = await loop.compress_context(CompressionReason.MANUAL, instructions)
    telemetry = result.telemetry
    if telemetry.status is CompressionStatus.SUCCEEDED:
        saved = max(0, telemetry.before_token_count - telemetry.after_token_count)
        return (
            f"Summarized conversation history from ~{telemetry.before_token_count} to "
            f"~{telemetry.after_token_count} tokens (saved ~{saved})."
        )
    reason = telemetry.error_code or "no eligible history"
    if telemetry.status is CompressionStatus.SKIPPED:
        return f"Conversation was not summarized: {reason}."
    return f"Conversation summarization failed safely: {reason}."


summarize_session.annotations = ToolAnnotations(
    title="Summarize / Compact Conversation",
    read_only=False,
    idempotent=False,
    destructive=False,
)
