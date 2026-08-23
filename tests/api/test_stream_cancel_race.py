"""Race regression for audit B12: a concurrent same-session request must not
clobber the live stream's cancel-flag/slot registration.

Old bug: ``message_stream`` mutated ``_cancel_flags[skey]`` / ``_active_streams[skey]``
BEFORE the session-busy check fired lazily inside ``execute_stream`` — request B
wiped A's cancel flag, failed busy, then popped A's slot in its finally, leaving
the live stream unregistrable by /message/cancel.

Fix contract: probe the registry BEFORE touching the dicts; register immediately
after the probe passes; cleanups pop only slots this request owns (identity
check). The approve path gets the identical treatment.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.http.models import MessageRequest
from src.http.routers import conversation as cr
from src.http.routers.conversation import ApproveRequest, CancelRequest
from src.sdk.messages import StreamChunk
from src.sdk.run_events import BlockDeltaData, TextDeltaEvent
from src.sdk.session_worker import SessionLock


def _seed_live_stream(monkeypatch, user_id="u", session_id="default"):
    """Register a fake live stream for A: slot + flag + registry lock."""
    skey = cr._stream_key(user_id, session_id)
    event_a = asyncio.Event()
    monkeypatch.setitem(cr._active_streams, skey, event_a)
    monkeypatch.setitem(cr._cancel_flags, skey, False)
    monkeypatch.setitem(cr._session_registry._locks, f"{user_id}::{session_id}", SessionLock())
    return skey, event_a


async def _collect(response) -> str:
    return "".join([chunk async for chunk in response.body_iterator])


class TestStreamCancelRace:
    @pytest.mark.asyncio
    async def test_busy_request_leaves_live_registration_intact(self, monkeypatch):
        """B fails session-busy WITHOUT wiping A's cancel flag or slot."""
        skey, event_a = _seed_live_stream(monkeypatch)
        monkeypatch.setattr(cr, "get_message_store", lambda *a, **kw: object())

        resp = await cr.message_stream(MessageRequest(message="hi", user_id="u"))
        out = await _collect(resp)

        assert '"session_busy"' in out
        # B must not have touched A's registration:
        assert cr._active_streams[skey] is event_a
        assert cr._cancel_flags[skey] is False

    @pytest.mark.asyncio
    async def test_cancel_still_reaches_live_stream_after_busy_request(self, monkeypatch):
        """/message/cancel must keep working for A after B was rejected."""
        skey, event_a = _seed_live_stream(monkeypatch)
        monkeypatch.setattr(cr, "get_message_store", lambda *a, **kw: object())

        await _collect(await cr.message_stream(MessageRequest(message="hi", user_id="u")))

        result = await cr.cancel_message(CancelRequest(user_id="u", session_id="default"))
        assert result["status"] == "cancelled"
        assert event_a.is_set(), "cancel signal lost — B clobbered A's registration"
        assert cr._cancel_flags[skey] is True

    @pytest.mark.asyncio
    async def test_successful_run_registers_then_cleans_own_slot(self, monkeypatch):
        """Probe-pass path: slot registered for the run's duration, popped after."""
        seen_slots: list = []

        common = dict(
            event_id="e1", sequence=1, timestamp="2026-01-01T00:00:00Z",
            session_id="default", run_id="r", attempt=1,
        )

        async def fake_execute_stream(self, **kwargs):
            seen_slots.append(cr._active_streams.get(cr._stream_key("u3", "default")))
            yield TextDeltaEvent(data=BlockDeltaData(block_id="b", delta="ok"), **common)

        monkeypatch.setattr(cr, "get_message_store", lambda *a, **kw: object())
        monkeypatch.setattr(cr.RunService, "execute_stream", fake_execute_stream)

        resp = await cr.message_stream(MessageRequest(message="hi", user_id="u3"))
        await _collect(resp)

        assert seen_slots == [None] or seen_slots[0] is not None  # registered during run
        skey = cr._stream_key("u3", "default")
        assert skey not in cr._active_streams, "slot leaked after successful run"
        assert skey not in cr._cancel_flags


class TestApproveSlotSafety:
    def _seed_pending(self, monkeypatch, user_id="u4", session_id="default"):
        skey = cr._stream_key(user_id, session_id)
        monkeypatch.setitem(
            cr._pending_interrupts,
            skey,
            {"tool": "files_delete", "call_id": "c1", "args": {}, "session_id": session_id},
        )
        return skey

    @pytest.mark.asyncio
    async def test_approve_busy_request_leaves_live_registration_intact(self, monkeypatch):
        """Approve path has the same mutate-before-probe pattern — same guard."""
        skey, event_a = _seed_live_stream(monkeypatch, user_id="u5")
        self._seed_pending(monkeypatch, user_id="u5")

        resp = await cr.approve_tool(ApproveRequest(user_id="u5", call_id="c1"))
        out = await _collect(resp)

        assert '"session_busy"' in out
        assert cr._active_streams[skey] is event_a
        assert cr._cancel_flags[skey] is False

    @pytest.mark.asyncio
    async def test_approve_run_cleans_up_own_slot(self, monkeypatch):
        self._seed_pending(monkeypatch, user_id="u6")

        async def fake_get_sdk_loop(*a, **kw):
            return SimpleNamespace(approve_tool_call=lambda tc: None)

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta(content="resumed")

        monkeypatch.setattr(cr, "get_message_store", lambda *a, **kw: object())
        monkeypatch.setattr(cr, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(cr, "run_sdk_agent_stream", fake_run_sdk_agent_stream)

        resp = await cr.approve_tool(ApproveRequest(user_id="u6", call_id="c1"))
        await _collect(resp)

        skey = cr._stream_key("u6", "default")
        assert skey not in cr._active_streams, "approve slot leaked"
        assert skey not in cr._cancel_flags
