"""Former Gmail slice, retaining generic invocation and governed execution checks."""
from types import SimpleNamespace

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.middleware_hitl import HITLMiddleware
from src.sdk.tools import ToolRegistry, ToolResult


@pytest.mark.asyncio
async def test_approval_required_tool_returns_structured_result(tmp_path):
    from tests.sdk.governance_fixture import local_fixture

    target = tmp_path / "direct-result.txt"
    tool = local_fixture(target)
    assert tool.annotations.requires_approval is True
    result = await tool.ainvoke({"payload": "fixture", "user_id": "alice"})
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.structured_content == {"result_id": "fixture-1", "actor": "alice"}
    assert target.read_text() == "fixture"


@pytest.mark.asyncio
async def test_permission_gate_approves_and_executes_fixture(tmp_path, monkeypatch):
    from tests.sdk.governance_fixture import local_fixture
    from src.sdk.loop import _current_agent_loop

    target = tmp_path / "approved-result.txt"
    tool = local_fixture(target)
    service = GovernanceService(data_root=str(tmp_path))
    monkeypatch.setattr("src.sdk.governance.get_governance_service", lambda _: service)
    monkeypatch.setattr("src.sdk.governance.governance_enabled", lambda: True)
    monkeypatch.setattr(service, "resolve_permission_for_call", lambda *_: "ask")
    monkeypatch.setattr("src.sdk.runner.get_active_tool_definition", lambda *args, **kwargs: tool)
    registry = ToolRegistry()
    registry.register(tool)
    token = _current_agent_loop.set(SimpleNamespace(_registry=registry, _flow_session_id="fixture-session"))
    try:
        pending = await HITLMiddleware(user_id="alice").guard_tool_call(tool.name, {"payload": "approved"})
    finally:
        _current_agent_loop.reset(token)
    assert not target.exists()
    assert pending is not None and pending.structured_content is not None
    proposal_id = pending.structured_content["proposal_id"]
    assert service.approve("alice", proposal_id) is True
    result = await service.execute_approved("alice", proposal_id)
    assert result["is_error"] is False
    assert result["structured_content"] == {
        "result_id": "fixture-1", "actor": "alice", "executed": True,
        "tool": "governance_local_fixture", "outcome": "succeeded",
    }
    assert target.read_text() == "approved"
    row = service.get_pending("alice", proposal_id)
    assert row is not None and row["outcome"] == "succeeded"
    restarted = GovernanceService(data_root=str(tmp_path))
    restored = restarted.get_pending("alice", proposal_id)
    assert restored is not None and restored["outcome"] == "succeeded"
