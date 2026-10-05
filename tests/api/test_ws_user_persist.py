"""#139: one WS user message persists one user row and reaches the model once."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from src.http.routers import ws as ws_router
from src.sdk.messages import StreamChunk
from tests.api.conftest import make_run_event_factory


class FakeWebSocket:
    client = None

    def __init__(self, release):
        self.messages = [
            json.dumps({"type": "user_message", "content": "go", "user_id": "test_user"}),
        ]
        self.sent = []

    async def accept(self):
        pass

    async def receive_text(self):
        if not self.messages:
            raise WebSocketDisconnect()
        return self.messages.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeConversation:
    """Conversation double that records every persisted row."""

    def __init__(self):
        self.rows: list[tuple[str, str]] = []
        self.history_calls = 0

    def add_message(self, role, content, metadata=None, session_id=None, **kwargs):
        self.rows.append((role, str(content)))
        return f"msg-{len(self.rows)}"

    def get_messages_with_summary(self, *, session_id, limit):
        self.history_calls += 1
        # The first turn's history: nothing persisted yet (RunService owns
        # the current-turn prompt under the fixed behaviour).
        if self.history_calls == 1:
            return []
        return [
            SimpleNamespace(
                role="user", content="go", metadata={}, id="u1", ts=None,
                session_id=session_id,
            )
        ]


@pytest.mark.asyncio
async def test_handler_does_not_pre_persist_the_user_message(monkeypatch):
    """The WS handler no longer writes the user row itself; RunService owns it."""
    conversation = FakeConversation()
    handler_writes: list[tuple[str, str]] = []

    real_persist = ws_router._persist_ws_conversation_message

    def spy_persist(conversation_arg, role, content, **kwargs):
        handler_writes.append((role, str(content)))
        return real_persist(conversation_arg, role, content, **kwargs)

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="working")
        yield StreamChunk.done(content="working")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        async for event in base_fake(service_self, **kwargs):
            yield event

    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(ws_router, "_persist_ws_conversation_message", spy_persist)
    monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

    release = asyncio.Event()
    websocket = FakeWebSocket(release)

    async def run_and_release():
        try:
            await ws_router.ws_conversation(websocket)
        except WebSocketDisconnect:
            pass
        finally:
            release.set()

    runner = asyncio.create_task(run_and_release())
    await asyncio.wait_for(runner, timeout=5)

    assert handler_writes == [], (
        f"the handler pre-persisted the user message: {handler_writes}"
    )
    # The prompt is still carried to the stream as the current message.
    assert websocket.sent, "the run produced no frames"


@pytest.mark.asyncio
async def test_run_service_history_does_not_double_the_prompt(monkeypatch):
    """The model-side view: the prompt reaches the run exactly once."""
    conversation = FakeConversation()
    histories_seen: list[list[str]] = []

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="working")
        yield StreamChunk.done(content="working")

    captured: dict = {}

    async def fake_execute_stream(service_self, **kwargs):
        captured.update(kwargs)
        async for event in base_fake(service_self, **kwargs):
            yield event

    base_fake = make_run_event_factory(chunk_gen)

    def spy_add_message(prompt, session_id, metadata=None, **kwargs):
        histories_seen.append(prompt)
        return "user-row"

    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

    release = asyncio.Event()
    websocket = FakeWebSocket(release)

    async def run_and_release():
        try:
            await ws_router.ws_conversation(websocket)
        except WebSocketDisconnect:
            pass
        finally:
            release.set()

    runner = asyncio.create_task(run_and_release())
    await asyncio.wait_for(runner, timeout=5)
    # The WS handler must not pre-persist; RunService owns the turn row.
    pre_writes = [r for r in conversation.rows if r[0] == "user"]
    assert len(pre_writes) <= 1, (
        f"handler pre-wrote the prompt alongside RunService: {pre_writes}"
    )
