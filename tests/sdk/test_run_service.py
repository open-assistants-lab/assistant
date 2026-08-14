"""Tests for RunService with scripted orchestration fixture."""

from __future__ import annotations

from typing import Any

import pytest

from src.sdk.messages import Message, StreamChunk
from src.sdk.run_service import RunService
from src.sdk.session_worker import SessionBusyError, SessionWorkerRegistry


class InMemoryMessageStore:
    """Minimal in-memory message store for testing."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._id_counter = 0

    def add_message(self, role: str, content: str, metadata: dict | None = None, session_id: str = "") -> str:
        self._id_counter += 1
        mid = f"msg-{self._id_counter}"
        self.messages.append({
            "id": mid,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "session_id": session_id,
        })
        return mid

    def get_messages_with_summary(self, session_id: str, limit: int = 50) -> list[Message]:
        return []

    def persist_run(self, run_id: str, session_id: str, user_message_id: str,
                    final_answer: Message, audit_records: list[Message],
                    metadata: dict) -> str:
        self._id_counter += 1
        mid = f"msg-{self._id_counter}"
        self.messages.append({
            "id": mid,
            "role": final_answer.role,
            "content": final_answer.content,
            "metadata": {**metadata, "run_id": run_id},
            "session_id": session_id,
        })
        return mid


class FakeLoop:
    """Minimal fake loop for testing RunService."""
    def __init__(self, model_id: str = "test:model"):
        self.model_id = model_id
        self.rubric = None
        self.cancel_event = None
        self.state = type("State", (), {"messages": []})()

    async def run(self, messages):
        return [Message.assistant(content="Test response")]

    async def run_stream(self, messages):
        yield StreamChunk(type="text_delta", content="Test response")
        yield StreamChunk(type="done", content="Test response")


@pytest.mark.asyncio
async def test_run_service_execute_returns_run_result(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.RunService._load_rubric_middleware", lambda *a: None)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    result = await service.execute(
        session_id="chat-1",
        prompt="Hello",
    )

    assert result.run_id is not None
    assert result.session_id == "chat-1"
    assert result.status.value == "completed"
    assert result.response is not None


@pytest.mark.asyncio
async def test_run_service_execute_stream_yields_run_events(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.RunService._load_rubric_middleware", lambda *a: None)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    events = []
    async for event in service.execute_stream(
        session_id="chat-1",
        prompt="Hello",
    ):
        events.append(event)

    assert len(events) > 0
    assert events[-1].type == "done"
    assert events[0].sequence == 1
    for i, event in enumerate(events):
        assert event.sequence == i + 1
        assert event.run_id is not None
        assert event.session_id == "chat-1"
        assert event.attempt >= 1


@pytest.mark.asyncio
async def test_run_service_session_busy(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    lock = await registry.acquire("chat-1")
    try:
        with pytest.raises(SessionBusyError):
            await service.execute(session_id="chat-1", prompt="Hello")
    finally:
        await registry.release("chat-1")


@pytest.mark.asyncio
async def test_run_service_different_sessions_concurrent(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.RunService._load_rubric_middleware", lambda *a: None)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    import asyncio
    results = await asyncio.gather(
        service.execute(session_id="chat-1", prompt="Hello"),
        service.execute(session_id="chat-2", prompt="World"),
    )
    assert len(results) == 2
    assert results[0].session_id == "chat-1"
    assert results[1].session_id == "chat-2"
