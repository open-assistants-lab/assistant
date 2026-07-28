"""Summarization middleware — aligned with LangChain's SummarizationMiddleware.

Monitors message token counts and automatically summarizes older messages when
a threshold is reached, preserving recent messages and maintaining context
continuity by ensuring AI/Tool message pairs remain together.

Extensions over LangChain:
- on_summarize callback (persist summary to conversation store)
- force_summarize() (overflow recovery + manual /summarize)
- Settings-based config (config.yaml + env vars)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypedDict

from src.app_logging import get_logger
from src.sdk.messages import Message
from src.sdk.middleware import Middleware
from src.sdk.state import AgentState

logger = get_logger()

SummaryCallback = Callable[[str], Awaitable[None]] | Callable[[str], Any]
TokenCounter = Callable[[Iterable[Message]], int]

DEFAULT_SUMMARY_PROMPT = """<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective in this task is to extract the highest quality/most relevant context from the conversation history below.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract the highest quality/most relevant pieces of information from your conversation history.
This context will then overwrite the conversation history presented below. Because of this, ensure the context you extract is only the most important information to continue working toward your overall goal.
</objective_information>

<instructions>
The conversation history below will be replaced with the context you extract in this step.
You want to ensure that you don't repeat any actions you've already completed, so the context you extract from the conversation history should be focused on the most important information to your overall goal.

You should structure your summary using the following sections. Each section acts as a checklist - you must populate it with relevant information or explicitly state "None" if there is nothing to report for that section:

## SESSION INTENT

What is the user's primary goal or request? What overall task are you trying to accomplish? This should be concise but complete enough to understand the purpose of the entire session.

## SUMMARY

Extract and record all of the most important context from the conversation history. Include important choices, conclusions, or strategies determined during this conversation. Include the reasoning behind key decisions. Document any rejected options and why they were not pursued.

## ARTIFACTS

What artifacts, files, or resources were created, modified, or accessed during this conversation? For file modifications, list specific file paths and briefly describe the changes made to each. This section prevents silent loss of artifact information.

## NEXT STEPS

What specific tasks remain to be completed to achieve the session intent? What should you do next?

</instructions>

The user will message you with the full message history from which you'll extract context to create a replacement. Carefully read through it all and think deeply about what information is most important to your overall goal and should be saved:

With all of this in mind, please carefully read over the entire conversation history, and extract the most important and relevant context to replace it so that you can free up space in the conversation history.
Respond ONLY with the extracted context. Do not include any additional information, or text before or after the extracted context.

