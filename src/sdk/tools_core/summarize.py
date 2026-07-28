"""Summarization tool — manual /summarize command for the agent loop."""

from src.sdk.middleware_summarization import SummarizationMiddleware
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

    summary_mw = loop.find_middleware(SummarizationMiddleware)
    if summary_mw is None:
        return "Error: No summarization middleware configured."

    before_tokens = summary_mw.count_tokens(loop.state.messages)
    success = await summary_mw.force_summarize(loop.state, instructions=instructions)
    if not success:
        return "Conversation too short to summarize meaningfully."
    after_tokens = summary_mw.count_tokens(loop.state.messages)
    saved = before_tokens - after_tokens
    return f"Summarized. Saved ~{saved} tokens."


summarize_session.annotations = ToolAnnotations(
    title="Summarize / Compact Conversation",
    read_only=True,
    idempotent=True,
)
