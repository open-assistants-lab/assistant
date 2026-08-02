"""Summarization middleware — aligned with LangChain's SummarizationMiddleware.

Monitors message token counts and automatically summarizes older messages when
a threshold is reached, preserving recent messages and maintaining context
continuity by ensuring AI/Tool message pairs remain together.

Extensions over LangChain:
- typed summary sink (persist lossless compression artifacts)
- force_summarize() (overflow recovery + manual /summarize)
- Settings-based config (config.yaml + env vars)
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from src.app_logging import get_logger
from src.sdk.compression import (
    CompressionArtifact,
    CompressionContext,
    CompressionResult,
    CompressionStatus,
    CompressionTelemetry,
    PersistenceStatus,
    SummaryPersistenceResult,
    SummarySink,
)
from src.sdk.messages import Message, Usage
from src.sdk.middleware import Middleware
from src.sdk.run_models import ContextSnapshot, UsageAggregate
from src.sdk.state import AgentState

logger = get_logger()

TokenCounter = Callable[[Iterable[Message]], int]
SummaryProviderFactory = Callable[[], Any]

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


class _SummaryGenerationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def _load_prompt_file(prompt_file: str, user_id: str = "default_user") -> str:
    """Load summary prompt from per-user file.

    If the per-user file doesn't exist, seed it from seeds/prompts/ first.
    This ensures every user has their own editable copy.
    """
    from src.config import get_settings
    settings = get_settings()

    user_path = Path(settings.data_path) / "users" / user_id / prompt_file
    seed_path = Path(__file__).parent.parent.parent / "seeds" / "prompts" / prompt_file

    # Seed: copy from seeds/ to per-user if user file doesn't exist
    if not user_path.exists() and seed_path.exists():
        user_path.parent.mkdir(parents=True, exist_ok=True)
        user_path.write_text(seed_path.read_text())
        logger.info("summarization.prompt_seeded", {"user_id": user_id, "path": str(user_path)}, user_id=user_id)

    # Read per-user file
    if user_path.exists():
        return user_path.read_text()

    # Fallback to seeds/ if per-user seeding failed
    if seed_path.exists():
        return seed_path.read_text()

    # Last resort: built-in constant
    return DEFAULT_SUMMARY_PROMPT


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
        user_id: str | None = None,
        trim_tokens_to_summarize: int | None = _DEFAULT_TRIM_TOKEN_LIMIT,
        summary_sink: SummarySink | None = None,
        summary_provider_factory: SummaryProviderFactory | None = None,
    ) -> None:
        self.model = model
        self.trigger = trigger
        self.keep = self._validate_context_size(keep, "keep")
        self.trim_tokens_to_summarize = trim_tokens_to_summarize
        self._summary_sink = summary_sink
        self._summary_provider_factory = summary_provider_factory
        self._summary_provider: Any | None = None
        self._prompt_file = prompt_file
        self.user_id = user_id or "default_user"

        # Load summary prompt: explicit param > file (seeded per user) > built-in default
        if summary_prompt is not None:
            self.summary_prompt = summary_prompt
        elif prompt_file is not None and user_id is not None:
            self.summary_prompt = _load_prompt_file(prompt_file, user_id=user_id)
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

    async def force_summarize(
        self,
        state: AgentState,
        context: CompressionContext,
        instructions: str | None = None,
    ) -> CompressionResult:
        """Compress context without checking the automatic trigger."""
        result = await self._compress(state.messages, context, instructions=instructions)
        if result.compressed and result.artifact is not None:
            state.messages = list(result.artifact.replacement_messages)
            state.extra["_compression_result"] = result
        return result

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
        context = state.extra.get("_compression_context")
        if not isinstance(context, CompressionContext):
            return None
        if not self._should_summarize(messages, total_tokens):
            result = self._result_without_artifact(
                context, CompressionStatus.SKIPPED, messages, error_code="trigger_not_met"
            )
        else:
            result = await self._compress(messages, context)
        update: dict[str, Any] = {"extra": {"_compression_result": result}}
        if result.compressed and result.artifact is not None:
            update["messages"] = list(result.artifact.replacement_messages)
        return update

    # -- Trigger evaluation --

    def _should_summarize(self, messages: list[Message], total_tokens: int) -> bool:
        if not self._trigger_clauses:
            return False

        for clause in self._trigger_clauses:
            clause_met = True
            for kind, value in clause.items():
                numeric_value = cast(int | float, value)
                if kind == "messages" and len(messages) < int(numeric_value):
                    clause_met = False
                    break
                if kind == "tokens":
                    threshold = float(numeric_value)
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
                    threshold = int(max_input * float(numeric_value))
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
            tool_call_id = messages[idx].tool_call_id
            if tool_call_id:
                tool_call_ids.add(tool_call_id)
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
        message = Message(
            role="user",
            content=f"Here is a summary of the conversation to date:\n\n{summary}",
        )
        return [message.model_copy(update={"source": "summarization_middleware"})]

    # -- Message trimming --

    def _trim_messages_for_summary(self, messages: list[Message]) -> list[Message]:
        """Return the largest safe chronological prefix within the summary budget."""
        if self.trim_tokens_to_summarize is None:
            return messages
        total = 0
        end = 0
        for message in messages:
            message_tokens = self._count_message_tokens(message)
            if total + message_tokens > self.trim_tokens_to_summarize:
                break
            total += message_tokens
            end += 1
        if end and end < len(messages) and messages[end].role == "tool":
            end = min(end, self._find_safe_cutoff_point(messages, end))
        return messages[:end]

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
    ) -> tuple[str, UsageAggregate]:
        if not messages_to_summarize:
            raise _SummaryGenerationError("empty_input", "no messages were selected")

        formatted = self._messages_to_conversation_text(messages_to_summarize)
        if instructions:
            formatted = f"[Focus: {instructions}]\n\n{formatted}"

        prompt = self.summary_prompt.format(messages=formatted)

        summary_messages = [
            Message.system("You are a context extraction assistant."),
            Message.user(prompt),
        ]
        try:
            provider = self._get_summary_provider()
            response = await self._call_summary_provider(provider, summary_messages)
        except Exception as exc:
            logger.warning(
                "summarization.generation_failed",
                {"error_type": type(exc).__name__},
                user_id=self.user_id,
            )
            raise _SummaryGenerationError("provider_error", "summary provider failed") from exc
        content = response.content if isinstance(response.content, str) else ""
        content = content.strip()
        if not content:
            raise _SummaryGenerationError("empty_response", "summary provider returned no content")
        return content, self._usage_aggregate(getattr(response, "usage", None))

    def _get_summary_provider(self) -> Any:
        if self._summary_provider is None:
            if self._summary_provider_factory is not None:
                self._summary_provider = self._summary_provider_factory()
            else:
                from src.sdk.providers.factory import create_model_from_config

                self._summary_provider = create_model_from_config(self.model, user_id=self.user_id)
        return self._summary_provider

    async def _call_summary_provider(self, provider: Any, messages: list[Message]) -> Message:
        try:
            from src.sdk.langfuse_tracer import LangfuseTracer

            if LangfuseTracer.is_enabled():
                client = LangfuseTracer._get_client()
                if client:
                    with client.start_as_current_observation(
                        as_type="generation",
                        name="SummarizationMiddleware_summary",
                        model=self.model,
                    ) as generation:
                        response = await provider.chat(messages)
                        usage = getattr(response, "usage", None)
                        if usage is not None:
                            generation.update(
                                usage_details={
                                    "input": usage.input_tokens,
                                    "output": usage.output_tokens,
                                    "reasoning": usage.reasoning_tokens,
                                }
                            )
                        return cast(Message, response)
        except ImportError:
            pass
        return cast(Message, await provider.chat(messages))

    def _usage_aggregate(self, usage: Usage | None) -> UsageAggregate:
        if usage is None:
            return UsageAggregate()
        return UsageAggregate(
            available=True,
            calls=1,
            models=(self.model,),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
        )

    async def _compress(
        self,
        messages: list[Message],
        context: CompressionContext,
        instructions: str | None = None,
    ) -> CompressionResult:
        before_count = len(messages)
        before_tokens = self.token_counter(messages)
        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return self._result_without_artifact(
                context,
                CompressionStatus.SKIPPED,
                messages,
                error_code="no_cutoff",
            )

        old_messages, recent_messages = self._partition_messages(messages, cutoff_index)
        summarized_messages = self._trim_messages_for_summary(old_messages)
        if not summarized_messages:
            return self._result_without_artifact(
                context,
                CompressionStatus.SKIPPED,
                messages,
                error_code="summary_budget_too_small",
            )
        old_remainder = old_messages[len(summarized_messages) :]

        try:
            summary, usage = await self._acreate_summary(
                summarized_messages, instructions=instructions
            )
        except _SummaryGenerationError as exc:
            return self._result_without_artifact(
                context,
                CompressionStatus.FAILED,
                messages,
                error_code=exc.code,
                error_message=str(exc),
            )

        summary_message = self._build_new_messages(summary)[0]
        replacement_messages = (summary_message, *old_remainder, *recent_messages)
        summarized_ids = self._storage_ids(summarized_messages)
        preserved_ids = self._storage_ids([*old_remainder, *recent_messages])
        persistence_eligible = len(summarized_ids) == len(summarized_messages)
        artifact = CompressionArtifact(
            summary=summary,
            replacement_messages=replacement_messages,
            summarized_message_ids=summarized_ids,
            preserved_message_ids=preserved_ids,
            persistence_eligible=persistence_eligible,
        )
        persistence = SummaryPersistenceResult(status=PersistenceStatus.NOT_REQUESTED)

        if persistence_eligible and self._summary_sink is not None:
            try:
                sink_result = self._summary_sink(artifact, context)
                if inspect.isawaitable(sink_result):
                    sink_result = await sink_result
                if not isinstance(sink_result, SummaryPersistenceResult):
                    raise TypeError("summary sink must return SummaryPersistenceResult")
                persistence = sink_result
            except Exception as exc:
                logger.warning(
                    "summarization.persistence_failed",
                    {"error_type": type(exc).__name__},
                    user_id=self.user_id,
                )
                persistence = SummaryPersistenceResult(status=PersistenceStatus.FAILED)

        if persistence.status is PersistenceStatus.SUCCEEDED:
            summary_message = summary_message.model_copy(
                update={"storage_id": persistence.summary_id}
            )
            replacement_messages = (summary_message, *old_remainder, *recent_messages)
            artifact = artifact.model_copy(
                update={
                    "replacement_messages": replacement_messages,
                    "persisted_summary_id": persistence.summary_id,
                }
            )

        after_messages = list(replacement_messages)
        after_tokens = self.token_counter(after_messages)
        after_context = self._after_context(context.before, after_tokens)
        telemetry = CompressionTelemetry(
            status=CompressionStatus.SUCCEEDED,
            reason=context.reason,
            before_message_count=before_count,
            after_message_count=len(after_messages),
            before_token_count=before_tokens,
            after_token_count=after_tokens,
            summarized_message_count=len(summarized_messages),
            preserved_message_count=len(old_remainder) + len(recent_messages),
            replacement_message_count=len(replacement_messages),
            summary_model=self.model,
            summarizer_usage=usage,
            persistence=persistence,
            before_context=context.before,
            after_context=after_context,
        )
        logger.info(
            "summarization.completed",
            {
                "before_message_count": before_count,
                "after_message_count": len(after_messages),
                "summarized_message_count": len(summarized_messages),
            },
            user_id=self.user_id,
        )
        return CompressionResult(artifact=artifact, telemetry=telemetry)

    def _result_without_artifact(
        self,
        context: CompressionContext,
        status: CompressionStatus,
        messages: list[Message],
        *,
        error_code: str,
        error_message: str | None = None,
    ) -> CompressionResult:
        count = len(messages)
        tokens = self.token_counter(messages)
        return CompressionResult(
            telemetry=CompressionTelemetry(
                status=status,
                reason=context.reason,
                before_message_count=count,
                after_message_count=count,
                before_token_count=tokens,
                after_token_count=tokens,
                preserved_message_count=count,
                summary_model=self.model,
                persistence=SummaryPersistenceResult(status=PersistenceStatus.NOT_REQUESTED),
                error_code=error_code,
                error_message=error_message,
                before_context=context.before,
            )
        )

    @staticmethod
    def _storage_ids(messages: Iterable[Message]) -> tuple[str, ...]:
        ids: list[str] = []
        for message in messages:
            storage_id = getattr(message, "storage_id", None)
            if isinstance(storage_id, str) and storage_id.strip() and storage_id not in ids:
                ids.append(storage_id)
        return tuple(ids)

    @staticmethod
    def _after_context(before: ContextSnapshot | None, tokens: int) -> ContextSnapshot | None:
        if before is None:
            return None
        percentage = tokens / before.context_window * 100 if before.context_window else None
        return ContextSnapshot.model_validate(
            {
                **before.model_dump(),
                "estimated_tokens": tokens,
                "percentage": percentage,
            }
        )

    # -- Trigger normalization --

    def _normalize_trigger(
        self,
        trigger: ContextSize | TriggerClause | list[ContextSize | TriggerClause] | None,
    ) -> list[TriggerClause]:
        if trigger is None:
            return []

        def _tuple_to_clause(t: ContextSize) -> TriggerClause:
            kind, value = self._validate_context_size(t, "trigger")
            return cast(TriggerClause, {kind: value})

        def _validate_mapping(m: Mapping[str, Any]) -> TriggerClause:
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
            return cast(TriggerClause, out)

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
