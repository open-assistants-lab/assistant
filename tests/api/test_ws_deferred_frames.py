"""Deferred control frames must still be answered (#146, follows #141).

A control frame that completes in the same `asyncio.wait` as the stream is
captured into `deferred_control`. The #141 fix consumed a deferred steer; every
other deferred frame — notably a ping — was still dropped, so a client pinging
as its stream ended got no pong and could treat the connection as dead.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from src.http.routers import ws as ws_router
from src.sdk.messages import StreamChunk
from tests.api.conftest import make_run_event_factory
from tests.api.test_ws_transport_b4 import (
    FakeConversation,
    FakeWebSocket,
    _settings,
)


@pytest.mark.asyncio
async def test_deferred_ping_is_answered_with_a_pong(monkeypatch):
    """user_message + ping, stream finishing in the same wait: expect a pong."""
    _settings(monkeypatch)
    prompts_seen: list[str] = []

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="ok")
        yield StreamChunk.done(content="ok")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        prompts_seen.append(kwargs.get("prompt"))
        async for e in base_fake(service_self, **kwargs):
            yield e

    conversation = FakeConversation()
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

    release = asyncio.Event()
    websocket = FakeWebSocket(
        [
            json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
            json.dumps({"type": "ping"}),
        ],
        release,
    )
    try:
        await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
    except (TimeoutError, WebSocketDisconnect):
        pass

    kinds = [m.get("type") for m in websocket.sent]
    assert "pong" in kinds, f"a deferred ping was dropped: {kinds}"


@pytest.mark.asyncio
async def test_deferred_steer_is_still_acked_and_queued(monkeypatch):
    """The #141 contract must not regress while fixing the ping path."""
    _settings(monkeypatch)
    prompts_seen: list[str] = []

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="ok")
        yield StreamChunk.done(content="ok")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        prompts_seen.append(kwargs.get("prompt"))
        if release is not None and len(prompts_seen) >= 2:
            release.set()
        async for e in base_fake(service_self, **kwargs):
            yield e

    conversation = FakeConversation()
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

    release = asyncio.Event()
    websocket = FakeWebSocket(
        [
            json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
            json.dumps({"type": "steer", "content": "change plan"}),
        ],
        release,
    )
    try:
        await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
    except (TimeoutError, WebSocketDisconnect):
        pass

    acks = [m for m in websocket.sent if m.get("type") == "steer_ack"]
    assert acks, "the deferred steer stopped being acknowledged"
    assert "change plan" in prompts_seen


@pytest.mark.asyncio
async def test_deferred_second_user_message_opens_the_next_turn(monkeypatch):
    """#149: a user message captured with the stream is a turn, not litter.

    The deferred drain handled ping and steer; a second user_message had no
    branch and was discarded when the slot was cleared, so the client's next
    instruction silently never ran.
    """
    _settings(monkeypatch)
    prompts_seen: list[str] = []

    async def chunk_gen(**kwargs):
        yield StreamChunk.text_delta(content="ok")
        yield StreamChunk.done(content="ok")

    base_fake = make_run_event_factory(chunk_gen)

    async def fake_execute_stream(service_self, **kwargs):
        prompts_seen.append(kwargs.get("prompt"))
        if release is not None and len(prompts_seen) >= 2:
            release.set()
        async for e in base_fake(service_self, **kwargs):
            yield e

    conversation = FakeConversation()
    monkeypatch.setattr(
        ws_router, "aget_message_store", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

    release = asyncio.Event()
    websocket = FakeWebSocket(
        [
            json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
            json.dumps({"type": "user_message", "content": "change plan", "user_id": "t"}),
        ],
        release,
    )
    try:
        await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
    except (TimeoutError, WebSocketDisconnect):
        pass

    assert prompts_seen[:2] == ["go", "change plan"], (
        f"the deferred user message was discarded: {prompts_seen}"
    )
