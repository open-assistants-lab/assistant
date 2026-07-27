"""Tests for agent/message-store integration."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.http.models import MessageRequest
from src.sdk.messages import Message, StreamChunk
from src.sdk.runner import _messages_from_conversation


@dataclass
class StoredMessage:
    role: str
    content: str
    metadata: dict | None = None


class FakeConversation:
    def __init__(self) -> None:
        self.messages: list[StoredMessage] = []

    def add_message(self, role: str, content: str, metadata: dict | None = None, session_id: str | None = None) -> None:
        self.messages.append(StoredMessage(role, content, metadata))

    def get_messages_by_session_id(self, session_id: str, limit: int = 50) -> list[StoredMessage]:
        return self.messages[-limit:]

    def get_messages_with_summary(self, limit: int) -> list[StoredMessage]:
        return self.messages[-limit:]


def test_tool_messages_are_preserved_as_context() -> None:
    messages = [
        StoredMessage("user", "List my unread emails"),
        StoredMessage("tool", "5 unread emails", {"tool_name": "email_list"}),
        StoredMessage("assistant", "I found 5 emails."),
    ]

    sdk_messages = _messages_from_conversation(messages)

    assert any("email_list" in str(m.content) and "5 unread" in str(m.content) for m in sdk_messages)


@pytest.mark.asyncio
async def test_verbose_message_persists_tool_results(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.tool_input_start("email_list", "call_1")
        yield StreamChunk.tool_result_event("email_list", "call_1", "5 unread emails")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    await conversation_router.handle_message(
        MessageRequest(message="List emails", user_id="u", verbose=True)
    )

    tool_messages = [m for m in store.messages if m.role == "tool"]
    assert [(m.content, m.metadata) for m in tool_messages] == [
        ("5 unread emails", {"tool_name": "email_list", "tool_call_id": "call_1"})
    ]


@pytest.mark.asyncio
async def test_verbose_message_dedupes_alias_text_and_tool_end(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.text_delta("Hello")
        yield StreamChunk.ai_token("Hello")
        yield StreamChunk.reasoning_delta("Think")
        yield StreamChunk.reasoning("Think")
        yield StreamChunk.tool_input_start("email_list", "call_1")
        yield StreamChunk.tool_end("email_list", "call_1", "legacy result")
        yield StreamChunk.tool_result_event("email_list", "call_1", "canonical result")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    result = await conversation_router.handle_message(
        MessageRequest(message="List emails", user_id="u", verbose=True)
    )

    assert result.response == "canonical result"
    assert [m.content for m in store.messages if m.role == "reasoning"] == ["Think"]
    assert [(m.content, m.metadata) for m in store.messages if m.role == "tool"] == [
        ("canonical result", {"tool_name": "email_list", "tool_call_id": "call_1"})
    ]


@pytest.mark.asyncio
async def test_verbose_message_reports_tool_call_when_start_name_arrives_late(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.tool_input_start("", "call_1")
        yield StreamChunk.tool_result_event("message_search", "call_1", "found memory")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    result = await conversation_router.handle_message(
        MessageRequest(message="Search memory", user_id="u", verbose=True)
    )

    assert result.tool_calls == [{"name": "message_search", "tool_call_id": "call_1"}]


@pytest.mark.asyncio
async def test_stream_message_persists_tool_result_content(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.tool_input_start("email_list", "call_1")
        yield StreamChunk.tool_result_event("email_list", "call_1", "5 unread emails")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    response = await conversation_router.message_stream(
        MessageRequest(message="List emails", user_id="u")
    )
    async for _ in response.body_iterator:
        pass

    tool_messages = [m for m in store.messages if m.role == "tool"]
    assert [(m.content, m.metadata) for m in tool_messages] == [
        ("5 unread emails", {"tool_name": "email_list", "tool_call_id": "call_1"})
    ]


@pytest.mark.asyncio
async def test_stream_message_dedupes_alias_text_and_tool_end(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.text_delta("Hello")
        yield StreamChunk.ai_token("Hello")
        yield StreamChunk.reasoning_delta("Think")
        yield StreamChunk.reasoning("Think")
        yield StreamChunk.tool_input_start("email_list", "call_1")
        yield StreamChunk.tool_end("email_list", "call_1", "legacy result")
        yield StreamChunk.tool_result_event("email_list", "call_1", "canonical result")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    response = await conversation_router.message_stream(
        MessageRequest(message="List emails", user_id="u")
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    output = "".join(chunks)
    assert output.count('"content": "Hello"') == 1
    assert output.count('"content": "Think"') == 1
    assert "legacy result" not in output
    assert [(m.content, m.metadata) for m in store.messages if m.role == "tool"] == [
        ("canonical result", {"tool_name": "email_list", "tool_call_id": "call_1"})
    ]


@pytest.mark.asyncio
async def test_stream_error_does_not_persist_success_fallback(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        yield StreamChunk.error("boom")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    response = await conversation_router.message_stream(MessageRequest(message="fail", user_id="u"))
    async for _ in response.body_iterator:
        pass

    assert [m for m in store.messages if m.role == "assistant"] == []


@pytest.mark.asyncio
async def test_stream_cancel_does_not_persist_success_fallback(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()

    async def fake_stream(**kwargs):
        conversation_router._cancel_flags["u:default"] = True
        yield StreamChunk.text_delta("ignored")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

    response = await conversation_router.message_stream(MessageRequest(message="cancel", user_id="u"))
    async for _ in response.body_iterator:
        pass

    assert [m for m in store.messages if m.role == "assistant"] == []


@pytest.mark.asyncio
async def test_verbose_empty_stream_does_not_run_agent_twice(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = FakeConversation()
    run_calls = 0

    async def fake_stream(**kwargs):
        if False:
            yield StreamChunk.text_delta("")

    async def fake_run(**kwargs):
        nonlocal run_calls
        run_calls += 1
        return [Message.assistant("fallback")]

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)
    monkeypatch.setattr(conversation_router, "run_sdk_agent", fake_run)

    result = await conversation_router.handle_message(
        MessageRequest(message="Do something", user_id="u", verbose=True)
    )

    assert result.response == "Task completed."
    assert run_calls == 0
