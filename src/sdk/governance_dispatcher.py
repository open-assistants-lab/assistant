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

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        interval_seconds: float = 1.0,
        batch_size: int = 20,
        max_concurrency: int = 4,
    ) -> None:
        if batch_size < 1 or max_concurrency < 1:
            raise ValueError("batch_size and max_concurrency must be positive")
        self.worker_id = worker_id or f"operation-dispatcher-{uuid.uuid4().hex}"
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
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
        """Mark any crash-interrupted delivery outcome uncertain; never replay it."""
        for user_id in iter_governance_user_ids():
            get_governance_service(user_id).operations.reconcile_unfinished_dispatches(user_id)

    async def dispatch_once(self) -> int:
        """Send at most one bounded batch, with bounded concurrent requests."""
        candidates: list[tuple[str, str]] = []
        for user_id in iter_governance_user_ids():
            if len(candidates) >= self.batch_size:
                break
            service = get_governance_service(user_id)
            remaining = self.batch_size - len(candidates)
            candidates.extend(
                (user_id, operation_id)
                for operation_id in service.operations.queued_dispatch_ids(user_id, limit=remaining)
            )

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async with httpx.AsyncClient(timeout=5.0) as client:
            async def dispatch_one(user_id: str, operation_id: str) -> bool:
                async with semaphore:
                    service = get_governance_service(user_id)
                    try:
                        claim = service.operations.claim_dispatch(user_id, operation_id, self.worker_id)
                        envelope = service.operations.dispatch_envelope(user_id, operation_id)
                    except ValueError:
                        return False
                    try:
                        response = await client.post(
                            claim.executor_url,
                            json=envelope,
                            headers={"Idempotency-Key": claim.idempotency_key},
                        )
                    except httpx.RequestError:
                        service.operations.record_dispatch_result(
                            user_id, operation_id, self.worker_id, acknowledged=None
                        )
                        return False
                    service.operations.record_dispatch_result(
                        user_id,
                        operation_id,
                        self.worker_id,
                        acknowledged=response.is_success,
                    )
                    return response.is_success

            outcomes = await asyncio.gather(
                *(dispatch_one(user_id, operation_id) for user_id, operation_id in candidates)
            )
        return sum(outcomes)
