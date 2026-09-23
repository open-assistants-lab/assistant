"""Phase 1 durable governed-operation ledger tests."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading

from src.sdk.governance import GovernanceService
from src.sdk.governance_operations import OperationStatus


def _approved_operation(service: GovernanceService, user_id: str = "alice"):
    proposal_id = service.create_pending(user_id, "menu_change_execute", {"store": "HQ"})
    assert service.approve(user_id, proposal_id)
    return service.operations.approve_and_create_operation(user_id, proposal_id)


def test_approved_proposal_creates_one_queued_operation_atomically(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))

    operation = _approved_operation(service)
    again = service.operations.approve_and_create_operation("alice", operation.proposal_id)

    assert operation.status is OperationStatus.QUEUED
    assert again.operation_id == operation.operation_id
    assert service.get_pending("alice", operation.proposal_id)["status"] == "consumed"


def test_operation_lifecycle_events_and_terminal_idempotency(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service)

    assert service.operations.transition("alice", operation.operation_id, OperationStatus.RUNNING)
    first = service.operations.append_event("alice", operation.operation_id, "running", "Started")
    second = service.operations.append_event("alice", operation.operation_id, "running", "Published")
    assert (first.sequence, second.sequence) == (1, 2)

    completed = service.operations.complete(
        "alice", operation.operation_id, {"content": "done", "changed": 1}
    )
    repeated = service.operations.complete("alice", operation.operation_id, {"content": "other"})

    assert completed.status is OperationStatus.SUCCEEDED
    assert repeated.result == {"content": "done", "changed": 1}
    assert service.operations.get_events("alice", operation.operation_id) == [first, second]


def test_terminal_failure_is_idempotent(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service)
    assert service.operations.transition("alice", operation.operation_id, OperationStatus.RUNNING)

    failed = service.operations.finish(
        "alice", operation.operation_id, OperationStatus.FAILED, error_code="executor_failed"
    )
    repeated = service.operations.finish(
        "alice", operation.operation_id, OperationStatus.FAILED, error_code="other"
    )

    assert failed.status is OperationStatus.FAILED
    assert repeated.error_code == "executor_failed"


def test_running_cancel_remains_durable_request(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service)
    assert service.operations.transition("alice", operation.operation_id, OperationStatus.RUNNING)
    assert service.operations.request_cancel("alice", operation.operation_id)
    current = service.operations.get_operation("alice", operation.operation_id)
    assert current is not None
    assert current.status is OperationStatus.RUNNING
    assert current.cancel_requested


def test_request_cancel_is_compare_and_set(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service)

    assert service.operations.request_cancel("alice", operation.operation_id)
    first = service.operations.get_operation("alice", operation.operation_id)
    assert first is not None
    assert not service.operations.request_cancel("alice", operation.operation_id)
    second = service.operations.get_operation("alice", operation.operation_id)

    assert second is not None
    assert second.cancel_requested
    assert second.updated_at == first.updated_at


def test_independent_services_create_one_operation_under_concurrent_approval(tmp_path):
    first = GovernanceService(data_root=str(tmp_path))
    second = GovernanceService(data_root=str(tmp_path))
    proposal_id = first.create_pending("alice", "menu_change_execute", {"store": "HQ"})
    assert first.approve("alice", proposal_id)
    barrier = threading.Barrier(2)

    def create(service: GovernanceService):
        barrier.wait()
        return service.operations.approve_and_create_operation("alice", proposal_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        operations = list(pool.map(create, (first, second)))

    assert {operation.operation_id for operation in operations} == {operations[0].operation_id}
    assert first.get_pending("alice", proposal_id)["status"] == "consumed"


def test_ledger_schema_preserves_legacy_proposal_data(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    db_path = service._db_path("alice")  # noqa: SLF001 - legacy DB fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE proposals (
                proposal_id TEXT PRIMARY KEY, ts TEXT NOT NULL, tool TEXT NOT NULL,
                arguments TEXT NOT NULL, permission TEXT NOT NULL, status TEXT NOT NULL,
                expires_at TEXT, session_id TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO proposals VALUES
            ('legacy', '2026-01-01T00:00:00+00:00', 'menu_change_execute', '{}',
             'ask', 'approved', NULL, 'session-42')"""
        )

    assert service.operations.approve_and_create_operation("alice", "legacy").proposal_id == "legacy"
    legacy = service.get_pending("alice", "legacy")
    assert legacy is not None
    assert legacy["status"] == "consumed"
    assert legacy["session_id"] == "session-42"


def test_operations_are_isolated_by_user(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service, "alice")

    assert service.operations.get_operation("bob", operation.operation_id) is None
    assert not service.operations.request_cancel("bob", operation.operation_id)
    assert service.operations.get_events("bob", operation.operation_id) == []
