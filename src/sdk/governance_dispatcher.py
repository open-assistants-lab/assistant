"""Bounded dispatcher for durable external governed operations."""

from __future__ import annotations

import asyncio
import uuid

import httpx

from src.sdk.governance import get_governance_service, iter_governance_user_ids


class GovernedOperationDispatcher:
    """Deliver queued external-operation envelopes without owning execution.

    A transport retry keeps the same durable idempotency key. Unknown delivery
    outcomes become ``uncertain`` rather than replaying a side effect.
    """

    def __init__(self, *, worker_id: str | None = None, interval_seconds: float = 1.0) -> None:
        self.worker_id = worker_id or f"operation-dispatcher-{uuid.uuid4().hex}"
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        await self.reconcile_startup()
        self._task = asyncio.create_task(self._run(), name="governed-operation-dispatcher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self.dispatch_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def reconcile_startup(self) -> None:
        """Never replay a crash-interrupted dispatch with an unknown outcome."""
        for user_id in iter_governance_user_ids():
            get_governance_service(user_id).operations.reconcile_claimed_dispatches(user_id)

    async def dispatch_once(self) -> int:
        dispatched = 0
        for user_id in iter_governance_user_ids():
            service = get_governance_service(user_id)
            for operation_id in service.operations.queued_dispatch_ids(user_id):
                try:
                    claim = service.operations.claim_dispatch(user_id, operation_id, self.worker_id)
                except ValueError:
                    continue
                envelope = service.operations.dispatch_envelope(user_id, operation_id)
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        response = await client.post(
                            claim.executor_url,
                            json=envelope,
                            headers={"Idempotency-Key": claim.idempotency_key},
                        )
                except httpx.RequestError:
                    service.operations.record_dispatch_result(
                        user_id, operation_id, self.worker_id, acknowledged=None
                    )
                    continue
                service.operations.record_dispatch_result(
                    user_id,
                    operation_id,
                    self.worker_id,
                    acknowledged=response.is_success,
                )
                if response.is_success:
                    dispatched += 1
        return dispatched
