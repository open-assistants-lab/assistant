import asyncio
import json
import sqlite3
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
from src.sdk.execution_store import ReceiptStateError, SQLiteReceiptStore


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
async def test_same_request_id_with_different_arguments_is_rejected(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    await store.create_or_get(make_request("req-reuse"))
    conflicting = make_request("req-reuse").model_copy(
        update={"arguments": {"command": "false"}}
    )

    with pytest.raises(ReceiptStateError):
        await store.create_or_get(conflicting)

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


@pytest.mark.asyncio
async def test_timeout_preserves_unknown_external_effect(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-timeout"))

    finished = await store.finalize(
        receipt.receipt_id,
        outcome=Outcome.TIMED_OUT,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.UNKNOWN,
        verification_state=VerificationState.UNKNOWN,
        termination_reason="deadline_exceeded",
    )

    assert finished.outcome is Outcome.TIMED_OUT
    assert finished.effect_state is EffectState.UNKNOWN
    assert finished.outcome is not Outcome.UNCERTAIN
    await store.close()


@pytest.mark.asyncio
async def test_provider_acknowledgement_and_events_survive_restart(tmp_path) -> None:
    database = tmp_path / "receipts.db"
    store = SQLiteReceiptStore(database)
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-events"))

    first_event = await store.append_event(
        receipt.receipt_id,
        "execution.started",
        {"executor": "connector"},
    )
    observation = Observation(
        observation_id="obs-1",
        kind="provider_ack",
        source="connector_action",
        authoritative=True,
        correlation_id="provider-1",
        observed_at=datetime.now(UTC),
        payload={"resource_id": "resource-1"},
    )
    observed = await store.record_observation(receipt.receipt_id, observation)
    await store.close()

    restarted = SQLiteReceiptStore(database)
    await restarted.initialize()
    restored = await restarted.get(receipt.receipt_id)
    events = await restarted.list_events(receipt.receipt_id)

    assert events == [first_event]
    assert restored is not None
    assert restored.observations == [observation]
    assert restored.observations[0].authoritative is True
    assert restored.observations == observed.observations
    await restarted.close()


@pytest.mark.asyncio
async def test_rebuild_preserves_terminal_receipt_and_observations(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-rebuild"))
    await store.append_event(receipt.receipt_id, "execution.started", {})
    await store.record_observation(
        receipt.receipt_id,
        Observation(
            observation_id="obs-rebuild",
            kind="test_report",
            source="agent_followup",
            observed_at=datetime.now(UTC),
            payload={"passed": 1},
        ),
    )
    finalized = await store.finalize(
        receipt.receipt_id,
        outcome=Outcome.SUCCEEDED,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.APPLIED,
        verification_state=VerificationState.VERIFIED,
    )

    rebuilt = await store.rebuild(receipt.receipt_id)

    assert rebuilt == finalized
    await store.close()


@pytest.mark.asyncio
async def test_duplicate_finalization_is_idempotent_but_conflicting_is_rejected(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-finalize"))
    values = {
        "outcome": Outcome.CANCELLED,
        "executor_state": ExecutorState.TERMINAL,
        "effect_state": EffectState.NOT_APPLIED,
        "verification_state": VerificationState.NOT_REQUESTED,
    }

    first = await store.finalize(receipt.receipt_id, **values)
    second = await store.finalize(receipt.receipt_id, **values)

    assert second == first
    with pytest.raises(ReceiptStateError):
        await store.finalize(
            receipt.receipt_id,
            outcome=Outcome.FAILED,
            executor_state=ExecutorState.TERMINAL,
            effect_state=EffectState.NOT_APPLIED,
            verification_state=VerificationState.NOT_REQUESTED,
        )
    await store.close()


@pytest.mark.asyncio
async def test_events_have_monotonic_sequences_per_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-sequence"))

    first = await store.append_event(receipt.receipt_id, "execution.started", {})
    with pytest.raises(ReceiptStateError):
        await store.append_event(receipt.receipt_id, "execution.started", {})
    second = await store.append_event(receipt.receipt_id, "execution.output", {"ok": True})

    assert [first.sequence, second.sequence] == [1, 2]
    await store.close()


@pytest.mark.asyncio
async def test_disconnect_can_be_uncertain_without_becoming_timeout(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-disconnect"))

    finished = await store.finalize(
        receipt.receipt_id,
        outcome=Outcome.UNCERTAIN,
        executor_state=ExecutorState.UNKNOWN,
        effect_state=EffectState.UNKNOWN,
        verification_state=VerificationState.UNKNOWN,
        termination_reason="transport_disconnected",
    )

    assert finished.outcome is Outcome.UNCERTAIN
    assert finished.outcome is not Outcome.TIMED_OUT
    await store.close()


@pytest.mark.asyncio
async def test_persisted_payloads_redact_credential_like_fields(tmp_path) -> None:
    database = tmp_path / "receipts.db"
    store = SQLiteReceiptStore(database)
    await store.initialize()
    request = make_request("req-redaction").model_copy(
        update={
            "arguments": {
                "api_key": "request-secret",
                "nested": {"password": "nested-secret"},
            }
        }
    )
    receipt = await store.create_or_get(request)
    await store.append_event(
        receipt.receipt_id,
        "provider.response",
        {"access_token": "event-secret", "safe": "value"},
    )
    await store.close()

    connection = sqlite3.connect(database)
    request_json = connection.execute(
        "SELECT request_json FROM execution_receipts WHERE receipt_id = ?",
        (receipt.receipt_id,),
    ).fetchone()[0]
    event_json = connection.execute(
        "SELECT payload_json FROM execution_events WHERE receipt_id = ?",
        (receipt.receipt_id,),
    ).fetchone()[0]
    connection.close()

    assert "request-secret" not in request_json
    assert "nested-secret" not in request_json
    assert "event-secret" not in event_json
    assert json.loads(event_json)["safe"] == "value"


@pytest.mark.asyncio
async def test_concurrent_identical_finalization_writes_one_completion(tmp_path) -> None:
    database = tmp_path / "receipts.db"
    creator = SQLiteReceiptStore(database)
    first_worker = SQLiteReceiptStore(database)
    second_worker = SQLiteReceiptStore(database)
    await creator.initialize()
    await first_worker.initialize()
    await second_worker.initialize()
    receipt = await creator.create_or_get(make_request("req-concurrent"))

    kwargs = {
        "outcome": Outcome.SUCCEEDED,
        "executor_state": ExecutorState.TERMINAL,
        "effect_state": EffectState.APPLIED,
        "verification_state": VerificationState.VERIFIED,
    }
    results = await asyncio.gather(
        first_worker.finalize(receipt.receipt_id, **kwargs),
        second_worker.finalize(receipt.receipt_id, **kwargs),
    )

    assert results[0] == results[1]
    events = await creator.list_events(receipt.receipt_id)
    assert [event.event_type for event in events] == ["execution.completed"]
    await creator.close()
    await first_worker.close()
    await second_worker.close()


@pytest.mark.asyncio
async def test_lifecycle_events_cannot_reopen_a_terminal_receipt(tmp_path) -> None:
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-terminal"))
    await store.finalize(
        receipt.receipt_id,
        outcome=Outcome.SUCCEEDED,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.APPLIED,
        verification_state=VerificationState.VERIFIED,
    )

    with pytest.raises(ReceiptStateError):
        await store.append_event(receipt.receipt_id, "execution.started", {})

    restored = await store.get(receipt.receipt_id)
    assert restored is not None
    assert restored.outcome is Outcome.SUCCEEDED
    assert restored.executor_state is ExecutorState.TERMINAL
    await store.close()
