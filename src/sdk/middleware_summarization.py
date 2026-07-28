"""Summarization middleware for reducing token count in conversation history.

SDK-native implementation: replaces src/middleware/summarization.py.
Reimplements LangChain's SummarizationMiddleware from scratch:
- Token counting via tiktoken (cl100k_base encoding)
- Message truncation when token count exceeds threshold
- Summary generation via AgentLoop.run_single()
- Callback on successful summary
- Duplicate prevention guard
- Tool output pruning before summarization
- Force summarization for overflow recovery and manual /summarize
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import tiktoken

from src.app_logging import get_logger
from src.sdk.messages import Message
from src.sdk.middleware import Middleware
from src.sdk.state import AgentState

logger = get_logger()

SummaryCallback = Callable[[str], Awaitable[None]] | Callable[[str], Any]

SUMMARY_SYSTEM_PROMPT = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.

This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like file names, full code snippets, function signatures, file edits, etc
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. ## Accomplished
   What was completed since the last summary. List specific files modified, functions changed, tests added.

2. ## Current State
   What is in progress right now. What the user was last working on. What the last message was about.

3. ## Files & Architecture
   All files that have been touched. Their purposes. Key architectural decisions made.

4. ## Next Steps
   What the user was about to do next. Any explicit TODO items. Unresolved issues or bugs.

5. ## Constraints & Preferences
   User preferences, coding style constraints, performance requirements, or any other context that would be harmful to forget.
"""

SUMMARY_USER_TEMPLATE = (
    "Summarize the following conversation segment in 200-500 words:\n\n{conversation}"
)

_APPROX_TOKENS_PER_CHAR = 0.25
_FALLBACK_TOKEN_RATIO = 4


