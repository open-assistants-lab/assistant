from datetime import UTC, datetime

import pytest

from src.sdk.execution_models import ExecutionRequest
from src.sdk.execution_store import SQLiteReceiptStore


def make_request(request_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id,
        run_id="run-1",
        tool_call_id="call-1",
        tool_name="shell_execute",
        profile="build",
        arguments={"command": "true"},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_or_get_is_idempotent(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    request = make_request("req-1")

    first = await store.create_or_get(request)
    second = await store.create_or_get(request)

    assert second.receipt_id == first.receipt_id
    assert await store.get_by_request("req-1") == first
    assert await store.list_events(first.receipt_id) == []
    await store.close()


@pytest.mark.asyncio
async def test_receipt_survives_store_restart(tmp_path) -> None:
    database = tmp_path / "receipts.db"
    first_store = SQLiteReceiptStore(database)
    await first_store.initialize()
    created = await first_store.create_or_get(make_request("req-restart"))
    await first_store.close()

    second_store = SQLiteReceiptStore(database)
    await second_store.initialize()
    restored = await second_store.get(created.receipt_id)

    assert restored == created
    await second_store.close()
