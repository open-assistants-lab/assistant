"""Follow-up steering lifetime (audit E25).

A steer that arrives while the agent generates pure text stays queued on the
loop. RunService unregisters the loop when the stream ends, so the WS
handler's old `get_user_loop` lookup always returned None post-done — users
saw "steer accepted" followed by silence. These tests pin the fix: the live
loop is captured via execute_stream's on_stream_end callback and the
follow-up turn runs with the steered message even after unregistration.
"""

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


class StubLoop:
    """Minimal loop stand-in exposing the pending-steer surface."""

    def __init__(self, pending: list[str]):
        self._pending = list(pending)

    def has_pending_steer(self) -> bool:
        return bool(self._pending)

    def pop_steer(self) -> str | None:
        return self._pending.pop(0) if self._pending else None


@pytest.mark.asyncio
async def test_followup_steer_runs_as_next_turn_after_stream_end(monkeypatch):
    agent_stream_histories: list[list] = []

    class FakeConversation:
        def __init__(self):
            self._history_calls = 0

        def add_message(self, *args, **kwargs):
            return "msg-1"

        def get_messages_with_summary(self, *, session_id, limit):
            self._history_calls += 1
            if self._history_calls >= 2:
                # Follow-up reload: the steer was persisted at injection time.
                return [
                    SimpleNamespace(
                        role="user",
                        content="focus on the tests",
                        metadata={},
                        id="steer-1",
                        ts=None,
                        session_id=session_id,
                    )
                ]
            return [SimpleNamespace(role="user", content="go", metadata={}, id="u1", ts=None, session_id=session_id)]

    class FakeWebSocket:
        client = None

        def __init__(self, release):
            # Hold the connection open after frames run out: a real client
            # stays connected post-stream. Without this, receive_text raises
            # WebSocketDisconnect concurrently with stream completion and the
            # handler re-raises before reaching the follow-up block.
            self._release = release
            self.messages = [
                json.dumps(
                    {"type": "user_message", "content": "go", "user_id": "test_user"}
                ),
            ]
            self.sent = []

        async def accept(self):
            pass

        async def receive_text(self):
            if not self.messages:
                await self._release.wait()
                raise WebSocketDisconnect()
            return self.messages.pop(0)

        async def send_json(self, payload):
            self.sent.append(payload)

    real_run_agent_stream = ws_router._run_agent_stream

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="working")
        yield StreamChunk.done(content="working")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        on_end = kwargs.pop("on_stream_end", None)
        # Simulate the steer having been queued during pure-text generation.
        stub = StubLoop(["focus on the tests"])
        if on_end is not None:
            on_end(stub)
        async for event in base_fake(service_self, **kwargs):
            yield event

    async def recording_run_agent_stream(*args, **kwargs):
        out = kwargs.pop("stream_loop_out", None)
        agent_stream_histories.append(
            kwargs.get("sdk_messages") if "sdk_messages" in kwargs else args[2]
        )
        if len(agent_stream_histories) == 1:
            # First (real) turn keeps the live wiring so the holder gets
            # populated via execute_stream's on_stream_end callback.
            if out is not None:
                kwargs["stream_loop_out"] = out
            await real_run_agent_stream(*args, **kwargs)
        else:
            # Follow-up recorded: let ws_conversation finish cleanly.
            release.set()

    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=FakeConversation())
    )
    monkeypatch.setattr(ws_router, "_run_agent_stream", recording_run_agent_stream)
    monkeypatch.setattr(
        ws_router.RunService, "execute_stream", fake_execute_stream
    )

    release = asyncio.Event()
    websocket = FakeWebSocket(release)

    await ws_router.ws_conversation(websocket)

    assert len(agent_stream_histories) >= 2, (
        "follow-up turn never ran: steer accepted but delivered nowhere "
        "(audit E25)"
    )
    follow_history = agent_stream_histories[1]
    contents = [
        m.get("content") if isinstance(m, dict) else str(m.content)
        for m in follow_history
    ]
    assert any("focus on the tests" in c for c in contents), (
        f"follow-up history missing steered message: {contents}"
    )


@pytest.mark.asyncio
async def test_multiple_pending_steers_each_get_followup_turn(monkeypatch):
    """P2-1: every queued steer gets its own follow-up turn, not just the first."""
    agent_stream_histories: list[list] = []

    class FakeConversation:
        def __init__(self):
            self._history_calls = 0

        def add_message(self, *args, **kwargs):
            return "msg-1"

        def get_messages_with_summary(self, *, session_id, limit):
            self._history_calls += 1
            if self._history_calls == 2:
                content = "steer one"
            elif self._history_calls >= 3:
                content = "steer two"
            else:
                content = "go"
            return [
                SimpleNamespace(
                    role="user", content=content, metadata={}, id=f"u{self._history_calls}",
                    ts=None, session_id=session_id,
                )
            ]

    class FakeWebSocket:
        client = None

        def __init__(self, release):
            self._release = release
            self.messages = [
                json.dumps({"type": "user_message", "content": "go", "user_id": "test_user"}),
            ]
            self.sent = []

        async def accept(self):
            pass

        async def receive_text(self):
            if not self.messages:
                await self._release.wait()
                raise WebSocketDisconnect()
            return self.messages.pop(0)

        async def send_json(self, payload):
            self.sent.append(payload)

    real_run_agent_stream = ws_router._run_agent_stream

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="working")
        yield StreamChunk.done(content="working")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        on_end = kwargs.pop("on_stream_end", None)
        stub = StubLoop(["steer one", "steer two"])
        if on_end is not None:
            on_end(stub)
        async for event in base_fake(service_self, **kwargs):
            yield event

    async def recording_run_agent_stream(*args, **kwargs):
        out = kwargs.pop("stream_loop_out", None)
        agent_stream_histories.append(
            kwargs.get("sdk_messages") if "sdk_messages" in kwargs else args[2]
        )
        if len(agent_stream_histories) == 1:
            if out is not None:
                kwargs["stream_loop_out"] = out
            await real_run_agent_stream(*args, **kwargs)
        else:
            # Follow-up turn ran; let ws_conversation continue draining.
            release.set()

    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=FakeConversation())
    )
    monkeypatch.setattr(ws_router, "_run_agent_stream", recording_run_agent_stream)
    monkeypatch.setattr(
        ws_router.RunService, "execute_stream", fake_execute_stream
    )

    release = asyncio.Event()
    websocket = FakeWebSocket(release)

    await ws_router.ws_conversation(websocket)

    assert len(agent_stream_histories) == 3, (
        f"expected 1 real turn + 2 follow-up turns, got {len(agent_stream_histories)}"
    )
    delivered = [
        (m.get("content") if isinstance(m, dict) else str(m.content))
        for m in agent_stream_histories[1] + agent_stream_histories[2]
    ]
    assert any("steer one" in c for c in delivered), delivered
    assert any("steer two" in c for c in delivered), delivered
