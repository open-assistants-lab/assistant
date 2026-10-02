"""Issue #50: enforcement failure must never authorize a tool body."""

import asyncio
import sqlite3
from typing import Any

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, ToolCall
from src.sdk.middleware_hitl import HITLMiddleware
from src.sdk.state import AgentState
from src.sdk.tools import tool
from tests.sdk.test_sdk_loop import MockProvider


@pytest.fixture
def guarded_loop(monkeypatch):
    import src.sdk.governance as gov

    calls = []

    @tool
    async def guarded_write() -> str:
        """A side effect that must not run without authorization."""
        calls.append("executed")
        return "executed"

    svc = GovernanceService()
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    monkeypatch.setattr(gov, "get_governance_service", lambda user_id: svc)
    monkeypatch.setattr(svc, "resolve_permission_for_call", lambda *args: "ask")
    loop = AgentLoop(
        provider=MockProvider(),
        tools=[guarded_write],
        middlewares=[HITLMiddleware(user_id="guard-test")],
    )
    return loop, svc, calls


def fail_policy(*args: Any, **kwargs: Any) -> None:
    raise sqlite3.OperationalError("private database detail")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["resolve_permission_for_call", "create_pending"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("batch", [False, True])
async def test_guard_failure_never_executes_dispatch(
    guarded_loop, monkeypatch, failure, stream, batch
):
    """Swallowing a policy/storage exception must fail all four dispatch paths."""
    loop, svc, calls = guarded_loop
    monkeypatch.setattr(svc, failure, fail_policy)
    tc = ToolCall(id="call-1", name="guarded_write", arguments={})
    state = AgentState(messages=[])
    if stream:
        dispatch = (
            loop._execute_tool_batch_streaming([tc], state)
            if batch
            else loop._execute_single_tool_streaming(tc, state)
        )
        events = [event async for event in dispatch]
        assert any(event.canonical_type == "tool_result" for event in events)
    elif batch:
        await loop._execute_tool_batch([tc], state)
    else:
        await loop._execute_single_tool(tc, state)

    assert calls == []
    results = [message for message in state.messages if message.role == "tool"]
    assert len(results) == 1
    assert results[0].tool_call_id == "call-1"
    assert "NOT executed" in results[0].content
    assert "private database detail" not in results[0].content


@pytest.mark.asyncio
async def test_guard_failure_returns_explicit_nonexecution(guarded_loop, monkeypatch):
    loop, svc, calls = guarded_loop
    monkeypatch.setattr(svc, "create_pending", fail_policy)
    prepared = await loop._prepare_tool_call(
        ToolCall(id="call-1", name="guarded_write", arguments={})
    )
    assert prepared.blocked
    result = prepared.blocked_result
    assert result.is_error
    assert result.structured_content["executed"] is False
    assert result.structured_content["governance"] == "error"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_public_run_continues_after_blocked_guard(guarded_loop, monkeypatch, stream):
    loop, svc, calls = guarded_loop
    monkeypatch.setattr(svc, "create_pending", fail_policy)
    loop.provider.set_responses([
        Message.assistant(tool_calls=[ToolCall(id="call-1", name="guarded_write", arguments={})]),
        Message.assistant(content="The action was not executed."),
    ])
    messages = [Message.user("Perform the action")]
    if stream:
        events = [event async for event in loop.run_stream(messages)]
        assert any(event.canonical_type == "done" for event in events)
    else:
        result = await loop.run(messages)
        assert result[-1].content == "The action was not executed."
    assert calls == []


@pytest.mark.asyncio
async def test_guard_cancellation_propagates(guarded_loop, monkeypatch):
    loop, svc, calls = guarded_loop

    def cancelled(*args: Any, **kwargs: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(svc, "create_pending", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await loop._prepare_tool_call(ToolCall(id="c", name="guarded_write", arguments={}))
    assert calls == []


@pytest.mark.asyncio
async def test_audit_failure_does_not_block_authorized_tool(guarded_loop, monkeypatch):
    loop, svc, calls = guarded_loop
    monkeypatch.setattr(svc, "resolve_permission_for_call", lambda *args: "allow")
    monkeypatch.setattr(loop.capture_bus, "emit", fail_policy)
    loop._emit_audit(kind="tool_call", tool="guarded_write")
    await loop._execute_single_tool(
        ToolCall(id="c", name="guarded_write", arguments={}), AgentState(messages=[])
    )
    assert calls == ["executed"]
