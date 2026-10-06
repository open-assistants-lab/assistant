"""Vendor-free proof of approval, external dispatch and durable terminal evidence."""

import httpx
import pytest


@pytest.mark.asyncio
async def test_fixture_approval_dispatch_callback_and_restart(tmp_path, monkeypatch):
    from src.sdk.governance import GovernanceService
    from src.sdk.governance_dispatcher import GovernedOperationDispatcher
    from src.sdk.governance_operations import OperationStatus
    from tests.sdk.governance_fixture import external_fixture

    tool = external_fixture()
    executor = tool.annotations.executor
    service = GovernanceService(data_root=str(tmp_path))
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._callback_secret",
        staticmethod(lambda: "fixture-only-callback-secret"),
    )
    monkeypatch.setattr(
        "src.sdk.governance_operations.GovernanceOperationStore._external_executor_allowed_hosts",
        staticmethod(lambda: ["executor.invalid"]),
    )
    proposal_id = service.create_pending(
        "alice", tool.name, {"payload": "fixture-value"}, executor=executor
    )
    assert service.operations.queued_dispatch_ids("alice") == []
    with pytest.raises(ValueError):
        service.approve_external_operation("bob", proposal_id, executor)
    created, accepted = service.approve_external_operation("alice", proposal_id, executor)
    assert accepted is True
    repeated, accepted_again = service.approve_external_operation("alice", proposal_id, executor)
    assert accepted_again is False
    assert repeated.operation.operation_id == created.operation.operation_id

    envelopes = []
    def accept(request):
        import json
        envelopes.append((request.url, json.loads(request.content), request.headers))
        return httpx.Response(202, json={"accepted": True})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "src.sdk.governance_dispatcher.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(accept), **kwargs),
    )
    monkeypatch.setattr("src.sdk.governance_dispatcher.iter_governance_user_ids", lambda: ["alice"])
    monkeypatch.setattr("src.sdk.governance_dispatcher.get_governance_service", lambda _: service)
    dispatcher = GovernedOperationDispatcher(worker_id="fixture-worker")
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    assert len(envelopes) == 1
    url, envelope, headers = envelopes[0]
    assert str(url) == "https://executor.invalid/fixture"
    assert envelope["tool_name"] == "governance_fixture"
    assert envelope["arguments"] == {"payload": "fixture-value"}
    assert headers["Idempotency-Key"] == created.operation.dispatch_idempotency_key

    binding = dict(
        proposal_id=proposal_id, tool_name=tool.name,
        arguments_hash=created.operation.arguments_hash,
        manifest_hash=executor.manifest_hash,
    )
    with pytest.raises(PermissionError):
        service.operations.finish_callback(
            "alice", created.operation.operation_id, "wrong-capability",
            OperationStatus.SUCCEEDED, **binding,
        )
    terminal = service.operations.finish_callback(
        "alice", created.operation.operation_id, envelope["callback_token"],
        OperationStatus.SUCCEEDED, result={"fixture_result_id": "result-1"}, **binding,
    )
    assert terminal.status is OperationStatus.SUCCEEDED
    duplicate = service.operations.finish_callback(
        "alice", created.operation.operation_id, envelope["callback_token"],
        OperationStatus.SUCCEEDED, result={"fixture_result_id": "must-not-overwrite"}, **binding,
    )
    assert duplicate.result == {"fixture_result_id": "result-1"}
    restarted = GovernanceService(data_root=str(tmp_path))
    durable = restarted.operations.get_operation("alice", terminal.operation_id)
    assert durable is not None
    assert durable.status is OperationStatus.SUCCEEDED
    assert durable.result == {"fixture_result_id": "result-1"}
    assert restarted.operations.queued_dispatch_ids("alice") == []
    assert restarted.operations.get_operation("bob", terminal.operation_id) is None
