import asyncio
import subprocess
from datetime import UTC, datetime

import pytest

from src.sdk.execution_kernel import ExecutionKernel
from src.sdk.execution_models import (
    EffectState,
    ExecutionCompletion,
    ExecutionRequest,
    ExecutorState,
    Observation,
    Outcome,
    VerificationState,
)
from src.sdk.execution_store import SQLiteReceiptStore


def make_request(request_id: str, expected_effect: EffectState = EffectState.NOT_APPLICABLE) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id,
        tool_name="shell_execute",
        profile="build",
        expected_effect=expected_effect,
        arguments={"command": "true"},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_successful_executor_produces_terminal_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)
    calls = 0

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        nonlocal calls
        calls += 1
        return ExecutionCompletion(
            content={"output": "ok"},
            observations=[
                Observation(
                    observation_id="obs-success",
                    kind="process_exit",
                    source="shell_execute",
                    observed_at=datetime.now(UTC),
                    payload={"status": 0},
                )
            ],
        )

    receipt = await kernel.run(make_request("req-success"), executor)
    repeated = await kernel.run(make_request("req-success"), executor)

    assert calls == 1
    assert receipt.outcome is Outcome.SUCCEEDED
    assert receipt.executor_state is ExecutorState.TERMINAL
    assert receipt.effect_state is EffectState.NOT_APPLICABLE
    assert receipt.verification_state is VerificationState.NOT_REQUESTED
    assert receipt.content == {"output": "ok"}
    assert repeated == receipt
    await store.close()


@pytest.mark.asyncio
async def test_timeout_preserves_external_effect_uncertainty(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        await asyncio.sleep(1)
        return ExecutionCompletion()

    receipt = await kernel.run(
        make_request("req-external-timeout", EffectState.APPLIED),
        executor,
        timeout_seconds=0.01,
    )

    assert receipt.outcome is Outcome.TIMED_OUT
    assert receipt.executor_state is ExecutorState.TERMINAL
    assert receipt.effect_state is EffectState.UNKNOWN
    assert receipt.verification_state is VerificationState.UNKNOWN
    await store.close()


@pytest.mark.asyncio
async def test_local_timeout_has_no_effect_to_verify(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        await asyncio.sleep(1)
        return ExecutionCompletion()

    receipt = await kernel.run(
        make_request("req-local-timeout"),
        executor,
        timeout_seconds=0.01,
    )

    assert receipt.outcome is Outcome.TIMED_OUT
    assert receipt.effect_state is EffectState.NOT_APPLICABLE
    assert receipt.verification_state is VerificationState.NOT_REQUESTED
    await store.close()


@pytest.mark.asyncio
async def test_subprocess_timeout_produces_timed_out_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        raise subprocess.TimeoutExpired("echo hi", 1)

    receipt = await kernel.run(make_request("req-subprocess-timeout"), executor)

    assert receipt.outcome is Outcome.TIMED_OUT
    assert receipt.termination_reason == "deadline_exceeded"
    await store.close()


@pytest.mark.asyncio
async def test_executor_error_produces_failed_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        raise RuntimeError("executor failed")

    receipt = await kernel.run(make_request("req-failed"), executor)

    assert receipt.outcome is Outcome.FAILED
    assert receipt.executor_state is ExecutorState.TERMINAL
    assert receipt.effect_state is EffectState.UNKNOWN
    assert receipt.verification_state is VerificationState.UNKNOWN
    assert receipt.termination_reason == "executor_error"
    await store.close()


@pytest.mark.asyncio
async def test_cancellation_persists_cancelled_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    kernel = ExecutionKernel(store)
    started = asyncio.Event()

    async def executor(request: ExecutionRequest) -> ExecutionCompletion:
        started.set()
        await asyncio.Event().wait()
        return ExecutionCompletion()

    task = asyncio.create_task(kernel.run(make_request("req-cancel"), executor))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    receipt = await store.get_by_request("req-cancel")
    assert receipt is not None
    assert receipt.outcome is Outcome.CANCELLED
    assert receipt.executor_state is ExecutorState.TERMINAL
    assert receipt.effect_state is EffectState.NOT_APPLICABLE
    assert receipt.verification_state is VerificationState.NOT_REQUESTED
    await store.close()
