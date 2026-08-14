"""Tests for agent/message-store integration."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.http.models import MessageRequest
from src.sdk.messages import Message, StreamChunk
from src.sdk.runner import _messages_from_conversation
from tests.api.conftest import make_run_event_factory


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

    def persist_run(self, **kwargs) -> None:
        pass

    def get_messages_by_session_id(self, session_id: str, limit: int = 50) -> list[StoredMessage]:
        return self.messages[-limit:]

    def get_messages_with_summary(
        self, session_id: str, limit: int = 50
    ) -> list[StoredMessage]:
        return self.messages[-limit:]


class SessionAwareConversation(FakeConversation):
    def __init__(self) -> None:
        super().__init__()
        self.summary_calls: list[tuple[str, int]] = []
        self.raw_calls: list[tuple[str, int]] = []
        self.sessions = {
            "session-a": [StoredMessage("summary", "A summary"), StoredMessage("user", "A kept")],
            "session-b": [StoredMessage("summary", "B sentinel")],
        }

    def get_messages_with_summary(
        self, session_id: str, limit: int = 50
    ) -> list[StoredMessage]:
        self.summary_calls.append((session_id, limit))
        return self.sessions[session_id]

    def get_messages_by_session_id(self, session_id: str, limit: int = 50) -> list[StoredMessage]:
        self.raw_calls.append((session_id, limit))
        raise AssertionError("raw transcript used for model input")


@pytest.mark.asyncio
async def test_rest_runner_uses_only_session_scoped_summary_history(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router
    from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

    store = SessionAwareConversation()
    captured = {}

    class DummyLoop:
        model_id = "x:y"

    async def fake_get_sdk_loop(*args, **kwargs):
        return DummyLoop()

    async def fake_orchestrate(self, loop, messages, run_id, session_id, lock, rubric=None):
        captured["messages"] = messages
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            status=RunStatus.COMPLETED,
            attempt=1,
            model="x:y",
            response="done",
            usage=RunUsage(),
            verification=VerificationOutcome(),
        )

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router.RunService, "_run_bounded_orchestration", fake_orchestrate)
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)

    await conversation_router.handle_message(
        MessageRequest(message="new A", user_id="u", session_id="session-a")
    )

    contents = [str(message.content) for message in captured["messages"]]
    assert store.summary_calls == [("session-a", 50)]
    assert store.raw_calls == []
    assert any("A summary" in content for content in contents)
    assert all("B sentinel" not in content for content in contents)


@pytest.mark.asyncio
async def test_sse_runner_uses_only_session_scoped_summary_history(monkeypatch) -> None:
    from src.http.routers import conversation as conversation_router

    store = SessionAwareConversation()
    captured = {}

    class DummyLoop:
        model_id = "x:y"

        async def run_stream(self, messages):
            captured["messages"] = messages
            yield StreamChunk.done("done")

    async def fake_get_sdk_loop(*args, **kwargs):
        return DummyLoop()

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router.RunService, "_load_rubric_middleware", lambda self, loop, rubric=None: None)
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)

    response = await conversation_router.message_stream(
        MessageRequest(message="new A", user_id="u", session_id="session-a")
    )
    async for _ in response.body_iterator:
        pass

    contents = [str(message.content) for message in captured["messages"]]
    assert store.summary_calls == [("session-a", 50)]
    assert store.raw_calls == []
    assert any("A summary" in content for content in contents)
    assert all("B sentinel" not in content for content in contents)


def test_tool_messages_are_preserved_as_context() -> None:
    messages = [
        StoredMessage("user", "List my unread emails"),
        StoredMessage("tool", "5 unread emails", {"tool_name": "email_list"}),
        StoredMessage("assistant", "I found 5 emails."),
    ]

    sdk_messages = _messages_from_conversation(messages)

    assert any("email_list" in str(m.content) and "5 unread" in str(m.content) for m in sdk_messages)


@pytest.mark.skip(
    reason="handle_message no longer persists tool messages: the RunService refactor "
    "surfaced tool calls as RunResult.tool_calls (MessageResponse.tool_calls) instead. "
    "Tool-message persistence on the streaming path is covered by "
    "test_stream_message_persists_tool_result_content."
)
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


@pytest.mark.skip(
    reason="handle_message no longer persists reasoning/tool messages: the RunService "
    "refactor surfaced tool calls as RunResult.tool_calls instead. Dedup and tool "
    "persistence on the streaming path are covered by "
    "test_stream_message_dedupes_alias_text_and_tool_end."
)
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
    from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

    store = FakeConversation()

    class DummyLoop:
        model_id = "x:y"

    async def fake_get_sdk_loop(*args, **kwargs):
        return DummyLoop()

    async def fake_orchestrate(self, loop, messages, run_id, session_id, lock, rubric=None):
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            status=RunStatus.COMPLETED,
            attempt=1,
            model="x:y",
            response="found memory",
            usage=RunUsage(),
            verification=VerificationOutcome(),
            tool_calls=[{"name": "message_search", "tool_call_id": "call_1"}],
        )

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router.RunService, "_run_bounded_orchestration", fake_orchestrate)
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)

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
    monkeypatch.setattr(conversation_router.RunService, "execute_stream", make_run_event_factory(fake_stream))

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
        # Canonical stream: aliases (ai_token/reasoning/tool_end) no longer exist
        # on the RunEvent wire; only the canonical events are emitted.
        yield StreamChunk.text_delta("Hello")
        yield StreamChunk.reasoning_delta("Think")
        yield StreamChunk.tool_input_start("email_list", "call_1")
        yield StreamChunk.tool_end("email_list", "call_1", "legacy result")
        yield StreamChunk.tool_result_event("email_list", "call_1", "canonical result")

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router.RunService, "execute_stream", make_run_event_factory(fake_stream))

    response = await conversation_router.message_stream(
        MessageRequest(message="List emails", user_id="u")
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    output = "".join(chunks)
    # Canonical RunEvent wire format carries deltas under data.delta.
    assert output.count('"delta": "Hello"') == 1
    assert output.count('"delta": "Think"') == 1
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
    monkeypatch.setattr(conversation_router.RunService, "execute_stream", make_run_event_factory(fake_stream))

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
    monkeypatch.setattr(conversation_router.RunService, "execute_stream", make_run_event_factory(fake_stream))

    response = await conversation_router.message_stream(MessageRequest(message="cancel", user_id="u"))
    async for _ in response.body_iterator:
        pass

    assert [m for m in store.messages if m.role == "assistant"] == []


@pytest.mark.asyncio
async def test_stream_done_failed_does_not_persist_success_fallback(monkeypatch) -> None:
    """A run that fails mid-stream yields done(status=failed); the router must not
    persist the 'Task completed.' success fallback for it."""
    from datetime import UTC, datetime

    from src.http.routers import conversation as conversation_router
    from src.sdk.run_events import DoneData, DoneEvent
    from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

    store = FakeConversation()

    common = dict(
        event_id="e1",
        sequence=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="default",
        run_id="r1",
        attempt=1,
    )

    async def fake_execute_stream(self, **kwargs):
        yield DoneEvent(
            data=DoneData(
                result=RunResult(
                    run_id="r1",
                    session_id="default",
                    status=RunStatus.FAILED,
                    attempt=1,
                    model="x:y",
                    response="",
                    usage=RunUsage(),
                    verification=VerificationOutcome(),
                    persisted_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ),
            **common,
        )

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(conversation_router.RunService, "execute_stream", fake_execute_stream)

    response = await conversation_router.message_stream(MessageRequest(message="fail", user_id="u"))
    async for _ in response.body_iterator:
        pass

    assert [m for m in store.messages if m.role == "assistant"] == []


@pytest.mark.skip(
    reason="handle_message no longer has a verbose/stream split: the RunService refactor "
    "removed the empty-stream fallback path (run_sdk_agent_stream + run_sdk_agent). "
    "The non-streaming path always runs exactly once via RunService.execute."
)
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
