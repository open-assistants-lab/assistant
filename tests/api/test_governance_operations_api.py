"""HTTP contracts for async governed operations (Phase 2)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def gov_env(monkeypatch, tmp_path):
    import src.sdk.governance as governance
    import src.storage.paths as paths_mod
    from src.config import reload_settings

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    monkeypatch.setattr(governance, "_services", {})
    monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("GOVERNANCE_OPERATION_CALLBACK_SECRET", "test-operation-secret")
    monkeypatch.setenv("GOVERNANCE_EXTERNAL_EXECUTOR_ALLOWED_HOSTS", '["executor.internal"]')
    reload_settings()
    yield
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def client(gov_env):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_async_approval_returns_accepted_without_invoking_executor(client, monkeypatch) -> None:
    import src.http.routers.governance as governance_router
    from src.sdk.governance import get_governance_service
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    executor = ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run")
    monkeypatch.setattr(service, "_active_tool_definition", lambda *_args: object())
    monkeypatch.setattr(service, "execution_mode_for_tool", lambda _user_id, _tool_name: "async")
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "explicit")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: executor)
    # Snapshot the immutable external executor at proposal creation.
    proposal_id = service.create_pending("alice", "menu_change_execute", {"store": "HQ"}, tier="explicit")

    async def executor_must_not_run(*_args, **_kwargs):
        raise AssertionError("async approval invoked the synchronous executor")

    monkeypatch.setattr(governance_router, "execute_approved_tool", executor_must_not_run)

    response = client.post(
        f"/v1/governance/pendings/{proposal_id}/approve", params={"user_id": "alice"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["operation_status"] == "queued"
    assert payload["proposal_id"] == proposal_id
    assert payload["operation_id"]

    repeated = client.post(
        f"/v1/governance/pendings/{proposal_id}/approve", params={"user_id": "alice"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["operation_id"] == payload["operation_id"]


def test_async_approval_without_external_executor_is_rejected(client, monkeypatch) -> None:
    from src.sdk.governance import get_governance_service
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    executor = ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run")
    monkeypatch.setattr(service, "_active_tool_definition", lambda *_args: object())
    monkeypatch.setattr(service, "execution_mode_for_tool", lambda *_args: "async")
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "explicit")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: executor)
    proposal_id = service.create_pending("alice", "menu_change_execute", {}, tier="explicit")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: None)

    response = client.post(
        f"/v1/governance/pendings/{proposal_id}/approve", params={"user_id": "alice"}
    )
    assert response.status_code == 409
    assert "executor metadata changed" in response.json()["detail"]
    assert service.get_pending("alice", proposal_id)["status"] == "pending"


def test_operation_read_list_and_cancel_request_are_user_scoped(client) -> None:
    from src.sdk.governance import get_governance_service

    proposal_id = get_governance_service("alice").create_pending(
        "alice", "menu_change_execute", {}, tier="explicit"
    )
    operation, _ = get_governance_service("alice").approve_async_operation("alice", proposal_id)

    detail = client.get(
        f"/v1/governance/operations/{operation.operation_id}", params={"user_id": "alice"}
    )
    assert detail.status_code == 200
    assert detail.json()["operation_id"] == operation.operation_id
    assert detail.json()["status"] == "queued"

    listed = client.get("/v1/governance/operations", params={"user_id": "alice", "status": "queued"})
    assert listed.status_code == 200
    assert [item["operation_id"] for item in listed.json()] == [operation.operation_id]

    cancel = client.post(
        f"/v1/governance/operations/{operation.operation_id}/cancel", params={"user_id": "alice"}
    )
    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True

    other_user = client.get(
        f"/v1/governance/operations/{operation.operation_id}", params={"user_id": "bob"}
    )
    assert other_user.status_code == 404
