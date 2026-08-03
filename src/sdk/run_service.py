"""RunService — single entry point for executing a user turn.

Owns user-message persistence, history loading, settings snapshotting,
runner execution, final persistence, and terminal outcome construction.
Routers do not write conversation records directly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk
from src.sdk.middleware_rubric import RubricMiddleware
from src.sdk.run_models import (
    ContextSnapshot,
    CriterionEvaluation,
    RunResult,
    RunStatus,
    RunUsage,
    RubricAvailability,
    RubricEvaluation,
    RubricEvaluationResult,
    TerminalRubricStatus,
    UsageAggregate,
    VerificationOutcome,
)
from src.sdk.runner import get_sdk_loop, register_user_loop, unregister_user_loop
from src.sdk.session_worker import SessionBusyError, SessionLock, SessionWorkerRegistry
from src.storage.messages import MessageStore

logger = get_logger()


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
    ) -> RunResult:
        """Non-streaming execution. Returns immutable RunResult."""
        lock = await self._registry.acquire(session_id)
        try:
            return await self._run(session_id, prompt, model, provider_keys, lock)
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
    ) -> AsyncIterator[StreamChunk]:
        """Streaming execution. Yields StreamChunk events in real-time."""
        lock = await self._registry.acquire(session_id)
        try:
            async for chunk in self._run_stream(session_id, prompt, model, provider_keys, lock):
                yield chunk
        finally:
            await self._registry.release(session_id)

    async def _run(
        self,
        session_id: str,
        prompt: str,
        model: str | None,
        provider_keys: dict[str, str] | None,
        lock: SessionLock,
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
            messages = list(history) + [Message.user(prompt)]

            result = await self._run_bounded_orchestration(loop, messages, run_id, session_id, lock)

            answer_mid = self._message_store.persist_run(
                run_id=run_id,
                session_id=session_id,
                user_message_id=user_msg_id,
                final_answer=Message.assistant(content=result.response),
                audit_records=[],
                metadata={"model": result.model},
            )

            return RunResult(
                run_id=run_id,
                session_id=session_id,
                status=result.status,
                attempt=result.attempt,
                model=result.model,
                response=result.response,
                usage=result.usage,
                verification=result.verification,
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
    ) -> AsyncIterator[StreamChunk]:
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
            messages = list(history) + [Message.user(prompt)]

            rubric_mw = self._load_rubric_middleware(loop)
            rubric_enabled = rubric_mw is not None
            max_attempts = rubric_mw.max_iterations if rubric_mw else 1

            evaluations: list[RubricEvaluation] = []
            final_response = ""
            final_attempt = 1
            run_status = RunStatus.COMPLETED
            rubric_status = TerminalRubricStatus.NOT_RUN
            rubric_availability = RubricAvailability.OFF
            agent_usage = UsageAggregate()
            grader_usage = UsageAggregate()

            for attempt in range(1, max_attempts + 1):
                if lock.cancelled:
                    run_status = RunStatus.CANCELLED
                    break

                try:
                    async for chunk in loop.run_stream(messages):
                        if chunk.type == "done":
                            final_response = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                            final_attempt = attempt
                            if chunk.usage:
                                agent_usage = UsageAggregate(
                                    available=True,
                                    calls=1,
                                    models=(loop.model_id,),
                                    input_tokens=chunk.usage.input_tokens or 0,
                                    output_tokens=chunk.usage.output_tokens or 0,
                                    reasoning_tokens=chunk.usage.reasoning_tokens or 0,
                                )
                        elif chunk.type == "error":
                            run_status = RunStatus.FAILED
                            break
                        yield chunk
                except Exception as exc:
                    logger.error("run_service.agent_error", {"error": str(exc)}, user_id=self._user_id)
                    run_status = RunStatus.FAILED
                    break

                if run_status == RunStatus.FAILED:
                    break

                if not rubric_enabled:
                    break

                rubric_availability = RubricAvailability.ON
                if lock.cancelled:
                    rubric_status = TerminalRubricStatus.CANCELLED
                    break

                yield StreamChunk(type="rubric_evaluation_start", content=json.dumps({
                    "grading_run_id": str(uuid.uuid4()),
                    "max_attempts": max_attempts,
                }))

                grading_messages = list(messages) + [Message.assistant(content=final_response)]
                evaluation_result = await rubric_mw.grade(grading_messages, attempt - 1)

                grader_usage = UsageAggregate(available=True, calls=1, models=(rubric_mw._grader_provider.model_id,))

                evaluation = RubricEvaluation(
                    grading_run_id=evaluation_result["grading_run_id"],
                    attempt=attempt,
                    result=RubricEvaluationResult(evaluation_result["result"]),
                    explanation=evaluation_result["explanation"],
                    criteria=tuple(
                        CriterionEvaluation(
                            name=c["name"],
                            passed=c["passed"],
                            gap=c.get("gap"),
                        )
                        for c in evaluation_result["criteria"]
                    ),
                    passed_count=sum(1 for c in evaluation_result["criteria"] if c["passed"]),
                    total_count=len(evaluation_result["criteria"]),
                )
                evaluations.append(evaluation)

                yield StreamChunk(type="rubric_evaluation_end", content=json.dumps({
                    "grading_run_id": evaluation.grading_run_id,
                    "attempt": attempt,
                    "result": evaluation.result.value,
                    "explanation": evaluation.explanation,
                    "criteria": [c.model_dump() for c in evaluation.criteria],
                    "max_attempts": max_attempts,
                }))

                if evaluation.result in (
                    RubricEvaluationResult.SATISFIED,
                    RubricEvaluationResult.INVALID_RUBRIC,
                    RubricEvaluationResult.GRADER_ERROR,
                ):
                    rubric_status = {
                        RubricEvaluationResult.SATISFIED: TerminalRubricStatus.SATISFIED,
                        RubricEvaluationResult.INVALID_RUBRIC: TerminalRubricStatus.INVALID_RUBRIC,
                        RubricEvaluationResult.GRADER_ERROR: TerminalRubricStatus.GRADER_ERROR,
                    }[evaluation.result]
                    break

                if attempt == max_attempts:
                    rubric_status = TerminalRubricStatus.MAX_ATTEMPTS_REACHED
                    break

                yield StreamChunk(type="response_revision_start", content=json.dumps({
                    "previous_attempt": attempt,
                    "new_attempt": attempt + 1,
                    "max_attempts": max_attempts,
                }))

                feedback = _revision_prompt(evaluation_result)
                messages = list(messages) + [Message.user(content=feedback, source="rubric_middleware")]

            if rubric_status == TerminalRubricStatus.NOT_RUN and rubric_availability == RubricAvailability.ON:
                rubric_status = TerminalRubricStatus.SATISFIED

            answer_mid = self._message_store.persist_run(
                run_id=run_id,
                session_id=session_id,
                user_message_id=user_msg_id,
                final_answer=Message.assistant(content=final_response),
                audit_records=[],
                metadata={"model": loop.model_id},
            )

            run_result = RunResult(
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
                    attempts=len(evaluations),
                    max_attempts=max_attempts,
                    evaluations=tuple(evaluations),
                ),
                persisted_at=datetime.now(UTC),
            )
            yield StreamChunk(type="done", content=json.dumps({"result": run_result.model_dump(mode="json")}))
        except SessionBusyError:
            yield StreamChunk(type="error", content=json.dumps({
                "code": "session_busy",
                "message": "Session already has an active run",
                "retryable": True,
            }))
        except asyncio.CancelledError:
            yield StreamChunk(type="error", content=json.dumps({
                "code": "cancelled",
                "message": "Run was cancelled",
                "retryable": False,
            }))
        except Exception as exc:
            logger.error("run_service.error", {"error": str(exc)}, user_id=self._user_id)
            yield StreamChunk(type="error", content=json.dumps({
                "code": "internal_error",
                "message": str(exc),
                "retryable": False,
            }))
        finally:
            unregister_user_loop(self._user_id, loop, session_id=session_id)

    async def _run_bounded_orchestration(
        self,
        loop: AgentLoop,
        messages: list[Message],
        run_id: str,
        session_id: str,
        lock: SessionLock,
    ) -> RunResult:
        rubric_mw = self._load_rubric_middleware(loop)
        rubric_enabled = rubric_mw is not None
        max_attempts = rubric_mw.max_iterations if rubric_mw else 1

        evaluations: list[RubricEvaluation] = []
        final_response = ""
        final_attempt = 1
        run_status = RunStatus.COMPLETED
        rubric_status = TerminalRubricStatus.NOT_RUN
        rubric_availability = RubricAvailability.OFF
        agent_usage = UsageAggregate()
        grader_usage = UsageAggregate()

        for attempt in range(1, max_attempts + 1):
            if lock.cancelled:
                run_status = RunStatus.CANCELLED
                break

            try:
                result_messages = await loop.run(messages)
            except Exception as exc:
                logger.error("run_service.agent_error", {"error": str(exc)}, user_id=self._user_id)
                run_status = RunStatus.FAILED
                break

            last_assistant = None
            for msg in reversed(result_messages):
                if msg.role == "assistant":
                    last_assistant = msg
                    break
            if last_assistant is None:
                run_status = RunStatus.FAILED
                break

            final_response = last_assistant.content if isinstance(last_assistant.content, str) else str(last_assistant.content)
            final_attempt = attempt

            if last_assistant.usage:
                agent_usage = UsageAggregate(
                    available=True,
                    calls=1,
                    models=(loop.model_id,),
                    input_tokens=last_assistant.usage.input_tokens or 0,
                    output_tokens=last_assistant.usage.output_tokens or 0,
                    reasoning_tokens=last_assistant.usage.reasoning_tokens or 0,
                )

            if not rubric_enabled:
                break

            rubric_availability = RubricAvailability.ON
            if lock.cancelled:
                rubric_status = TerminalRubricStatus.CANCELLED
                break

            grading_messages = list(messages) + [last_assistant]
            evaluation_result = await rubric_mw.grade(grading_messages, attempt - 1)

            grader_usage = UsageAggregate(available=True, calls=1, models=(rubric_mw._grader_provider.model_id,))

            evaluation = RubricEvaluation(
                grading_run_id=evaluation_result["grading_run_id"],
                attempt=attempt,
                result=RubricEvaluationResult(evaluation_result["result"]),
                explanation=evaluation_result["explanation"],
                criteria=tuple(
                    CriterionEvaluation(
                        name=c["name"],
                        passed=c["passed"],
                        gap=c.get("gap"),
                    )
                    for c in evaluation_result["criteria"]
                ),
                passed_count=sum(1 for c in evaluation_result["criteria"] if c["passed"]),
                total_count=len(evaluation_result["criteria"]),
            )
            evaluations.append(evaluation)

            if evaluation.result in (
                RubricEvaluationResult.SATISFIED,
                RubricEvaluationResult.INVALID_RUBRIC,
                RubricEvaluationResult.GRADER_ERROR,
            ):
                rubric_status = {
                    RubricEvaluationResult.SATISFIED: TerminalRubricStatus.SATISFIED,
                    RubricEvaluationResult.INVALID_RUBRIC: TerminalRubricStatus.INVALID_RUBRIC,
                    RubricEvaluationResult.GRADER_ERROR: TerminalRubricStatus.GRADER_ERROR,
                }[evaluation.result]
                break

            if attempt == max_attempts:
                rubric_status = TerminalRubricStatus.MAX_ATTEMPTS_REACHED
                break

            feedback = _revision_prompt(evaluation_result)
            messages = list(messages) + [Message.user(content=feedback, source="rubric_middleware")]

        if rubric_status == TerminalRubricStatus.NOT_RUN and rubric_availability == RubricAvailability.ON:
            rubric_status = TerminalRubricStatus.SATISFIED

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
                attempts=len(evaluations),
                max_attempts=max_attempts,
                evaluations=tuple(evaluations),
            ),
        )

    def _load_rubric_middleware(self, loop: AgentLoop) -> RubricMiddleware | None:
        """Load rubric configuration and create RubricMiddleware if enabled."""
        try:
            from src.config import get_settings
            from src.sdk.providers.factory import create_model_from_config

            settings = get_settings()
            vc = settings.verification
            if vc.enabled is not True:
                return None

            grader_model = vc.grader_model or loop.model_id
            grader_provider = create_model_from_config(grader_model, user_id=self._user_id)

            grader_prompt: str | None = None
            try:
                from src.config.user_settings_store import UserSettingsStore
                store = UserSettingsStore(self._user_id)
                grader_prompt = store.load_grader_prompt()
            except Exception:
                grader_prompt = vc.default_rubric or None

            if not grader_prompt:
                logger.warning("rubric.no_prompt", {}, user_id=self._user_id)
                return None

            return RubricMiddleware(
                grader_provider=grader_provider,
                grader_prompt=grader_prompt,
                max_iterations=vc.max_iterations,
            )
        except Exception as exc:
            logger.warning("rubric.load_failed", {"error": str(exc)}, user_id=self._user_id)
            return None


def _revision_prompt(evaluation: dict[str, Any]) -> str:
    from src.sdk.middleware_rubric import _revision_prompt as _rp
    return _rp(evaluation)
