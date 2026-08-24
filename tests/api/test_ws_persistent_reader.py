"""Persistent WS reader queue (audit P6 part B).

The WS handler used one fresh `receive_task` per stream pass. When the stream
completed while a control frame was in flight, the pending receive task was
cancelled and the frame was LOST (the socket had not yet delivered it, or the
task result was discarded). These tests pin the fix: a single long-lived
reader task drains the socket into an asyncio.Queue, so a control frame that
arrives while a stream is active (and survives stream completion) is still
delivered to the handler.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from src.http.routers import ws as ws_router
from src.sdk.messages import StreamChunk
from tests.api.conftest import make_run_event_factory


class FakeWebSocket:
    client = None

    def __init__(self, frames):
        # frames: list of (raw_payload, delay_before_return)
        self._frames = list(frames)
        self.sent = []

    async def accept(self):
        pass

    async def receive_text(self):
        if not self._frames:
            raise WebSocketDisconnect()
        raw, delay = self._frames.pop(0)
        if delay:
            await asyncio.sleep(delay)
        return raw

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeConversation:
    def __init__(self):
        self._history_calls = 0

    def add_message(self, *args, **kwargs):
        return "msg-1"

    def get_messages_with_summary(self, *, session_id, limit):
        return [
            SimpleNamespace(role="user", content="go", metadata={}, id="u1", ts=None, session_id=session_id)
        ]


@pytest.mark.asyncio
async def test_control_frame_survives_stream_completion(monkeypatch):
    """A ping sent while the stream is active must still get a Pong even when
    the stream completes before the frame is delivered (audit P6 part B)."""
    agent_stream_calls: list[list] = []

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="working")
        yield StreamChunk.done(content="working")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        async for event in base_fake(service_self, **kwargs):
            yield event

    async def recording_run_agent_stream(*args, **kwargs):
        sdk_msgs = kwargs.get("sdk_messages") if "sdk_messages" in kwargs else args[2]
        agent_stream_calls.append(sdk_msgs)

    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )
    monkeypatch.setattr(
        ws_router, "get_message_store", lambda *a, **k: FakeConversation()
    )
    monkeypatch.setattr(ws_router, "_run_agent_stream", recording_run_agent_stream)
    monkeypatch.setattr(
        ws_router.RunService, "execute_stream", fake_execute_stream
    )

    websocket = FakeWebSocket(
        [
            # First frame: start a run.
            (json.dumps({"type": "user_message", "content": "go", "user_id": "test_user"}), 0.0),
            # Ping arrives while the (fast-completing) stream is active. Its
            # delivery is delayed so it lands AFTER the stream completes —
            # the per-pass receive task would have been cancelled here.
            (json.dumps({"type": "ping"}), 0.05),
        ]
    )

    await ws_router.ws_conversation(websocket)

    pongs = [m for m in websocket.sent if m.get("type") == "pong"]
    assert pongs, (
        "ping sent during an active stream was lost: no pong in "
        f"{[m.get('type') for m in websocket.sent]}"
    )
    assert len(agent_stream_calls) == 1
