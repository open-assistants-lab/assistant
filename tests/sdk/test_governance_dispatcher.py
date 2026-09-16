"""Phase 3b durable external-dispatch tests."""

from __future__ import annotations

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.governance_dispatcher import GovernedOperationDispatcher
from src.sdk.governance_operations import OperationStatus
from src.sdk.tools import ExternalHTTPExecutor


@pytest.fixture(autouse=True)
def callback_secret(monkeypatch):
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "test-operation-callback-secret"),
    )


def external_operation(service: GovernanceService):
    proposal_id = service.create_pending("alice", "menu_change_execute", {"store": "HQ"})
    created, _ = service.operations.approve_pending_and_create_external_operation(
        "alice", proposal_id,
        ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run", manifest_hash="m1"),
    )
    return created


@pytest.mark.asyncio
async def test_dispatch_posts_one_immutable_envelope_and_acknowledges(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    created = external_operation(service)
    sent: list[tuple[str, dict, dict]] = []

    class Response:
        is_success = True

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, headers):
            sent.append((url, json, headers))
            return Response()

    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda user_id: service)
    monkeypatch.setattr("src.sdk.governance_dispatcher.httpx.AsyncClient", Client)
    dispatcher = GovernedOperationDispatcher(worker_id="test")
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    assert len(sent) == 1
    _, envelope, headers = sent[0]
    assert envelope["operation_id"] == created.operation.operation_id
    assert envelope["proposal_id"] == created.operation.proposal_id
    assert envelope["arguments"] == {"store": "HQ"}
    assert envelope["callback_token"] == created.callback_capability
    assert headers["Idempotency-Key"] == created.operation.dispatch_idempotency_key
    assert service.operations.get_dispatch("alice", created.operation.operation_id).status == "dispatched"


@pytest.mark.asyncio
async def test_startup_reconciliation_marks_dispatched_unfinished_operation_uncertain(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    created = external_operation(service)
    service.operations.claim_dispatch("alice", created.operation.operation_id, "worker")
    service.operations.record_dispatch_result("alice", created.operation.operation_id, "worker", acknowledged=True)
    assert service.operations.get_dispatch("alice", created.operation.operation_id).status == "dispatched"
    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda user_id: service)
    await GovernedOperationDispatcher().reconcile_startup()
    operation = service.operations.get_operation("alice", created.operation.operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.UNCERTAIN
    assert service.operations.get_events("alice", created.operation.operation_id)[-1].kind == "uncertain"


@pytest.mark.asyncio
async def test_dispatch_once_bounds_concurrency_and_batch(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    for _ in range(4):
        external_operation(service)
    active = 0
    peak = 0

    class Response:
        is_success = True

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *_args, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await __import__("asyncio").sleep(0.01)
            active -= 1
            return Response()

    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda user_id: service)
    monkeypatch.setattr("src.sdk.governance_dispatcher.httpx.AsyncClient", Client)
    dispatcher = GovernedOperationDispatcher(worker_id="test", batch_size=3, max_concurrency=2)
    assert await dispatcher.dispatch_once() == 3
    assert peak <= 2


@pytest.mark.asyncio
async def test_startup_reconciliation_marks_claimed_dispatch_uncertain(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    created = external_operation(service)
    service.operations.claim_dispatch("alice", created.operation.operation_id, "dead-worker")
    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda user_id: service)
    await GovernedOperationDispatcher().reconcile_startup()
    operation = service.operations.get_operation("alice", created.operation.operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.UNCERTAIN
