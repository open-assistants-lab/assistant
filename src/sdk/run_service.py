"""RunService — single entry point for executing a user turn.

Owns user-message persistence, history loading, settings snapshotting,
runner execution, final persistence, and terminal outcome construction.
Routers do not write conversation records directly.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger
from src.sdk.langfuse_tracer import LangfuseTracer
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk
from src.sdk.middleware_rubric import RubricMiddleware
from src.sdk.run_events import (
    BlockData,
    BlockDeltaData,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    InterruptData,
    InterruptEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    RevisionStartData,
    RevisionStartEvent,
    RubricEndData,
    RubricEndEvent,
    RubricStartData,
    RubricStartEvent,
    RunEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolDeltaData,
    ToolEndData,
    ToolInputDeltaEvent,
    ToolInputEndEvent,
    ToolInputStartEvent,
    ToolResultData,
    ToolResultEvent,
    ToolStartData,
    UsageEvent,
    UsageEventData,
    parse_run_event,
)
from src.sdk.run_models import (
    CriterionEvaluation,
    RubricAvailability,
    RubricEvaluation,
    RubricEvaluationResult,
    RunResult,
    RunStatus,
    RunUsage,
    TerminalRubricStatus,
    UsageAggregate,
    VerificationOutcome,
)
from src.sdk.runner import get_sdk_loop, register_user_loop, unregister_user_loop
from src.sdk.session_worker import SessionBusyError, SessionLock, SessionWorkerRegistry
from src.storage.messages import Message as StorageMessage
from src.storage.messages import MessageStore

logger = get_logger()


def _storage_messages_to_sdk(history: list[Any]) -> list[Message]:
    """Convert storage-layer Message dataclasses to SDK Message objects.

    The storage layer (src.storage.messages.Message) is a plain dataclass with
    a different field set than the SDK Message (notably no `reasoning`,
    `tool_calls`, `tool_call_id`, `name`). The context measurer, providers,
    and middlewares all expect SDK Message objects, so history loaded from the
    message store must be normalized before entering the agent loop.

    Storage messages with role='reasoning' (persisted by the streaming SSE
    handler's persist_reasoning_message) are converted to SDK assistant
    messages with the reasoning field set, matching the runner's history
    loader at runner.py:826.
    """
    converted: list[Message] = []
    for row in history:
        # Already an SDK Message (e.g. a freshly constructed one) — keep as is.
        if isinstance(row, Message):
            converted.append(row)
            continue
        role = row.role
        if role == "reasoning":
            # Storage reasoning messages → SDK assistant with reasoning field.
            converted.append(
                Message(
                    role="assistant",
                    content="",
                    reasoning=row.content,
                    storage_id=getattr(row, "id", None),
                    storage_ts=str(getattr(row, "ts", None)) if getattr(row, "ts", None) is not None else None,
                    storage_session_id=getattr(row, "session_id", None),
                    source=getattr(row, "source", None),
                )
            )
        elif role == "summary":
            # Storage summary messages → SDK user message with the same
            # [SUMMARY OF PREVIOUS CONVERSATION] framing the runner's history
            # loader uses (runner.py:730). Without this branch the SDK Message
            # role validation rejects role="summary" outright.
            converted.append(
                Message(
                    role="user",
                    content=f"[SUMMARY OF PREVIOUS CONVERSATION]\n{row.content}",
                    source=getattr(row, "source", None) or "summarization_middleware",
                    storage_id=getattr(row, "id", None),
                    storage_ts=str(getattr(row, "ts", None)) if getattr(row, "ts", None) is not None else None,
                    storage_session_id=getattr(row, "session_id", None),
                )
            )
        else:
            converted.append(
                Message(
                    role=role,
                    content=row.content,
                    storage_id=getattr(row, "id", None),
                    storage_ts=str(getattr(row, "ts", None)) if getattr(row, "ts", None) is not None else None,
                    storage_session_id=getattr(row, "session_id", None),
                    source=getattr(row, "source", None),
                )
            )
    return converted


def _sdk_message_to_storage(msg: Message, session_id: str) -> StorageMessage:
    """Convert an SDK Message to the storage-layer Message persist_run expects."""
    return StorageMessage(
        id="",
        ts=datetime.now(UTC),
        role=msg.role,
        content=str(msg.content or ""),
        metadata={"stream": True},
        session_id=session_id,
    )




def _tool_audit_records(
    loop: Any, session_id: str
) -> list[StorageMessage]:
    """Collect the run's executed tool messages for persistence.

    Tool executions are visible in the transcript but excluded from the
    model context (audit records) — without this they were silently
    dropped from the stored history on the success path, so a reload
    showed the response without the tool calls that produced it.
    """
    state = getattr(loop, "state", None)
    if state is None:
        return []
    records: list[StorageMessage] = []
    for m in state.messages:
        if m.role != "tool":
            continue
        meta: dict[str, Any] = {"stream": True}
        if getattr(m, "name", None):
            meta["tool_name"] = m.name
        if getattr(m, "tool_call_id", None):
            meta["tool_call_id"] = m.tool_call_id
        records.append(
            StorageMessage(
                id="",
                ts=datetime.now(UTC),
                role="tool",
                content=str(m.content or ""),
                metadata=meta,
                session_id=session_id,
            )
        )
    return records



def _verification_metadata(verification: Any) -> dict[str, Any] | None:
    """The verification verdict for the stored turn metadata (reload renders
    the Rubric row from it). None when verification never ran."""
    if verification is None:
        return None
    availability = getattr(verification, "availability", None)
    if availability is None or availability.value != "on":
        return None
    evaluations = getattr(verification, "evaluations", None) or []
    if not evaluations:
        return None
    return {
        "status": verification.status.value,
        "attempts": verification.attempts,
        "max_attempts": verification.max_attempts,
        "evaluations": [
            {
                "attempt": e.attempt,
                "result": e.result.value,
                "explanation": e.explanation,
                "criteria": [{"name": c.name, "passed": c.passed, "gap": c.gap} for c in e.criteria],
            }
            for e in evaluations
        ],
    }

def _to_evaluation_result(raw: str) -> RubricEvaluationResult:
    """Map the grader's raw result string to RubricEvaluationResult.

    The grader returns "failed" for a malformed/contradictory rubric; that
    maps to INVALID_RUBRIC (the terminal status for ungradable rubrics).
    """
    if raw == "failed":
        return RubricEvaluationResult.INVALID_RUBRIC
    return RubricEvaluationResult(raw)


def _evaluation_from_dict(evaluation: dict[str, Any], attempt: int) -> RubricEvaluation:
    """Build the contract RubricEvaluation from a grader verdict dict."""
    return RubricEvaluation(
        grading_run_id=evaluation["grading_run_id"],
        attempt=attempt,
        result=_to_evaluation_result(evaluation["result"]),
        explanation=evaluation["explanation"],
        criteria=tuple(
            CriterionEvaluation(
                name=c["name"],
                passed=c["passed"],
                gap=c.get("gap"),
            )
            for c in evaluation["criteria"]
        ),
        passed_count=sum(1 for c in evaluation["criteria"] if c["passed"]),
        total_count=len(evaluation["criteria"]),
    )


def _revision_prompt(evaluation: dict[str, Any]) -> str:
    from src.sdk.middleware_rubric import _revision_prompt as _rp
    return _rp(evaluation)


def _stream_chunk_to_event(
    chunk: StreamChunk,
    emit: Callable[[type[RunEvent], dict[str, Any], int], RunEvent],
    attempt: int,
    model_id: str = "",
    accumulated_args: dict[str, str] | None = None,
) -> RunEvent | None:
    """Convert a StreamChunk to the corresponding RunEvent.

    accumulated_args is a mutable dict keyed by call_id for tracking
    tool_input_delta arguments across chunks. Returns None for terminal
    chunks (done/error) that have no RunEvent projection.
    """
    if accumulated_args is None:
        accumulated_args = {}
    ct = chunk.canonical_type
    if ct == "text_start":
        return emit(TextStartEvent, BlockData(block_id=chunk.call_id or str(uuid.uuid4())).model_dump(), attempt)
    elif ct == "text_delta":
        return emit(TextDeltaEvent, BlockDeltaData(block_id="text", delta=chunk.content).model_dump(), attempt)
    elif ct == "text_end":
        return emit(TextEndEvent, BlockData(block_id="text").model_dump(), attempt)
    elif ct == "reasoning_start":
        return emit(ReasoningStartEvent, BlockData(block_id=chunk.call_id or str(uuid.uuid4())).model_dump(), attempt)
    elif ct == "reasoning_delta":
        return emit(ReasoningDeltaEvent, BlockDeltaData(block_id="reasoning", delta=chunk.content).model_dump(), attempt)
    elif ct == "reasoning_end":
        return emit(ReasoningEndEvent, BlockData(block_id="reasoning").model_dump(), attempt)
    elif ct == "tool_input_start":
        return emit(ToolInputStartEvent, ToolStartData(
            block_id=chunk.call_id or str(uuid.uuid4()),
            tool_call_id=chunk.call_id or "",
            name=chunk.tool or "unknown",
        ).model_dump(), attempt)
    elif ct == "tool_input_delta":
        call_id = chunk.call_id or ""
        if call_id:
            accumulated_args[call_id] = accumulated_args.get(call_id, "") + (chunk.content or "")
        return emit(ToolInputDeltaEvent, ToolDeltaData(
            block_id="tool", tool_call_id=call_id, delta=chunk.content,
        ).model_dump(), attempt)
    elif ct == "tool_input_end":
        call_id = chunk.call_id or ""
        args_str = accumulated_args.pop(call_id, "")
        import json
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            args = {}
        return emit(ToolInputEndEvent, ToolEndData(
            block_id="tool", tool_call_id=call_id, arguments=args,
        ).model_dump(), attempt)
    elif ct == "tool_result":
        return emit(ToolResultEvent, ToolResultData(
            block_id=chunk.call_id or str(uuid.uuid4()),
            tool_call_id=chunk.call_id or "",
            name=chunk.tool or "unknown",
            status="completed",
            content=chunk.result_preview or "",
        ).model_dump(), attempt)
    elif ct == "interrupt":
        return emit(InterruptEvent, InterruptData(
            tool=chunk.tool or "unknown",
            call_id=chunk.call_id or "",
            args=chunk.args or {},
        ).model_dump(), attempt)
    elif ct == "usage" and chunk.usage:
        return emit(UsageEvent, UsageEventData(
            category="agent",
            model=model_id,
            llm_call_index=1,
            usage={
                "input_tokens": chunk.usage.input_tokens or 0,
                "output_tokens": chunk.usage.output_tokens or 0,
                "reasoning_tokens": chunk.usage.reasoning_tokens or 0,
                "cache_read_tokens": chunk.usage.cache_read_tokens or 0,
                "cache_creation_tokens": chunk.usage.cache_creation_tokens or 0,
            },
        ).model_dump(), attempt)
    elif ct == "done":
        return None
    elif ct == "error":
        return None
    return emit(TextDeltaEvent, BlockDeltaData(block_id="text", delta=chunk.content).model_dump(), attempt)


class RunService:
    """Single entry point for executing a user turn."""

    def __init__(
        self,
        user_id: str,
        registry: SessionWorkerRegistry,
        message_store: MessageStore,
    ) -> None:
        self._user_id = user_id
        self._registry = registry
        self._message_store = message_store

    async def execute(
        self,
        session_id: str,
        prompt: str,
        model: str | None = None,
        provider_keys: dict[str, str] | None = None,
        rubric: str | None = None,
    ) -> RunResult:
        """Non-streaming execution. Returns immutable RunResult."""
        lock = await self._registry.acquire(session_id)
        try:
            # Run-level trace root: the loop's agent_run span and the rubric
            # grader both nest under it (no-op when Langfuse is disabled).
            with LangfuseTracer.trace_run(self._user_id, session_id):
                return await self._run(session_id, prompt, model, provider_keys, lock, rubric)
        except SessionBusyError:
            raise
        finally:
            await self._registry.release(session_id)

    async def execute_stream(
        self,
        session_id: str,
        prompt: str,
        model: str | None = None,
        provider_keys: dict[str, str] | None = None,
        rubric: str | None = None,
    ) -> AsyncIterator[RunEvent]:
        """Streaming execution. Yields RunEvent envelopes."""
        lock = await self._registry.acquire(session_id)
        try:
            # Run-level trace root covering the whole stream (agent + grader).
            with LangfuseTracer.trace_run(self._user_id, session_id):
                async for event in self._run_stream(session_id, prompt, model, provider_keys, lock, rubric):
                    yield event
        finally:
            await self._registry.release(session_id)

    async def _run(
        self,
        session_id: str,
        prompt: str,
        model: str | None,
        provider_keys: dict[str, str] | None,
        lock: SessionLock,
        rubric: str | None = None,
    ) -> RunResult:
        run_id = str(uuid.uuid4())
        user_msg_id = self._message_store.add_message(
            "user", prompt, metadata={"run_id": run_id}, session_id=session_id
        )

        loop = await get_sdk_loop(
            self._user_id, "personal", model=model, provider_keys=provider_keys, session_id=session_id
        )
        register_user_loop(self._user_id, loop, session_id=session_id)
        try:
            history = self._message_store.get_messages_with_summary(session_id, limit=50)
            messages = _storage_messages_to_sdk(list(history)) + [Message.user(prompt)]

            result = await self._run_bounded_orchestration(loop, messages, run_id, session_id, lock, rubric)

            persisted_id = self._message_store.persist_run(
                run_id=run_id,
                session_id=session_id,
                user_message_id=user_msg_id,
                final_answer=_sdk_message_to_storage(Message.assistant(content=result.response), session_id),
                audit_records=_tool_audit_records(loop, session_id),
                metadata={"model": result.model, "verification": _verification_metadata(result.verification)},
            )

            return RunResult(
                run_id=run_id,
                session_id=session_id,
                status=result.status,
                attempt=result.attempt,
                model=result.model,
                response=result.response,
                final_message_id=persisted_id,
                usage=result.usage,
                verification=result.verification,
                tool_calls=result.tool_calls,
                persisted_at=datetime.now(UTC),
            )
        finally:
            unregister_user_loop(self._user_id, loop, session_id=session_id)

    async def _run_stream(
        self,
        session_id: str,
        prompt: str,
        model: str | None,
        provider_keys: dict[str, str] | None,
        lock: SessionLock,
        rubric: str | None = None,
    ) -> AsyncIterator[RunEvent]:
        run_id = str(uuid.uuid4())
        sequence = 0

        def _envelope(event_cls: type[RunEvent], data: Any, attempt: int = 1) -> dict[str, Any]:
            nonlocal sequence
            sequence += 1
            return {
                "schema_version": 1,
                "event_id": str(uuid.uuid4()),
                "sequence": sequence,
                "timestamp": datetime.now(UTC),
                "session_id": session_id,
                "run_id": run_id,
                "attempt": attempt,
                "type": event_cls.model_fields["type"].default,
                "data": data,
            }

        def _emit(event_cls: type[RunEvent], data: Any, attempt: int = 1) -> RunEvent:
            return parse_run_event(_envelope(event_cls, data, attempt))

        user_msg_id = self._message_store.add_message(
            "user", prompt, metadata={"run_id": run_id}, session_id=session_id
        )

        loop = await get_sdk_loop(
            self._user_id, "personal", model=model, provider_keys=provider_keys, session_id=session_id
        )
        register_user_loop(self._user_id, loop, session_id=session_id)
        try:
            history = self._message_store.get_messages_with_summary(session_id, limit=50)
            messages = _storage_messages_to_sdk(list(history)) + [Message.user(prompt)]

            from src.sdk.runner import (
                AttemptItem,
                ChunkItem,
                GradeEndItem,
                GradeStartItem,
                run_with_verification_stream,
            )

            evaluations: list[RubricEvaluation] = []
            final_response = ""
            final_attempt = 1
            run_status = RunStatus.COMPLETED
            rubric_status = TerminalRubricStatus.NOT_RUN
            rubric_availability = RubricAvailability.OFF
            agent_usage = UsageAggregate()
            grader_usage = UsageAggregate()
            accumulated_args: dict[str, str] = {}
            max_attempts = 1

            try:
                async for item in run_with_verification_stream(
                    loop,
                    messages,
                    self._user_id,
                    session_id,
                    rubric=rubric,
                    model=loop.model_id,
                    is_cancelled=lambda: lock.cancelled,
                ):
                    if isinstance(item, ChunkItem):
                        chunk = item.chunk
                        if chunk.type == "done":
                            final_response = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                            final_attempt = item.attempt
                            if chunk.usage:
                                agent_usage = UsageAggregate(
                                    available=True,
                                    calls=agent_usage.calls + 1,
                                    models=(loop.model_id,),
                                    input_tokens=agent_usage.input_tokens + (chunk.usage.input_tokens or 0),
                                    output_tokens=agent_usage.output_tokens + (chunk.usage.output_tokens or 0),
                                    reasoning_tokens=agent_usage.reasoning_tokens + (chunk.usage.reasoning_tokens or 0),
                                )
                        elif chunk.type == "error":
                            run_status = RunStatus.FAILED
                            break
                        ev = _stream_chunk_to_event(chunk, _emit, item.attempt, loop.model_id, accumulated_args)
                        if ev is not None:
                            yield ev
                    elif isinstance(item, GradeStartItem):
                        rubric_availability = RubricAvailability.ON
                        max_attempts = item.max_attempts
                        yield _emit(RubricStartEvent, RubricStartData(
                            grading_run_id=str(uuid.uuid4()),
                            max_attempts=item.max_attempts,
                        ).model_dump(), item.attempt)
                    elif isinstance(item, GradeEndItem):
                        if item.evaluation is not None:
                            evaluation = _evaluation_from_dict(item.evaluation, item.attempt)
                            evaluations.append(evaluation)
                            yield _emit(RubricEndEvent, RubricEndData(
                                evaluation=evaluation,
                                max_attempts=item.max_attempts,
                            ).model_dump(), item.attempt)
                        if item.feedback is not None:
                            yield _emit(RevisionStartEvent, RevisionStartData(
                                previous_attempt=item.attempt,
                                new_attempt=item.attempt + 1,
                                max_attempts=item.max_attempts,
                            ).model_dump(), item.attempt + 1)
                    elif isinstance(item, AttemptItem):
                        final_attempt = item.attempt
                        if item.rubric_status == "failed":
                            # No assistant output — the run failed and
                            # verification never ran (OFF/NOT_RUN).
                            run_status = RunStatus.FAILED
                        elif item.rubric_status == "cancelled":
                            run_status = RunStatus.CANCELLED
                            rubric_availability = RubricAvailability.ON
                            rubric_status = TerminalRubricStatus.CANCELLED
                        elif item.rubric_available and item.rubric_status != "needs_revision":
                            # 'needs_revision' is an intermediate verdict, not
                            # a terminal rubric status — only map terminal ones.
                            rubric_availability = RubricAvailability.ON
                            rubric_status = TerminalRubricStatus(item.rubric_status)
                            if evaluations and item.grader_model_id:
                                grader_usage = UsageAggregate(
                                    available=True,
                                    calls=len(evaluations),
                                    models=(item.grader_model_id,),
                                )
            except Exception as exc:
                logger.error("run_service.agent_error", {"error": str(exc)}, user_id=self._user_id)
                run_status = RunStatus.FAILED
                # If the agent died after verification started, report the
                # rubric as CANCELLED (never satisfied).
                if getattr(loop, "_rubric_started", False):
                    rubric_availability = RubricAvailability.ON
                    rubric_status = TerminalRubricStatus.CANCELLED

            if rubric_status == TerminalRubricStatus.NOT_RUN and rubric_availability == RubricAvailability.ON:
                # The verification loop ended without a terminal verdict. Only
                # claim satisfaction when the agent run itself completed; a
                # failed run (e.g. the agent died on a later revision attempt
                # after earlier evaluations) must not report satisfied — the
                # outcome validator rejects a satisfied status whose latest
                # evaluation was needs_revision, and CANCELLED is the only
                # terminal status that tolerates the leftover evaluations.
                rubric_status = (
                    TerminalRubricStatus.SATISFIED
                    if run_status is RunStatus.COMPLETED
                    else TerminalRubricStatus.CANCELLED
                )

            # The assistant's reasoning arrived BEFORE the answer in the
            # stream — persist it first so the stored transcript order matches
            # what the client saw (otherwise the reasoning would land below
            # the answer after a history reload).
            pre_messages: list[StorageMessage] = []
            if loop.state is not None:
                for msg in reversed(loop.state.messages):
                    if msg.role == "assistant" and getattr(msg, "reasoning", None):
                        pre_messages.append(StorageMessage(
                            id="",
                            ts=datetime.now(UTC),
                            role="reasoning",
                            content=str(msg.reasoning or ""),
                            metadata={"stream": True},
                            session_id=session_id,
                        ))
                        break

            verification_meta = None
            if rubric_availability == RubricAvailability.ON and evaluations:
                verification_meta = {
                    "status": rubric_status.value,
                    "attempts": len(evaluations),
                    "max_attempts": max_attempts,
                    "evaluations": [
                        {
                            "attempt": e.attempt,
                            "result": e.result.value,
                            "explanation": e.explanation,
                            "criteria": [{"name": c.name, "passed": c.passed, "gap": c.gap} for c in e.criteria],
                        }
                        for e in evaluations
                    ],
                }

            persisted_id = self._message_store.persist_run(
                run_id=run_id,
                session_id=session_id,
                user_message_id=user_msg_id,
                final_answer=_sdk_message_to_storage(Message.assistant(content=final_response), session_id),
                audit_records=_tool_audit_records(loop, session_id),
                pre_messages=pre_messages,
                metadata={"model": loop.model_id, "verification": verification_meta},
            )

            run_result = RunResult(
                run_id=run_id,
                session_id=session_id,
                status=run_status,
                attempt=final_attempt,
                model=loop.model_id,
                response=final_response,
                final_message_id=persisted_id,
                usage=RunUsage(agent=agent_usage, grader=grader_usage),
                verification=VerificationOutcome(
                    availability=rubric_availability,
                    status=rubric_status,
                    attempts=len(evaluations),
                    max_attempts=max_attempts,
                    evaluations=tuple(evaluations),
                ),
                persisted_at=datetime.now(UTC),
            )
            # The envelope attempt must match the result's final attempt (the
            # rubric loop can revise past attempt 1); DoneEvent validation
            # rejects a mismatch.
            yield _emit(DoneEvent, DoneData(result=run_result).model_dump(), final_attempt)
        except SessionBusyError:
            yield _emit(ErrorEvent, ErrorData(
                code="session_busy",
                message="Session already has an active run",
                retryable=True,
            ).model_dump())
        except asyncio.CancelledError:
            yield _emit(ErrorEvent, ErrorData(
                code="cancelled",
                message="Run was cancelled",
                retryable=False,
            ).model_dump())
        except Exception as exc:
            logger.error("run_service.error", {"error": str(exc)}, user_id=self._user_id)
            yield _emit(ErrorEvent, ErrorData(
                code="internal_error",
                message=str(exc),
                retryable=False,
            ).model_dump())
        finally:
            unregister_user_loop(self._user_id, loop, session_id=session_id)

    async def _run_bounded_orchestration(
        self,
        loop: AgentLoop,
        messages: list[Message],
        run_id: str,
        session_id: str,
        lock: SessionLock,
        rubric: str | None = None,
    ) -> RunResult:
        from src.sdk.runner import run_with_verification

        evaluations: list[RubricEvaluation] = []
        final_response = ""
        final_attempt = 1
        run_status = RunStatus.COMPLETED
        rubric_status = TerminalRubricStatus.NOT_RUN
        rubric_availability = RubricAvailability.OFF
        agent_usage = UsageAggregate()
        grader_usage = UsageAggregate()
        result_tool_calls: list[dict[str, Any]] = []
        max_attempts = 1
        outcome_attempts = 0

        try:
            vresult = await run_with_verification(
                loop,
                messages,
                self._user_id,
                session_id,
                rubric=rubric,
                model=loop.model_id,
                is_cancelled=lambda: lock.cancelled,
            )
        except Exception as exc:
            logger.error("run_service.agent_error", {"error": str(exc)}, user_id=self._user_id)
            run_status = RunStatus.FAILED
            # If the agent died after verification started, report the rubric
            # as CANCELLED (never satisfied) so the outcome stays consistent.
            if getattr(loop, "_rubric_started", False):
                rubric_availability = RubricAvailability.ON
                rubric_status = TerminalRubricStatus.CANCELLED
                outcome_attempts = 1
            vresult = None

        if vresult is not None and vresult.attempts:
            attempts = vresult.attempts
            max_attempts = vresult.max_attempts
            # Outcome attempts only count when verification was active; an
            # OFF outcome must report zero attempts.
            if vresult.rubric_available:
                outcome_attempts = len(attempts)
            final_attempt = attempts[-1].attempt
            final_messages = attempts[-1].messages

            # Rubric outcome from the engine's verdict.
            if vresult.rubric_available:
                if vresult.rubric_status == "failed":
                    # No assistant output — the run failed and verification
                    # never ran (matches the pre-engine behavior: OFF/NOT_RUN).
                    run_status = RunStatus.FAILED
                elif vresult.rubric_status == "cancelled":
                    run_status = RunStatus.CANCELLED
                    rubric_availability = RubricAvailability.ON
                    rubric_status = TerminalRubricStatus.CANCELLED
                else:
                    rubric_availability = RubricAvailability.ON
                    rubric_status = TerminalRubricStatus(vresult.rubric_status)
                for at in attempts:
                    if at.evaluation is not None:
                        evaluations.append(_evaluation_from_dict(at.evaluation, at.attempt))
                if evaluations and vresult.grader_model_id:
                    grader_usage = UsageAggregate(
                        available=True,
                        calls=len(evaluations),
                        models=(vresult.grader_model_id,),
                    )

            # Only derive the response from a completed agent attempt.
            if run_status is RunStatus.COMPLETED:
                last_assistant = None
                for msg in reversed(final_messages):
                    if msg.role == "assistant":
                        last_assistant = msg
                        break
                if last_assistant is None:
                    run_status = RunStatus.FAILED
                else:
                    final_response = last_assistant.content if isinstance(last_assistant.content, str) else str(last_assistant.content)

                # Extract tool call info from attempt 1's result messages.
                tool_call_records: list[dict[str, Any]] = []
                for msg in attempts[0].messages:
                    if msg.role == "assistant" and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_call_records.append({
                                "name": tc.name,
                                "arguments": tc.arguments,
                                "id": tc.id,
                                "status": "called",
                            })
                    elif msg.role == "tool" and msg.tool_call_id:
                        for record in tool_call_records:
                            if record.get("id") == msg.tool_call_id:
                                record["status"] = "done"
                                record["result"] = msg.content[:500] if isinstance(msg.content, str) else str(msg.content)[:500]
                                break
                if tool_call_records:
                    result_tool_calls = tool_call_records

                # Agent usage summed across attempts.
                for at in attempts:
                    la = None
                    for msg in reversed(at.messages):
                        if msg.role == "assistant":
                            la = msg
                            break
                    if la is not None and la.usage:
                        agent_usage = UsageAggregate(
                            available=True,
                            calls=agent_usage.calls + 1,
                            models=(loop.model_id,),
                            input_tokens=agent_usage.input_tokens + (la.usage.input_tokens or 0),
                            output_tokens=agent_usage.output_tokens + (la.usage.output_tokens or 0),
                            reasoning_tokens=agent_usage.reasoning_tokens + (la.usage.reasoning_tokens or 0),
                        )

        if rubric_status == TerminalRubricStatus.NOT_RUN and rubric_availability == RubricAvailability.ON:
            # Only claim satisfaction when the agent run completed (see the
            # matching fallback in _run_stream).
            rubric_status = (
                TerminalRubricStatus.SATISFIED
                if run_status is RunStatus.COMPLETED
                else TerminalRubricStatus.CANCELLED
            )

        return RunResult(
            run_id=run_id,
            session_id=session_id,
            status=run_status,
            attempt=final_attempt,
            model=loop.model_id,
            response=final_response,
            usage=RunUsage(agent=agent_usage, grader=grader_usage),
            verification=VerificationOutcome(
                availability=rubric_availability,
                status=rubric_status,
                attempts=outcome_attempts,
                max_attempts=max_attempts,
                evaluations=tuple(evaluations),
            ),
            tool_calls=result_tool_calls,
        )

    async def _load_rubric_middleware(self, loop: AgentLoop, rubric: str | None = None) -> RubricMiddleware | None:
        """Load rubric configuration and create RubricMiddleware if enabled.

        Delegates to the shared loader in middleware_rubric (the single
        rubric-loading site used by the verification engine).
        """
        from src.sdk.middleware_rubric import load_rubric_middleware

        return await load_rubric_middleware(self._user_id, loop, rubric)
