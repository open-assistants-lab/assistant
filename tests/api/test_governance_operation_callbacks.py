"""Executor-only callback contract for governed operations."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.http.routers import governance as router_module
from src.sdk.governance import GovernanceService
from src.sdk.governance_operations import OperationStatus
from src.sdk.tools import ExternalHTTPExecutor


@pytest.fixture(autouse=True)
def callback_secret(monkeypatch):
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "test-operation-callback-secret"),
    )


def created_external(tmp_path):
    service = GovernanceService(data_root=str(tmp_path))
    proposal_id = service.create_pending("alice", "menu_change_execute", {"store": "HQ"})
    created, _ = service.operations.approve_pending_and_create_external_operation(
        "alice", proposal_id,
        ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run", manifest_hash="m1"),
    )
    return service, created


def payload(created, **override):
    op = created.operation
    data = {
        "user_id": "alice", "proposal_id": op.proposal_id, "tool_name": op.tool_name,
        "arguments_hash": op.arguments_hash, "manifest_hash": "m1", "sequence": 1,
        "message_safe": "started", "result": {"content": "done"},
    }
    data.update(override)
    return router_module.OperationCallback(**data)


@pytest.mark.asyncio
async def test_callback_progress_and_completion_are_capability_authenticated(tmp_path, monkeypatch):
    service, created = created_external(tmp_path)
    monkeypatch.setattr(router_module, "_svc", lambda user_id: service)
    event = await router_module.append_operation_event(
        created.operation.operation_id, payload(created), created.callback_capability or ""
    )
    assert event["sequence"] == 1
    assert service.operations.get_operation("alice", created.operation.operation_id).status is OperationStatus.RUNNING
    completed = await router_module.complete_operation(
        created.operation.operation_id, payload(created), created.callback_capability or ""
    )
    assert completed["status"] == OperationStatus.SUCCEEDED.value
    assert (await router_module.complete_operation(
        created.operation.operation_id, payload(created), created.callback_capability or ""
    ))["status"] == OperationStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_callback_rejects_missing_capability_and_binding_mismatch(tmp_path, monkeypatch):
    service, created = created_external(tmp_path)
    monkeypatch.setattr(router_module, "_svc", lambda user_id: service)
    with pytest.raises(HTTPException) as missing:
        await router_module.append_operation_event(created.operation.operation_id, payload(created), "")
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as mismatch:
        await router_module.append_operation_event(
            created.operation.operation_id, payload(created, arguments_hash="wrong"), created.callback_capability or ""
        )
    assert mismatch.value.status_code == 401
