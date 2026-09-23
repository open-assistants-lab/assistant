"""Optional PostgreSQL receipt-store contract test.

Set POSTGRES_TEST_DSN to run this against a disposable PostgreSQL database.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from src.sdk.execution_models import (
    EffectState,
    ExecutionRequest,
    ExecutorState,
    Observation,
    Outcome,
    VerificationState,
)
from src.sdk.postgres_receipt_store import PostgresReceiptStore


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_DSN"), reason="POSTGRES_TEST_DSN not configured")
async def test_postgres_receipt_store_lifecycle() -> None:
    store = PostgresReceiptStore(os.environ["POSTGRES_TEST_DSN"])
    try:
        await store.initialize()
        request = ExecutionRequest(
            request_id=f"postgres-test-{uuid.uuid4().hex}",
            run_id="postgres-run",
            tool_call_id="postgres-call",
            tool_name="connector_gmail_send",
            arguments={"to": "person@example.com"},
            created_at=datetime.now(UTC),
        )
        receipt = await store.create_or_get(request)
        await store.append_event(receipt.receipt_id, "execution.started", {})
        await store.record_observation(
            receipt.receipt_id,
            Observation(
                observation_id=uuid.uuid4().hex,
                kind="provider_ack",
                source="gmail",
                authoritative=True,
                observed_at=datetime.now(UTC),
                payload={"message_id": "pg-message-1"},
            ),
        )
        finished = await store.finalize(
            receipt.receipt_id,
            outcome=Outcome.SUCCEEDED,
            executor_state=ExecutorState.TERMINAL,
            effect_state=EffectState.APPLIED,
            verification_state=VerificationState.VERIFIED,
            content={"provider_message_id": "pg-message-1"},
        )
        rebuilt = await store.rebuild(receipt.receipt_id)

        assert finished.outcome is Outcome.SUCCEEDED
        assert rebuilt is not None
        assert rebuilt.content == {"provider_message_id": "pg-message-1"}
        assert rebuilt.observations[0].payload == {"message_id": "pg-message-1"}
    finally:
        await store.close()
