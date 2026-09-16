"""Phase 3a durable external governed-operation store tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.sdk.governance import GovernanceService
from src.sdk.governance_operations import GovernanceOperationStore, OperationStatus
from src.sdk.tools import ExternalHTTPExecutor


@pytest.fixture(autouse=True)
def _external_executor_config(monkeypatch):
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "test-operation-callback-secret"),
    )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._external_executor_allowed_hosts",
        staticmethod(lambda: ["executor.internal"]),
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


def test_external_executor_rejects_url_userinfo():
    with pytest.raises(ValidationError, match="userinfo"):
        ExternalHTTPExecutor(
            kind="external_http", dispatch_url="https://user:secret@executor.internal/run"
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


def test_external_approval_is_user_scoped_requires_secret_and_allowlisted_host(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending("alice", "menu_change_execute", {})
    with pytest.raises(ValueError, match="Proposal cannot"):
        service.operations.approve_pending_and_create_external_operation(
            "bob", proposal_id,
            ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal")
        )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: ""),
    )
    with pytest.raises(ValueError, match="GOVERNANCE_OPERATION_CALLBACK_SECRET"):
        service.operations.approve_pending_and_create_external_operation(
            "alice", proposal_id, ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal")
        )


def test_external_approval_rejects_unallowlisted_host_and_accepts_exact_port(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending("alice", "menu_change_execute", {})
    with pytest.raises(ValueError, match="deployment-allowlisted"):
        service.operations.approve_pending_and_create_external_operation(
            "alice", proposal_id,
            ExternalHTTPExecutor(kind="external_http", dispatch_url="https://untrusted.internal/run"),
        )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._external_executor_allowed_hosts",
        staticmethod(lambda: ["executor.internal:8443"]),
    )
    created, _ = service.operations.approve_pending_and_create_external_operation(
        "alice", proposal_id,
        ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal:8443/run"),
    )
    assert created.operation.executor_url == "https://executor.internal:8443/run"


def test_external_approval_fails_closed_without_allowlisted_hosts(tmp_path, monkeypatch):
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending("alice", "menu_change_execute", {})
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._external_executor_allowed_hosts",
        staticmethod(lambda: []),
    )
    with pytest.raises(ValueError, match="deployment-allowlisted"):
        service.operations.approve_pending_and_create_external_operation(
            "alice", proposal_id,
            ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run"),
        )


def test_queued_cancel_atomically_cancels_outbox_before_dispatch(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation

    assert service.operations.request_cancel("alice", operation.operation_id)
    current = service.operations.get_operation("alice", operation.operation_id)
    assert current is not None
    assert current.status is OperationStatus.CANCELLED
    assert current.cancel_requested
    assert service.operations.get_dispatch("alice", operation.operation_id).status == "cancelled"
    assert service.operations.queued_dispatch_ids("alice") == []
    with pytest.raises(ValueError, match="not queued"):
        service.operations.claim_dispatch("alice", operation.operation_id, "worker")
    assert service.operations.get_events("alice", operation.operation_id)[-1].kind == "cancelled"


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


def test_unknown_dispatch_result_requires_current_worker_claim(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    service.operations.claim_dispatch("alice", operation.operation_id, "worker-1")

    with pytest.raises(ValueError, match="Dispatch claim mismatch"):
        service.operations.record_dispatch_result(
            "alice", operation.operation_id, "stale-worker", acknowledged=None
        )

    assert service.operations.get_operation("alice", operation.operation_id).status is OperationStatus.RUNNING
    assert service.operations.get_dispatch("alice", operation.operation_id).status == "claimed"
    assert service.operations.get_events("alice", operation.operation_id) == []


def test_restart_derives_same_capability_and_authenticates_callback(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation

    restarted = GovernanceService(data_root=str(tmp_path))
    restored = restarted.operations.get_operation("alice", operation.operation_id)
    assert restored is not None
    capability = GovernanceOperationStore._callback_capability(
        "test-operation-callback-secret",
        restored.operation_id,
        restored.proposal_id,
        restored.arguments_hash,
    )
    assert capability == created.callback_capability

    event = restarted.operations.append_callback_event(
        "alice", restored.operation_id, capability, 1, "running", "recovered",
        proposal_id=restored.proposal_id,
        tool_name=restored.tool_name,
        arguments_hash=restored.arguments_hash,
        manifest_hash=restored.manifest_hash,
    )
    assert event.sequence == 1


def test_callback_terminal_idempotency_and_ambiguous_dispatch_is_not_retried(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    created, _ = _external(service)
    operation = created.operation
    capability = created.callback_capability or ""
    binding = dict(proposal_id=operation.proposal_id, tool_name=operation.tool_name,
                   arguments_hash=operation.arguments_hash, manifest_hash="manifest-1")
    service.operations.claim_dispatch("alice", operation.operation_id, "w1")
    service.operations.record_dispatch_result("alice", operation.operation_id, "w1", acknowledged=False)
    with pytest.raises(ValueError, match="not queued"):
        service.operations.claim_dispatch("alice", operation.operation_id, "w2")
    done = service.operations.finish_callback("alice", operation.operation_id, capability,
                                              OperationStatus.SUCCEEDED, result={"ok": True}, **binding)
    assert service.operations.finish_callback("alice", operation.operation_id, capability,
                                              OperationStatus.SUCCEEDED, result={"ok": False}, **binding).operation_id == done.operation_id

@pytest.mark.parametrize("url", ["executor.internal/run", "/relative", "ftp://executor.internal/run"])
def test_external_executor_rejects_non_absolute_http_urls(url):
    with pytest.raises(ValueError, match="absolute http"):
        ExternalHTTPExecutor(kind="external_http", dispatch_url=url)
