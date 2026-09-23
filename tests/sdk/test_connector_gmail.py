"""Permission-gated Gmail connector tool tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.middleware_hitl import HITLMiddleware
from src.sdk.tools import ToolDefinition, ToolResult


@pytest.mark.asyncio
async def test_connector_gmail_send_requires_approval_and_returns_provider_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.sdk.tools_core.connector_gmail import connector_gmail_send

    class Client:
        def __init__(self, *, user_id: str):
            assert user_id == "alice"

        async def send_message(self, to: str, subject: str, body: str) -> dict[str, str]:
            assert (to, subject, body) == (
                "person@example.com",
                "Hello",
                "Message body",
            )
            return {"id": "sent-1", "threadId": "thread-1"}

    monkeypatch.setattr("src.sdk.tools_core.connector_gmail.GmailClient", Client)

    assert connector_gmail_send.annotations.requires_approval is True
    result = await connector_gmail_send.ainvoke(
        {
            "to": "person@example.com",
            "subject": "Hello",
            "body": "Message body",
            "user_id": "alice",
        }
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.structured_content == {
        "connector": "gmail",
        "provider_message_id": "sent-1",
        "thread_id": "thread-1",
    }


@pytest.mark.asyncio
async def test_permission_gate_approves_and_executes_gmail_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.sdk.tools_core.connector_gmail import connector_gmail_send

    service = GovernanceService(data_root=str(tmp_path))
    monkeypatch.setattr("src.sdk.governance.get_governance_service", lambda _user_id: service)
    monkeypatch.setattr("src.sdk.governance.governance_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "resolve_permission_for_call",
        lambda _user_id, _tool, _arguments: "ask",
    )

    class Registry:
        def get(self, name: str) -> ToolDefinition | None:
            return connector_gmail_send if name == connector_gmail_send.name else None

    class Client:
        def __init__(self, *, user_id: str):
            assert user_id == "alice"

        async def send_message(self, to: str, subject: str, body: str) -> dict[str, str]:
            return {"id": "sent-2", "threadId": "thread-2"}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr("src.sdk.tools_core.connector_gmail.GmailClient", Client)
    loop = SimpleNamespace(_registry=Registry(), _flow_session_id="session-2")
    from src.sdk.loop import _current_agent_loop

    token = cast(Any, _current_agent_loop).set(loop)
    try:
        pending = await HITLMiddleware(user_id="alice").guard_tool_call(
            connector_gmail_send.name,
            {"to": "person@example.com", "subject": "Hello", "body": "Message body"},
        )
    finally:
        cast(Any, _current_agent_loop).reset(token)

    assert pending is not None and pending.structured_content is not None
    proposal_id = str(pending.structured_content["proposal_id"])
    assert service.approve("alice", proposal_id) is True
    result = await service.execute_approved("alice", proposal_id)

    assert result["is_error"] is False
    assert result["structured_content"]["provider_message_id"] == "sent-2"
    row = service.get_pending("alice", proposal_id)
    assert row is not None
    assert row["outcome"] == "succeeded"
