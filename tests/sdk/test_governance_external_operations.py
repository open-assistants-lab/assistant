"""Phase 3a durable external governed-operation store tests."""

from __future__ import annotations

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.governance_operations import OperationStatus
from src.sdk.tools import ExternalHTTPExecutor


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


def test_callback_rejects_cross_user_capability_and_binding_mismatch(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    with pytest.raises(PermissionError):
        service.operations.append_callback_event(
            "bob",
            operation.operation_id,
            created.callback_capability or "",
            1,
            "running",
            "no",
            proposal_id=operation.proposal_id,
            tool_name=operation.tool_name,
            arguments_hash=operation.arguments_hash,
            manifest_hash="manifest-1",
        )
    with pytest.raises(PermissionError):
        service.operations.append_callback_event(
            "alice",
            operation.operation_id,
            created.callback_capability or "",
            1,
            "running",
            "no",
            proposal_id=operation.proposal_id,
            tool_name=operation.tool_name,
            arguments_hash="wrong",
            manifest_hash="manifest-1",
        )


def test_callback_sequence_terminal_idempotency_and_uncertain_reconciliation(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    capability = created.callback_capability or ""
    binding = dict(
        proposal_id=operation.proposal_id,
        tool_name=operation.tool_name,
        arguments_hash=operation.arguments_hash,
        manifest_hash="manifest-1",
    )
    service.operations.claim_dispatch("alice", operation.operation_id, "worker-1")
    service.operations.record_dispatch_result(
        "alice", operation.operation_id, "worker-1", acknowledged=True
    )
    first = service.operations.append_callback_event(
        "alice", operation.operation_id, capability, 1, "running", "started", **binding
    )
    assert first.sequence == 1
    with pytest.raises(ValueError):
        service.operations.append_callback_event(
            "alice", operation.operation_id, capability, 3, "running", "skipped", **binding
        )
    done = service.operations.finish_callback(
        "alice",
        operation.operation_id,
        capability,
        OperationStatus.SUCCEEDED,
        result={"ok": True},
        **binding,
    )
    repeated = service.operations.finish_callback(
        "alice",
        operation.operation_id,
        capability,
        OperationStatus.SUCCEEDED,
        result={"ok": False},
        **binding,
    )
    assert repeated.operation_id == done.operation_id

    second, _ = _external(service)
    service.operations.claim_dispatch("alice", second.operation.operation_id, "worker-1")
    uncertain = service.operations.record_dispatch_result(
        "alice", second.operation.operation_id, "worker-1", acknowledged=None
    )
    assert uncertain.status is OperationStatus.UNCERTAIN


def test_dispatch_retry_reuses_stable_key_and_legacy_db_migrates(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    first = service.operations.claim_dispatch("alice", operation.operation_id, "w1")
    assert first.idempotency_key == operation.dispatch_idempotency_key
    service.operations.record_dispatch_result(
        "alice", operation.operation_id, "w1", acknowledged=False
    )
    second = service.operations.claim_dispatch("alice", operation.operation_id, "w2")
    assert second.idempotency_key == first.idempotency_key

    legacy = GovernanceService(data_root=str(tmp_path / "legacy"))
    with legacy._conn("alice") as conn:  # noqa: SLF001 - migration fixture
        conn.execute(
            "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, arguments_hash TEXT NOT NULL, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, updated_at TEXT NOT NULL, completed_at TEXT, result_json TEXT, error_code TEXT, error_detail_safe TEXT)"
        )
        conn.commit()
    proposal = legacy.create_pending("alice", "menu_change_execute", {})
    migrated, _ = legacy.operations.approve_pending_and_create_external_operation(
        "alice",
        proposal,
        ExternalHTTPExecutor(
            kind="external_http", dispatch_url="https://executor.internal/operations"
        ),
    )
    assert migrated.operation.executor_url