class SummarizationMiddleware(Middleware):
    """Middleware that summarizes conversation history when token count exceeds threshold.

    Token counting uses tiktoken (cl100k_base) with a str-based fallback.
    When the message history exceeds trigger_tokens, the oldest messages are
    summarized and replaced with a single system message containing the summary.
    The most recent keep_tokens worth of messages are always preserved.

    Args:
        trigger_tokens: Start summarization when total tokens exceed this.
        keep_tokens: Always keep at least this many tokens of recent messages.
        model: LLM model identifier for summary generation.
        on_summarize: Optional callback invoked with summary content on success.
    """

    def __init__(
        self,
        trigger_tokens: int = 50000,
        keep_tokens: int = 1000,
        model: str = "ollama:minimax-m2.5",
        on_summarize: SummaryCallback | None = None,
    ):
        self.trigger_tokens = trigger_tokens
        self.keep_tokens = keep_tokens
        self.model = model
        self._on_summarize = on_summarize
        self._last_summary_msg_count: int = 0
        self._encoding: Any = None

    def _get_encoding(self) -> Any:
        if self._encoding is None:
            try:
                self._encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._encoding = None
        return self._encoding

    def count_tokens(self, text: str) -> int:
        enc = self._get_encoding()
        if enc is not None:
            try:
                return len(enc.encode(text))
            except Exception:
                pass
        return max(1, int(len(text) * _APPROX_TOKENS_PER_CHAR))

    def _count_message_tokens(self, msg: Message) -> int:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        total = self.count_tokens(content)
        total += 4
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                total += self.count_tokens(tc.name)
                total += self.count_tokens(json.dumps(tc.arguments))
        if msg.role == "tool" and msg.name:
            total += self.count_tokens(msg.name)
        if msg.reasoning:
            total += self.count_tokens(msg.reasoning)
        return total

    def _total_tokens(self, messages: list[Message]) -> int:
        return sum(self._count_message_tokens(m) for m in messages)

    def _messages_to_conversation_text(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    def _prune_tool_outputs(self, messages: list[Message], keep_tokens: int) -> list[Message]:
        """Replace old tool outputs with token-count placeholders.

        Messages within the keep_tokens window are left intact.
        Older messages with role=='tool' get their content replaced with
        a short placeholder to save tokens during summarization.
        """
        recent_tokens = 0
        boundary = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            t = self._count_message_tokens(messages[i])
            if recent_tokens + t > keep_tokens and i < len(messages) - 1:
                boundary = i + 1
                break
            recent_tokens += t

        pruned = list(messages)
        for i in range(boundary):
            if pruned[i].role == "tool":
                original_tokens = self._count_message_tokens(pruned[i])
                pruned[i] = Message(
                    role="tool",
                    content=f"[pruned: {original_tokens} tokens of tool output]",
                    name=pruned[i].name,
                    tool_call_id=pruned[i].tool_call_id,
                )
            elif pruned[i].role == "assistant" and pruned[i].tool_calls:
                pruned[i] = Message(
                    role="assistant",
                    content=pruned[i].content,
                    tool_calls=pruned[i].tool_calls,
                )
        return pruned

    def _split_messages(
        self, messages: list[Message], keep_tokens: int | None = None
    ) -> tuple[list[Message], list[Message]]:
        """Split messages into 'old' (to summarize) and 'recent' (keep as-is).

        The split boundary is computed from keep_tokens.
        System messages are excluded from the 'old' list.
        """
        tokens_to_keep = keep_tokens if keep_tokens is not None else self.keep_tokens
        recent_tokens = 0
        split_idx = len(messages)

        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = self._count_message_tokens(messages[i])
            if recent_tokens + msg_tokens > tokens_to_keep and i < len(messages) - 1:
                split_idx = i + 1
                break
            recent_tokens += msg_tokens
        else:
            split_idx = 1

        old_messages = messages[:split_idx]
        system_messages = [m for m in old_messages if m.role == "system"]
        non_system_old = [m for m in old_messages if m.role != "system"]
        return non_system_old, list(system_messages) + list(messages[split_idx:])

    async def _generate_summary(self, conversation_text: str) -> str | None:
        try:
            from src.sdk.loop import AgentLoop
            from src.sdk.providers.factory import create_model_from_config

            provider = create_model_from_config(self.model)
            loop = AgentLoop(provider=provider)

            summary_messages = [
                Message.system(SUMMARY_SYSTEM_PROMPT),
                Message.user(SUMMARY_USER_TEMPLATE.format(conversation=conversation_text)),
            ]

            result = await loop.run_single(summary_messages)
            if result:
                content = result.content if isinstance(result.content, str) else str(result.content)
                return content.strip() or None
            return None
        except Exception as e:
            logger.warning(
                "summarization.generation_failed",
                {"error": str(e)},
                user_id="system",
            )
            return None

    async def force_summarize(self, state: AgentState, instructions: str | None = None) -> bool:
        """Force summarization even if token count is below threshold.

        Called by overflow recovery or /summarize command.
        Returns True if summarization was performed.

        If instructions are provided, they are prepended to the summary
        prompt to focus the summary on specific areas.
        """
        messages = state.messages
        pruned = self._prune_tool_outputs(messages, self.keep_tokens)
        total_tokens = self._total_tokens(pruned)

        if total_tokens < 1000:
            return False

        old_messages, recent_messages = self._split_messages(pruned)
        conversation_text = self._messages_to_conversation_text(old_messages)

        if instructions:
            conversation_text = f"[Focus: {instructions}]\n\n{conversation_text}"

        summary = await self._generate_summary(conversation_text)
        if summary is None:
            return False

        new_messages = [
            Message.system(f"## Summary of previous conversation\n\n{summary}"),
            *recent_messages,
        ]
        state.messages = new_messages
        self._last_summary_msg_count = len(new_messages)

        if self._on_summarize is not None:
            try:
                result = self._on_summarize(summary)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass

        return True

    async def abefore_model(self, state: AgentState) -> dict[str, Any] | None:
        messages = state.messages
        current_msg_count = len(messages)

        if current_msg_count <= self._last_summary_msg_count and self._last_summary_msg_count > 0:
            logger.debug(
                "summarization.skipped",
                {"reason": "already_summarized_in_cycle", "msg_count": current_msg_count},
                user_id="system",
            )
            return None

        # Prune old tool outputs before counting tokens
        pruned = self._prune_tool_outputs(messages, self.keep_tokens)
        total_tokens = self._total_tokens(pruned)

        if total_tokens <= self.trigger_tokens:
            return None

        logger.info(
            "summarization.triggered",
            {
                "total_tokens": total_tokens,
                "trigger_tokens": self.trigger_tokens,
                "msg_count": current_msg_count,
            },
            user_id="system",
        )

        tokens_to_keep = self.keep_tokens
        recent_tokens = 0
        split_idx = len(messages)

        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = self._count_message_tokens(messages[i])
            if recent_tokens + msg_tokens > tokens_to_keep and i < len(messages) - 1:
                split_idx = i + 1
                break
            recent_tokens += msg_tokens
        else:
            split_idx = 1

        if split_idx <= 1:
            logger.warning(
                "summarization.cannot_split",
                {"msg_count": current_msg_count},
                user_id="system",
            )
            return None

        old_messages, recent_messages = self._split_messages(pruned)

        if not old_messages:
            return None

        conversation_text = self._messages_to_conversation_text(old_messages)
        summary_content = await self._generate_summary(conversation_text)

        if summary_content is None:
            logger.warning(
                "summarization.failed_no_summary",
                {"old_msg_count": len(old_messages)},
                user_id="system",
            )
            return None

        content_lower = summary_content.lower()
        failure_reasons = []
        if "too long to summarize" in content_lower:
            failure_reasons.append("content_too_long_for_summary")
        if "failed to summarize" in content_lower:
            failure_reasons.append("summary_generation_failed")
        if "cannot summarize" in content_lower:
            failure_reasons.append("summary_not_possible")
        if len(summary_content) < 200:
            failure_reasons.append(f"summary_too_short ({len(summary_content)} chars)")

        if failure_reasons:
            logger.warning(
                "summarization.failed",
                {
                    "failure_reasons": failure_reasons,
                    "summary_preview": summary_content[:100],
                },
                user_id="system",
            )
            return None

        summary_msg = Message.system(f"## Conversation Summary\n\n{summary_content}")

        new_messages = [summary_msg] + list(recent_messages)

        self._last_summary_msg_count = len(new_messages)

        logger.info(
            "summarization.completed",
            {
                "old_msg_count": current_msg_count,
                "new_msg_count": len(new_messages),
                "summary_length": len(summary_content),
                "tokens_before": total_tokens,
            },
            user_id="system",
        )

        if self._on_summarize is not None:
            try:
                result = self._on_summarize(summary_content)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.warning(
                    "summarization.callback_failed",
                    {"error": str(e)},
                    user_id="system",
                )

        return {"messages": new_messages}

    def before_model(self, state: AgentState) -> dict[str, Any] | None:
        total_tokens = self._total_tokens(state.messages)
        if total_tokens <= self.trigger_tokens:
            return None

        logger.info(
            "summarization.sync_trigger_needed",
            {"total_tokens": total_tokens, "trigger_tokens": self.trigger_tokens},
            user_id="system",
        )
        return None


__all__ = ["SummarizationMiddleware"]