<messages>
Messages to summarize:
{messages}
</messages>"""

_DEFAULT_MESSAGES_TO_KEEP = 20
_DEFAULT_TRIM_TOKEN_LIMIT = 4000
_DEFAULT_FALLBACK_MESSAGE_COUNT = 15


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ContextFraction = tuple[Literal["fraction"], float]
ContextTokens = tuple[Literal["tokens"], int]
ContextMessages = tuple[Literal["messages"], int]
ContextSize = ContextFraction | ContextTokens | ContextMessages


class TriggerClause(TypedDict, total=False):
    tokens: int
    messages: int
    fraction: float


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens_approximately(
    messages: Iterable[Message],
    *,
    chars_per_token: float = 4.0,
) -> int:
    """Approximate token count based on character length."""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        total += max(1, int(len(content) / chars_per_token))
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                total += max(1, int(len(tc.name) / chars_per_token))
                total += max(1, int(len(json.dumps(tc.arguments)) / chars_per_token))
        if msg.reasoning:
            total += max(1, int(len(msg.reasoning) / chars_per_token))
        total += 4
    return total


def _get_approximate_token_counter(model: Any) -> TokenCounter:
    """Tune token counter based on provider type."""
    provider_type = getattr(model, "provider_type", "")
    if "anthropic" in str(provider_type).lower():
        return partial(count_tokens_approximately, chars_per_token=3.3)
    return partial(count_tokens_approximately)


def _load_prompt_file(prompt_file: str) -> str | None:
    """Load summary prompt from file.

    Lookup order:
    1. Per-user: data/users/{user_id}/{prompt_file} (if user_id available)
    2. Default: defaults/{prompt_file} (shipped with repo)
    3. Fallback: None (caller uses DEFAULT_SUMMARY_PROMPT)
    """
    # Try per-user file
    try:
        from src.config import get_settings
        settings = get_settings()
        user_path = Path(settings.data_path) / "users" / "default_user" / prompt_file
        if user_path.exists():
            return user_path.read_text()
    except Exception:
        pass

    # Try default file (shipped with repo)
    try:
        default_path = Path(__file__).parent.parent.parent / "defaults" / prompt_file
        if default_path.exists():
            return default_path.read_text()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class SummarizationMiddleware(Middleware):
    """Summarizes conversation history when token limits are approached.

    Monitors message token counts and automatically summarizes older messages
    when a threshold is reached, preserving recent messages and maintaining
    context continuity by ensuring AI/Tool message pairs remain together.
    """

    def __init__(
        self,
        model: str,
        *,
        trigger: ContextSize | TriggerClause | list[ContextSize | TriggerClause] | None = None,
        keep: ContextSize = ("messages", _DEFAULT_MESSAGES_TO_KEEP),
        token_counter: TokenCounter | None = None,
        summary_prompt: str | None = None,
        prompt_file: str | None = None,
        trim_tokens_to_summarize: int | None = _DEFAULT_TRIM_TOKEN_LIMIT,
        on_summarize: SummaryCallback | None = None,
    ) -> None:
        self.model = model
        self.trigger = trigger
        self.keep = self._validate_context_size(keep, "keep")
        self.trim_tokens_to_summarize = trim_tokens_to_summarize
        self._on_summarize = on_summarize
        self._prompt_file = prompt_file

        # Load summary prompt: explicit param > file > built-in default
        if summary_prompt is not None:
            self.summary_prompt = summary_prompt
        elif prompt_file is not None:
            self.summary_prompt = _load_prompt_file(prompt_file) or DEFAULT_SUMMARY_PROMPT
        else:
            self.summary_prompt = DEFAULT_SUMMARY_PROMPT

        # Normalize trigger into list of clauses (AND within, OR across)
        self._trigger_clauses = self._normalize_trigger(trigger)

        # Token counter
        if token_counter is not None:
            self.token_counter = token_counter
        else:
            self.token_counter = count_tokens_approximately

        self._partial_token_counter: TokenCounter = self.token_counter

    # -- Public API --

    def count_tokens(self, messages: list[Message]) -> int:
        """Public token count for a list of messages."""
        return self.token_counter(messages)

    async def force_summarize(self, state: AgentState, instructions: str | None = None) -> bool:
        """Force summarization, bypassing trigger check.

        Used by overflow recovery in AgentLoop and manual /summarize tool.
        If instructions are provided, they are prepended to focus the summary.
        Returns True if summarization was performed.
        """
        messages = state.messages
        if len(messages) < 2:
            return False

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return False

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)
        if not messages_to_summarize:
            return False

        summary = await self._acreate_summary(messages_to_summarize, instructions=instructions)
        new_messages = self._build_new_messages(summary)
        state.messages = new_messages

        await self._fire_callback(summary)

        logger.info(
            "summarization.force_completed",
            {"old_msg_count": len(messages), "new_msg_count": len(new_messages), "summary_length": len(summary)},
            user_id="system",
        )
        return True

    # -- Hooks --

    def before_model(self, state: AgentState) -> dict[str, Any] | None:
        """Sync version — not fully supported (providers are async)."""
        # Our providers are async-only; sync before_model can't call the summary LLM.
        # Just check if we would trigger and log.
        messages = state.messages
        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None
        logger.info("summarization.sync_trigger_needed", {"total_tokens": total_tokens}, user_id="system")
        return None

    async def abefore_model(self, state: AgentState) -> dict[str, Any] | None:
        """Async version — triggers summarization when threshold is met."""
        messages = state.messages
        total_tokens = self.token_counter(messages)

        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)
        if not messages_to_summarize:
            return None

        logger.info(
            "summarization.triggered",
            {"total_tokens": total_tokens, "msg_count": len(messages), "cutoff": cutoff_index},
            user_id="system",
        )

        summary = await self._acreate_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary) + list(preserved_messages)

        logger.info(
            "summarization.completed",
            {"old_msg_count": len(messages), "new_msg_count": len(new_messages), "summary_length": len(summary)},
            user_id="system",
        )

        await self._fire_callback(summary)

        return {"messages": new_messages}

    # -- Trigger evaluation --

    def _should_summarize(self, messages: list[Message], total_tokens: int) -> bool:
        if not self._trigger_clauses:
            return False

        for clause in self._trigger_clauses:
            clause_met = True
            for kind, value in clause.items():
                if kind == "messages" and len(messages) < value:
                    clause_met = False
                    break
                if kind == "tokens":
                    threshold = float(value)
                    if (
                        total_tokens < threshold
                        and not self._should_summarize_based_on_reported_tokens(messages, threshold)
                    ):
                        clause_met = False
                        break
                if kind == "fraction":
                    max_input = self._get_model_max_input_tokens()
                    if max_input is None:
                        clause_met = False
                        break
                    threshold = int(max_input * value)
                    if threshold <= 0:
                        threshold = 1
                    if (
                        total_tokens < threshold
                        and not self._should_summarize_based_on_reported_tokens(messages, float(threshold))
                    ):
                        clause_met = False
                        break
            if clause_met:
                return True
        return False

    def _should_summarize_based_on_reported_tokens(
        self, messages: list[Message], threshold: float
    ) -> bool:
        """Check if provider-reported input tokens from last AI message exceeds threshold."""
        last_ai = next((m for m in reversed(messages) if m.role == "assistant"), None)
        if last_ai and last_ai.usage and last_ai.usage.input_tokens:
            if last_ai.usage.input_tokens >= threshold:
                return True
        return False

    def _get_model_max_input_tokens(self) -> int | None:
        """Get model's max input tokens from registry."""
        try:
            from src.sdk.registry import get_model_info
            info = get_model_info(self.model)
            if info and info.context_window:
                return info.context_window
        except Exception:
            pass
        return None

    # -- Cutoff determination --

    def _determine_cutoff_index(self, messages: list[Message]) -> int:
        """Choose cutoff index respecting retention configuration."""
        kind, value = self.keep

        if kind in ("tokens", "fraction"):
            token_cutoff = self._find_token_based_cutoff(messages)
            if token_cutoff is not None:
                return token_cutoff
            return self._find_safe_cutoff(messages, _DEFAULT_MESSAGES_TO_KEEP)

        return self._find_safe_cutoff(messages, int(value))

    def _find_token_based_cutoff(self, messages: list[Message]) -> int | None:
        """Find cutoff index based on target token retention."""
        if not messages:
            return 0

        kind, value = self.keep
        if kind == "fraction":
            max_input = self._get_model_max_input_tokens()
            if max_input is None:
                return None
            target = int(max_input * value)
        elif kind == "tokens":
            target = int(value)
        else:
            return None

        if target <= 0:
            target = 1

        if self.token_counter(messages) <= target:
            return 0

        # Binary search for earliest index that keeps suffix within budget
        left, right = 0, len(messages)
        cutoff = len(messages)
        for _ in range(len(messages).bit_length() + 1):
            if left >= right:
                break
            mid = (left + right) // 2
            if self._partial_token_counter(messages[mid:]) <= target:
                cutoff = mid
                right = mid
            else:
                left = mid + 1

        if cutoff >= len(messages):
            cutoff = max(0, len(messages) - 1)

        return self._find_safe_cutoff_point(messages, cutoff)

    def _find_safe_cutoff(self, messages: list[Message], messages_to_keep: int) -> int:
        """Find safe cutoff that preserves AI/Tool message pairs."""
        if len(messages) <= messages_to_keep:
            return 0
        target_cutoff = len(messages) - messages_to_keep
        return self._find_safe_cutoff_point(messages, target_cutoff)

    @staticmethod
    def _find_safe_cutoff_point(messages: list[Message], cutoff_index: int) -> int:
        """Find safe cutoff that doesn't split AI/Tool message pairs.

        If the message at cutoff_index is a tool result, search backward for
        the AIMessage that initiated the tool call and include it.
        """
        if cutoff_index >= len(messages):
            return cutoff_index

        if messages[cutoff_index].role != "tool":
            return cutoff_index

        # Collect tool_call_ids from consecutive ToolMessages at/after cutoff
        tool_call_ids: set[str] = set()
        idx = cutoff_index
        while idx < len(messages) and messages[idx].role == "tool":
            if messages[idx].tool_call_id:
                tool_call_ids.add(messages[idx].tool_call_id)
            idx += 1

        # Search backward for AIMessage with matching tool_calls
        for i in range(cutoff_index - 1, -1, -1):
            msg = messages[i]
            if msg.role == "assistant" and msg.tool_calls:
                ai_ids = {tc.id for tc in msg.tool_calls if tc.id}
                if tool_call_ids & ai_ids:
                    return i

        # Fallback: advance past ToolMessages to avoid orphaned tool responses
        return idx

    # -- Message partitioning --

    @staticmethod
    def _partition_messages(
        messages: list[Message], cutoff_index: int
    ) -> tuple[list[Message], list[Message]]:
        return messages[:cutoff_index], messages[cutoff_index:]

    @staticmethod
    def _build_new_messages(summary: str) -> list[Message]:
        return [
            Message(
                role="user",
                content=f"Here is a summary of the conversation to date:\n\n{summary}",
                source="summarization_middleware",
            )
        ]

    # -- Message trimming --

    def _trim_messages_for_summary(self, messages: list[Message]) -> list[Message]:
        """Trim messages to fit within trim_tokens_to_summarize."""
        if self.trim_tokens_to_summarize is None:
            return messages
        total = 0
        cutoff = 0
        for i in range(len(messages) - 1, -1, -1):
            t = self._count_message_tokens(messages[i])
            if total + t > self.trim_tokens_to_summarize:
                cutoff = i + 1
                break
            total += t
        else:
            cutoff = 0
        return messages[cutoff:] if cutoff > 0 else messages

    def _count_message_tokens(self, msg: Message) -> int:
        return self.token_counter([msg])

    # -- Summary generation --

    def _messages_to_conversation_text(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            lines.append(f"[{role}] {content}")
        return "\n\n".join(lines)

    async def _acreate_summary(
        self, messages_to_summarize: list[Message], instructions: str | None = None
    ) -> str:
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed:
            return "Previous conversation was too long to summarize."

        formatted = self._messages_to_conversation_text(trimmed)
        if instructions:
            formatted = f"[Focus: {instructions}]\n\n{formatted}"

        prompt = self.summary_prompt.format(messages=formatted)

        try:
            from src.sdk.loop import get_current_agent_loop
            from src.sdk.providers.factory import create_model_from_config

            # Prefer the current loop's provider (already Langfuse-wrapped with active trace context)
            loop = get_current_agent_loop()
            if loop is not None and hasattr(loop.provider, "chat"):
                provider = loop.provider
            else:
                provider = create_model_from_config(self.model)

            # Create a Langfuse generation observation if Langfuse is active
            summary_messages = [
                Message.system("You are a context extraction assistant."),
                Message.user(prompt),
            ]

            try:
                from src.sdk.langfuse_tracer import LangfuseTracer
                if LangfuseTracer.is_enabled():
                    client = LangfuseTracer._get_client()
                    if client:
                        with client.start_as_current_observation(
                            as_type="generation",
                            name="SummarizationMiddleware_summary",
                            model=self.model,
                        ) as gen:
                            try:
                                gen.update(input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in summary_messages])
                            except Exception:
                                pass
                            # Call the original (unwrapped) chat to avoid double-tracing
                            original_chat = getattr(provider, "_original_chat", None)
                            if original_chat:
                                response = await original_chat(summary_messages)
                            else:
                                response = await provider.chat(summary_messages)
                            try:
                                gen.update(output=response.model_dump() if hasattr(response, "model_dump") else str(response))
                                if hasattr(response, "usage") and response.usage:
                                    gen.update(usage_details={
                                        "input": response.usage.input_tokens,
                                        "output": response.usage.output_tokens,
                                        "reasoning": response.usage.reasoning_tokens,
                                    })
                            except Exception:
                                pass
                            content = response.content if isinstance(response.content, str) else str(response.content)
                            return content.strip()
            except ImportError:
                pass

            # No Langfuse — just call provider directly
            response = await provider.chat(summary_messages)
            content = response.content if isinstance(response.content, str) else str(response.content)
            return content.strip()
        except Exception as e:
            logger.warning("summarization.generation_failed", {"error": str(e)}, user_id="system")
            return f"Error generating summary: {e!s}"

    # -- Callback --

    async def _fire_callback(self, summary: str) -> None:
        if self._on_summarize is not None:
            try:
                result = self._on_summarize(summary)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.warning("summarization.callback_failed", {"error": str(e)}, user_id="system")

    # -- Trigger normalization --

    def _normalize_trigger(
        self,
        trigger: ContextSize | TriggerClause | list[ContextSize | TriggerClause] | None,
    ) -> list[TriggerClause]:
        if trigger is None:
            return []

        def _tuple_to_clause(t: ContextSize) -> TriggerClause:
            kind, value = self._validate_context_size(t, "trigger")
            return {kind: value}  # type: ignore[return-value]

        def _validate_mapping(m: dict[str, Any]) -> TriggerClause:
            if not m:
                raise ValueError("trigger clause must specify at least one of 'tokens', 'messages', 'fraction'")
            out: dict[str, int | float] = {}
            for k, v in m.items():
                if k not in {"tokens", "messages", "fraction"}:
                    raise ValueError(f"Unsupported trigger metric: {k!r}")
                if isinstance(v, bool):
                    raise ValueError(f"{k} trigger value must be numeric, got {v!r}")
                if k == "fraction":
                    if not isinstance(v, (int, float)):
                        raise ValueError(f"Fraction trigger values must be numeric, got {v!r}")
                elif not isinstance(v, int):
                    raise ValueError(f"{k} trigger values must be integers, got {v!r}")
                self._validate_context_size((k, v), "trigger")  # type: ignore[arg-type]
                out[k] = v
            return out  # type: ignore[return-value]

        clauses: list[TriggerClause] = []
        if isinstance(trigger, dict):
            clauses.append(_validate_mapping(trigger))
        elif isinstance(trigger, tuple):
            clauses.append(_tuple_to_clause(trigger))
        elif isinstance(trigger, list):
            for item in trigger:
                if isinstance(item, dict):
                    clauses.append(_validate_mapping(item))
                elif isinstance(item, tuple):
                    clauses.append(_tuple_to_clause(item))
                else:
                    raise TypeError(f"Unsupported trigger item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported trigger type: {type(trigger)}")
        return clauses

    @staticmethod
    def _validate_context_size(context: ContextSize, parameter_name: str) -> ContextSize:
        kind, value = context
        if kind == "fraction":
            if not 0 < value <= 1:
                raise ValueError(f"Fractional {parameter_name} values must be between 0 and 1, got {value}.")
        elif kind in ("tokens", "messages"):
            if value <= 0:
                raise ValueError(f"{parameter_name} thresholds must be greater than 0, got {value}.")
        else:
            raise ValueError(f"Unsupported context size type {kind} for {parameter_name}.")
        return context


__all__ = ["SummarizationMiddleware", "ContextSize", "TriggerClause"]
