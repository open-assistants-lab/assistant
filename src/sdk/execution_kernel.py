"""Execution orchestration over the shared lifecycle contract."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable

from src.sdk.execution_models import (
    EffectState,
    ExecutionCompletion,
    ExecutionRequest,
    ExecutorState,
    Outcome,
    Receipt,
    VerificationState,
)
from src.sdk.execution_store import ReceiptStateError, ReceiptStore

ExecutionExecutor = Callable[[ExecutionRequest], Awaitable[ExecutionCompletion]]


class ExecutionKernel:
    """Run one execution through the shared lifecycle and receipt contract."""

    def __init__(self, store: ReceiptStore) -> None:
        self._store = store

    async def run(
        self,
        request: ExecutionRequest,
        executor: ExecutionExecutor,
        *,
        timeout_seconds: float | None = None,
    ) -> Receipt:
        receipt = await self._store.create_or_get(request)
        if receipt.outcome is not None or receipt.executor_state is ExecutorState.RUNNING:
            return receipt

        try:
            await self._store.append_event(receipt.receipt_id, "execution.started", {})
        except ReceiptStateError:
            existing = await self._store.get(receipt.receipt_id)
            if existing is None:
                raise RuntimeError("receipt disappeared while starting execution")
            return existing

        try:
            if timeout_seconds is None:
                completion = await executor(request)
            else:
                completion = await asyncio.wait_for(executor(request), timeout_seconds)
        except (TimeoutError, subprocess.TimeoutExpired):
            effect_state, verification_state = _interrupted_states(request)
            return await self._store.finalize(
                receipt.receipt_id,
                outcome=Outcome.TIMED_OUT,
                executor_state=ExecutorState.TERMINAL,
                effect_state=effect_state,
                verification_state=verification_state,
                termination_reason="deadline_exceeded",
            )
        except asyncio.CancelledError:
            effect_state, verification_state = _interrupted_states(request)
            await self._store.finalize(
                receipt.receipt_id,
                outcome=Outcome.CANCELLED,
                executor_state=ExecutorState.TERMINAL,
                effect_state=effect_state,
                verification_state=verification_state,
                termination_reason="cancelled",
            )
            raise
        except Exception as exc:
            return await self._store.finalize(
                receipt.receipt_id,
                outcome=Outcome.FAILED,
                executor_state=ExecutorState.TERMINAL,
                effect_state=EffectState.UNKNOWN,
                verification_state=VerificationState.UNKNOWN,
                termination_reason="executor_error",
                content={"error_type": type(exc).__name__},
            )

        for observation in completion.observations:
            await self._store.record_observation(receipt.receipt_id, observation)
        return await self._store.finalize(
            receipt.receipt_id,
            outcome=completion.outcome,
            executor_state=ExecutorState.TERMINAL,
            effect_state=completion.effect_state,
            verification_state=completion.verification_state,
            content=completion.content,
        )


def _interrupted_states(request: ExecutionRequest) -> tuple[EffectState, VerificationState]:
    if request.expected_effect is EffectState.NOT_APPLICABLE:
        return EffectState.NOT_APPLICABLE, VerificationState.NOT_REQUESTED
    return EffectState.UNKNOWN, VerificationState.UNKNOWN
