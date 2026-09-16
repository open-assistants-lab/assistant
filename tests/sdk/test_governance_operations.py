"""Phase 1 durable governed-operation ledger tests."""

from __future__ import annotations

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


def test_cancel_requested_and_uncertain_are_durable(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service)

    assert service.operations.request_cancel("alice", operation.operation_id)
    current = service.operations.get_operation("alice", operation.operation_id)
    assert current is not None and current.cancel_requested
    assert service.operations.transition("alice", operation.operation_id, OperationStatus.UNCERTAIN)
    assert service.operations.get_operation("alice", operation.operation_id).status is OperationStatus.UNCERTAIN


def test_operations_are_isolated_by_user(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    operation = _approved_operation(service, "alice")

    assert service.operations.get_operation("bob", operation.operation_id) is None
    assert not service.operations.request_cancel("bob", operation.operation_id)
    assert service.operations.get_events("bob", operation.operation_id) == []
