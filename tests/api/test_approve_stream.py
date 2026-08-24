"""Task 35: approve-stream parity (audit E-streaming).

The approve/resume stream must match the /message/stream contract: canonical
envelopes, heartbeat keepalive during silence, visible/parseable error
events, and a terminal event even when the resumed run produces empty content.
"""

import asyncio
import json

import pytest

from src.http.routers import conversation as conversation_router
from src.sdk.messages import StreamChunk


class _Store:
    """Minimal in-memory conversation store for the approve flow."""

    def __init__(self):
        self.messages = []

    def get_messages_with_summary(self, *, session_id, limit=50):
        return []

    def add_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))
        return True


class _FakeLoop:
    def __init__(self):
        self.approved = []

    def approve_tool_call(self, tool_call):
        self.approved.append(tool_call)

    async def _execute_tool(self, tc):
        from src.sdk.tools import ToolResult

        return ToolResult(content="noon")


def _install(monkeypatch, store, stream_factory, heartbeat_interval=None):
    async def fake_get_sdk_loop(*a, **k):
        return _FakeLoop()

    monkeypatch.setattr(conversation_router, "get_message_store", lambda *a, **k: store)
    monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", stream_factory)
    if heartbeat_interval is not None:
        real = conversation_router._sse_with_heartbeat

        async def fast_hb(gen, **kw):
            async for item in real(gen, interval=heartbeat_interval):
                yield item

        monkeypatch.setattr(conversation_router, "_sse_with_heartbeat", fast_hb)


async def _consume(response):
    frames = []
    async for line in response.body_iterator:
        frames.append(line)
    return frames


def _parse_events(frames):
    return [
        json.loads(line[len("data: "):])
        for line in frames
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_approve_stream_emits_envelope_events_with_heartbeat(monkeypatch):
    """Approve stream yields envelope-shaped events + heartbeat pings during silence."""
    conversation_router._pending_interrupts["u:default"] = {
        "tool": "time_get",
        "call_id": "call-1",
    }

    async def fake_stream(**kwargs):
        yield StreamChunk.text_delta("hello")
        await asyncio.sleep(0.06)
        yield StreamChunk.done("world")

    _install(monkeypatch, _Store(), fake_stream, heartbeat_interval=0.01)

    response = await conversation_router.approve_tool(
        conversation_router.ApproveRequest(user_id="u", call_id="call-1")
    )
    frames = await _consume(response)

    # Heartbeat keepalive pings interleaved during the silence gap.
    assert any(": ping" in line for line in frames), frames

    events = _parse_events(frames)
    assert all("type" in e and "data" in e for e in events), events
    assert [e["type"] for e in events if e["type"] in ("text_delta", "done")] == [
        "text_delta",
        "done",
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["result"]["response"] == "world"


@pytest.mark.asyncio
async def test_approve_stream_injected_exception_yields_parseable_error(monkeypatch):
    """A failure inside the resumed run surfaces as a parseable error event."""
    conversation_router._pending_interrupts["u:default"] = {
        "tool": "time_get",
        "call_id": "call-1",
    }

    async def fake_stream(**kwargs):
        yield StreamChunk.text_delta("partial")
        raise RuntimeError("boom")

    _install(monkeypatch, _Store(), fake_stream)

    response = await conversation_router.approve_tool(
        conversation_router.ApproveRequest(user_id="u", call_id="call-1")
    )
    frames = await _consume(response)

    errors = [e for e in _parse_events(frames) if e["type"] == "error"]
    assert errors, frames
    assert errors[0]["data"]["code"] == "error"
    assert "boom" in errors[0]["data"]["message"]


@pytest.mark.asyncio
async def test_approve_stream_empty_content_done_still_emits_done(monkeypatch):
    """A resumed run finishing with empty content must still emit a done event.

    Regression: the done branch was gated on `if event.content`, so an empty
    final turn vanished entirely and the client never saw a terminal event.
    """
    conversation_router._pending_interrupts["u:default"] = {
        "tool": "time_get",
        "call_id": "call-1",
    }

    async def fake_stream(**kwargs):
        yield StreamChunk.done()

    _install(monkeypatch, _Store(), fake_stream)

    response = await conversation_router.approve_tool(
        conversation_router.ApproveRequest(user_id="u", call_id="call-1")
    )
    frames = await _consume(response)

    done = [e for e in _parse_events(frames) if e["type"] == "done"]
    assert done, f"terminal event vanished for empty-content run: {frames}"
