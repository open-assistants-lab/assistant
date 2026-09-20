"""HTTP contracts for async governed operations (Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    from src.sdk.governance_operations import GovernanceOperationStore
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    monkeypatch.setattr(governance_router, "_svc", lambda _user_id: service)
    monkeypatch.setattr(
        GovernanceOperationStore,
        "_external_executor_allowed_hosts",
        staticmethod(lambda: ["executor.internal"]),
    )
    monkeypatch.setattr(
        GovernanceOperationStore,
        "_callback_secret",
        staticmethod(lambda: "test-operation-secret"),
    )
    executor = ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run")
    monkeypatch.setattr(service, "_active_tool_definition", lambda *_args: object())
    monkeypatch.setattr(service, "execution_mode_for_tool", lambda _user_id, _tool_name: "async")
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "explicit")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: executor)
    # Snapshot the immutable external executor at proposal creation.
    proposal_id = service.create_pending(
        "alice", "menu_change_execute", {"store": "HQ"}, tier="explicit", executor=executor
    )

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
    import src.http.routers.governance as governance_router
    from src.sdk.governance import get_governance_service
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    monkeypatch.setattr(governance_router, "_svc", lambda _user_id: service)
    executor = ExternalHTTPExecutor(kind="external_http", dispatch_url="https://executor.internal/run")
    monkeypatch.setattr(service, "_active_tool_definition", lambda *_args: object())
    monkeypatch.setattr(service, "execution_mode_for_tool", lambda *_args: "async")
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "explicit")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: executor)
    proposal_id = service.create_pending(
        "alice", "menu_change_execute", {}, tier="explicit", executor=executor
    )
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


def test_pendings_scan_dispatches_async_proposals_instead_of_running_them(
    client, monkeypatch
) -> None:
    """An async-designated proposal must not run synchronously in a scan.

    Issue #33: list_pendings auto-executed an approved show_then_auto_send
    proposal without checking the executor, so a tool configured for the
    durable async path (#21) ran inside the pendings request instead: it
    blocked the request, created no operation, and settled the proposal
    through the synchronous leg.
    """
    import src.http.routers.governance as governance_router
    from src.sdk.governance import get_governance_service
    from src.sdk.governance_operations import GovernanceOperationStore
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    monkeypatch.setattr(governance_router, "_svc", lambda _user_id: service)
    monkeypatch.setattr(
        GovernanceOperationStore,
        "_external_executor_allowed_hosts",
        staticmethod(lambda: ["executor.internal"]),
    )
    monkeypatch.setattr(
        GovernanceOperationStore,
        "_callback_secret",
        staticmethod(lambda: "test-operation-secret"),
    )
    executor = ExternalHTTPExecutor(
        kind="external_http", dispatch_url="https://executor.internal/run"
    )
    monkeypatch.setattr(service, "_active_tool_definition", lambda *_args: object())
    monkeypatch.setattr(service, "execution_mode_for_tool", lambda _u, _t: "async")
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "show_then_auto_send")
    monkeypatch.setattr(service, "external_executor_for_tool", lambda *_args: executor)

    proposal_id = service.create_pending(
        "alice",
        "menu_change_execute",
        {"store": "HQ"},
        tier="show_then_auto_send",
        executor=executor,
    )
    # The auto-send window has elapsed: the scan's lazy expiry approves it.
    with service._conn("alice") as conn:
        conn.execute(
            "UPDATE proposals SET expires_at=? WHERE proposal_id=?",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), proposal_id),
        )
        conn.commit()

    ran_synchronously: list[object] = []

    async def sync_executor_must_not_run(*args, **kwargs):
        ran_synchronously.append(args)
        return {"content": "ran synchronously", "structured_content": {"executed": True}}

    monkeypatch.setattr(
        governance_router, "execute_approved_tool", sync_executor_must_not_run
    )

    response = client.get("/v1/governance/pendings", params={"user_id": "alice"})
    assert response.status_code == 200, response.text
    row = next(
        item for item in response.json() if item["proposal_id"] == proposal_id
    )

    assert ran_synchronously == [], "an async-designated proposal ran synchronously"
    assert row["status"] == "consumed", row
    assert row["execution"]["operation_id"], row


def test_pendings_scan_fails_closed_when_async_validation_refuses(client, monkeypatch) -> None:
    """A rejected async dispatch must not fall back to running the tool.

    The scan is a read endpoint: it reports that the proposal was not
    dispatched and leaves it alone, rather than executing something its own
    async validation refused.
    """
    import src.http.routers.governance as governance_router
    from src.sdk.governance import get_governance_service
    from src.sdk.tools import ExternalHTTPExecutor

    service = get_governance_service("alice")
    monkeypatch.setattr(governance_router, "_svc", lambda _user_id: service)
    executor = ExternalHTTPExecutor(
        kind="external_http", dispatch_url="https://executor.internal/run"
    )
    monkeypatch.setattr(service, "resolve_tier", lambda *_args: "show_then_auto_send")

    def refuse(*_args, **_kwargs):
        raise ValueError("async tool is no longer enabled")

    monkeypatch.setattr(service, "validate_async_approval", refuse)

    proposal_id = service.create_pending(
        "alice",
        "menu_change_execute",
        {"store": "HQ"},
        tier="show_then_auto_send",
        executor=executor,
    )
    with service._conn("alice") as conn:
        conn.execute(
            "UPDATE proposals SET expires_at=? WHERE proposal_id=?",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), proposal_id),
        )
        conn.commit()

    ran_synchronously: list[object] = []

    async def sync_executor_must_not_run(*args, **kwargs):
        ran_synchronously.append(args)
        return {"content": "ran", "structured_content": {"executed": True}}

    monkeypatch.setattr(
        governance_router, "execute_approved_tool", sync_executor_must_not_run
    )

    response = client.get("/v1/governance/pendings", params={"user_id": "alice"})
    assert response.status_code == 200, response.text
    row = next(item for item in response.json() if item["proposal_id"] == proposal_id)

    assert ran_synchronously == [], "a refused async dispatch ran synchronously"
    assert row["execution"]["status"] == "refused", row
    assert "no longer enabled" in row["execution"]["detail"], row
    assert row["status"] == "approved", "the proposal must be left for a human"
