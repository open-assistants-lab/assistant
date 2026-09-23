"""Permission-gated connector vertical slice."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.governance_dispatcher import GovernedOperationDispatcher
from src.sdk.governance_operations import OperationStatus
from src.sdk.middleware_hitl import HITLMiddleware
from src.sdk.tools import ExternalHTTPExecutor, ToolAnnotations, ToolDefinition


@pytest.mark.asyncio
async def test_permission_gated_connector_flows_to_idempotent_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GovernanceService(data_root=str(tmp_path))
    executor = ExternalHTTPExecutor(
        kind="external_http",
        dispatch_url="https://connector.internal/gmail/send",
        manifest_hash="gmail-send-v1",
    )
    connector = ToolDefinition(
        name="connector_gmail_send",
        description="Send a Gmail message through the connected service.",
        parameters={"type": "object", "properties": {"to": {"type": "string"}}},
        annotations=ToolAnnotations(
            requires_approval=True,
            execution_mode="async",
            executor=executor,
        ),
        function=lambda **_: None,
    )

    class Registry:
        def get(self, name: str) -> ToolDefinition | None:
            return connector if name == connector.name else None

    loop = SimpleNamespace(_registry=Registry(), _flow_session_id="session-1")
    monkeypatch.setattr("src.sdk.governance.get_governance_service", lambda _user_id: service)
    monkeypatch.setattr("src.sdk.governance.governance_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "resolve_permission_for_call",
        lambda _user_id, _tool, _arguments: "ask",
    )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "connector-test-secret"),
    )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._external_executor_allowed_hosts",
        staticmethod(lambda: ["connector.internal"]),
    )

    from src.sdk.loop import _current_agent_loop

    token = cast(Any, _current_agent_loop).set(loop)
    try:
        pending = await HITLMiddleware(user_id="alice").guard_tool_call(
            connector.name, {"to": "person@example.com"}
        )
    finally:
        cast(Any, _current_agent_loop).reset(token)

    assert pending is not None
    assert pending.structured_content is not None
    proposal_id = pending.structured_content["proposal_id"]
    proposal = service.get_pending("alice", proposal_id)
    assert proposal is not None
    assert proposal["status"] == "pending"
    assert proposal["executor"] == executor.model_dump(mode="json")

    created, approved_now = service.approve_external_operation("alice", proposal_id, executor)
    assert approved_now is True
    assert created.operation.status.value == "queued"

    sent: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    class Response:
        is_success: bool = True

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> Response:
            sent.append((url, json, headers))
            return Response()

    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda _user_id: service)
    monkeypatch.setattr("src.sdk.governance_dispatcher.httpx.AsyncClient", Client)

    dispatcher = GovernedOperationDispatcher(worker_id="connector-test")
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    assert len(sent) == 1
    assert sent[0][0] == executor.dispatch_url
    assert sent[0][1]["tool_name"] == connector.name
    assert sent[0][1]["arguments"] == {"to": "person@example.com"}
    assert sent[0][2]["Idempotency-Key"] == created.operation.dispatch_idempotency_key
    dispatch = service.operations.get_dispatch("alice", created.operation.operation_id)
    assert dispatch is not None
    assert dispatch.status == "dispatched"

    finished = service.operations.finish_callback(
        "alice",
        created.operation.operation_id,
        created.callback_capability or "",
        OperationStatus.SUCCEEDED,
        proposal_id=proposal_id,
        tool_name=connector.name,
        arguments_hash=created.operation.arguments_hash,
        manifest_hash=executor.manifest_hash,
        result={"provider_message_id": "gmail-message-1"},
    )
    assert finished.status is OperationStatus.SUCCEEDED
    assert finished.result == {"provider_message_id": "gmail-message-1"}
