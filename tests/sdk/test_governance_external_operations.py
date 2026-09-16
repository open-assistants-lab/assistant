"""Phase 3a durable external governed-operation store tests."""

from __future__ import annotations

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.governance_operations import OperationStatus
from src.sdk.tools import ExternalHTTPExecutor


@pytest.fixture(autouse=True)
def _callback_secret(monkeypatch):
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "test-operation-callback-secret"),
    )


def _external(service: GovernanceService, user_id: str = "alice"):
    proposal_id = service.create_pending(user_id, "menu_change_execute", {"store": "HQ"})
    return service.operations.approve_pending_and_create_external_operation(
        user_id,
        proposal_id,
        ExternalHTTPExecutor(
            kind="external_http",
            dispatch_url="https://executor.internal/operations",
            manifest_hash="manifest-1",
        ),
    )


def test_external_binding_outbox_and_capability_are_created_once(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, accepted = _external(service)
    assert accepted is True
    assert created.callback_capability
    assert created.operation.executor_url == "https://executor.internal/operations"
    assert created.operation.manifest_hash == "manifest-1"
    assert created.operation.dispatch_idempotency_key
    assert (
        service.operations.get_dispatch("alice", created.operation.operation_id).status == "queued"
    )

    repeated, accepted_again = service.operations.approve_pending_and_create_external_operation(
        "alice",
        created.operation.proposal_id,
        ExternalHTTPExecutor(
            kind="external_http", dispatch_url="https://executor.internal/operations"
        ),
    )
    assert accepted_again is False
    assert repeated.operation.operation_id == created.operation.operation_id
    assert repeated.callback_capability is None


def test_external_approval_is_user_scoped_and_requires_secret(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending("alice", "menu_change_execute", {})
    with pytest.raises(ValueError, match="Proposal cannot"):
        service.operations.approve_pending_and_create_external_operation(
            "bob", proposal_id, ExternalHTTPExecutor(kind="external_http", dispatch_url="https://x")
        )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: ""),
    )
    with pytest.raises(ValueError, match="GOVERNANCE_OPERATION_CALLBACK_SECRET"):
        service.operations.approve_pending_and_create_external_operation(
            "alice", proposal_id, ExternalHTTPExecutor(kind="external_http", dispatch_url="https://x")
        )


def test_callback_rejects_cross_user_capability_and_binding_mismatch(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    with pytest.raises(PermissionError):
        service.operations.append_callback_event(
            "bob", operation.operation_id, created.callback_capability or "", 1, "running", "no",
            proposal_id=operation.proposal_id, tool_name=operation.tool_name,
            arguments_hash=operation.arguments_hash, manifest_hash="manifest-1",
        )
    with pytest.raises(PermissionError):
        service.operations.append_callback_event(
            "alice", operation.operation_id, created.callback_capability or "", 1, "running", "no",
            proposal_id=operation.proposal_id, tool_name=operation.tool_name,
            arguments_hash="wrong", manifest_hash="manifest-1",
        )


def test_callback_sequence_is_atomic_and_duplicate_returns_existing_event(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation, capability = created.operation, created.callback_capability or ""
    binding = dict(proposal_id=operation.proposal_id, tool_name=operation.tool_name,
                   arguments_hash=operation.arguments_hash, manifest_hash="manifest-1")
    first = service.operations.append_callback_event(
        "alice", operation.operation_id, capability, 1, "running", "started", **binding
    )
    duplicate = service.operations.append_callback_event(
        "alice", operation.operation_id, capability, 1, "running", "ignored", **binding
    )
    assert duplicate == first
    with pytest.raises(ValueError):
        service.operations.append_callback_event(
            "alice", operation.operation_id, capability, 3, "running", "skipped", **binding
        )


def test_capability_plaintext_is_not_persisted_and_uncertain_has_evidence(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    with service._conn("alice") as conn:  # noqa: SLF001 - durable-storage proof
        dumped = " ".join(str(value) for row in conn.execute("SELECT * FROM operation_dispatches") for value in row)
    assert (created.callback_capability or "") not in dumped
    service.operations.claim_dispatch("alice", created.operation.operation_id, "worker-1")
    uncertain = service.operations.record_dispatch_result(
        "alice", created.operation.operation_id, "worker-1", acknowledged=None
    )
    assert uncertain.status is OperationStatus.UNCERTAIN
    assert uncertain.error_code == "dispatch_outcome_unknown"
    assert service.operations.get_events("alice", uncertain.operation_id)[-1].kind == "uncertain"


def test_callback_terminal_idempotency_and_dispatch_retry_key(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    capability = created.callback_capability or ""
    binding = dict(proposal_id=operation.proposal_id, tool_name=operation.tool_name,
                   arguments_hash=operation.arguments_hash, manifest_hash="manifest-1")
    first = service.operations.claim_dispatch("alice", operation.operation_id, "w1")
    service.operations.record_dispatch_result("alice", operation.operation_id, "w1", acknowledged=False)
    second = service.operations.claim_dispatch("alice", operation.operation_id, "w2")
    assert second.idempotency_key == first.idempotency_key
    done = service.operations.finish_callback("alice", operation.operation_id, capability,
                                              OperationStatus.SUCCEEDED, result={"ok": True}, **binding)
    assert service.operations.finish_callback("alice", operation.operation_id, capability,
                                              OperationStatus.SUCCEEDED, result={"ok": False}, **binding).operation_id == done.operation_id
