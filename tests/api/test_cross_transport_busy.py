"""Cross-transport session serialization + wired cancel (audit E26/E4).

Before this fix, conversation.py and ws.py each built their own
``SessionWorkerRegistry``, so a WS run and an SSE run on the same
(user_id, session_id) never saw each other's locks and executed concurrently
on the SAME cached AgentLoop. ``cancel_message`` also never signalled the
registry, so non-streaming runs ignored cancellation, and ``delete_session``
left ChromaDB vectors + ``_journal`` rows behind (ghost recall).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.http.routers import conversation as conversation_router
from src.http.routers import ws as ws_router
from src.sdk.session_worker import (
    SessionBusyError,
    get_session_registry,
    session_key,
)


@pytest.mark.asyncio
async def test_registries_are_process_global_singleton():
    """E26 core: both transports must share ONE registry instance."""
    assert conversation_router._session_registry is get_session_registry()
    assert ws_router._session_registry is get_session_registry()


@pytest.mark.asyncio
async def test_lock_acquired_via_one_transport_blocks_the_other():
    """A lock held under the canonical key is visible to every caller."""
    reg = get_session_registry()
    key = session_key("u1", "chat-1")
    lock = await reg.acquire(key)
    try:
        with pytest.raises(SessionBusyError):
            await reg.acquire(key)  # WS-side acquire of an SSE-held session
        assert reg.holds(key)
        # Different session unaffected.
        assert not reg.holds(session_key("u1", "other"))
    finally:
        await reg.release(key)


@pytest.mark.asyncio
async def test_cancel_message_requests_cancellation_via_registry(monkeypatch):
    """cancel_message must flow through SessionWorkerRegistry.stop so
    NON-STREAMING runs actually observe cancellation."""
    reg = get_session_registry()
    key = session_key("title_user", "sess-cancel")
    lock = await reg.acquire(key)
    monkeypatch.setattr(
        conversation_router, "reset_sdk_loop", lambda *a, **k: None
    )
    req = conversation_router.CancelRequest(user_id="title_user", session_id="sess-cancel")
    resp = await conversation_router.cancel_message(req)
    assert resp["status"] == "cancelled"
    assert lock.cancelled, "cancel must reach the held SessionLock"
    await reg.release(key)


@pytest.mark.asyncio
async def test_approve_during_active_run_returns_409(monkeypatch):
    """Approve racing a fresh run maps SessionBusyError to 409, not 500."""
    reg = get_session_registry()
    key = session_key("title_user", "sess-busy")
    await reg.acquire(key)
    skey = conversation_router._stream_key("title_user", "sess-busy")
    conversation_router._pending_interrupts[skey] = {
        "tool": "files_delete",
        "call_id": "c1",
        "session_id": "sess-busy",
    }
    try:
        req = conversation_router.ApproveRequest(
            user_id="title_user", session_id="sess-busy", call_id="c1"
        )
        with pytest.raises(HTTPException) as ei:
            await conversation_router.approve_tool(req)
        assert ei.value.status_code == 409
    finally:
        conversation_router._pending_interrupts.pop(skey, None)
        await reg.release(key)


@pytest.mark.asyncio
async def test_delete_session_refuses_while_run_active(monkeypatch):
    reg = get_session_registry()
    key = session_key("title_user", "sess-del")
    lock = await reg.acquire(key)

    calls: list[str] = []

    class FakeStore:
        def delete_session(self, sid: str) -> int:
            calls.append(sid)
            return 3

    monkeypatch.setattr(
        conversation_router, "get_message_store", lambda uid: FakeStore()
    )
    try:
        with pytest.raises(HTTPException) as ei:
            await conversation_router.delete_session(
                user_id="title_user", session_id="sess-del"
            )
        assert ei.value.status_code == 409
        assert calls == [], "must not delete while a run holds the session"

        await reg.release(key)
        resp = await conversation_router.delete_session(
            user_id="title_user", session_id="sess-del"
        )
        assert resp["status"] == "deleted" and calls == ["sess-del"]
    finally:
        reg._locks.pop(key, None)


@pytest.mark.asyncio
async def test_approve_busy_preserves_pending_and_does_not_approve(monkeypatch):
    """F1: a 409 must not consume the pending interrupt nor mutate the loop."""
    reg = get_session_registry()
    key = session_key("title_user", "sess-busy2")
    await reg.acquire(key)
    skey = conversation_router._stream_key("title_user", "sess-busy2")
    conversation_router._pending_interrupts[skey] = {
        "tool": "files_delete",
        "call_id": "c1",
        "session_id": "sess-busy2",
        "args": {"path": "/x"},
    }
    approved: list[object] = []

    class FakeLoop:
        def approve_tool_call(self, tc):
            approved.append(tc)

    async def fake_get_sdk_loop(*a, **kw):
        return FakeLoop()

    monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
    try:
        req = conversation_router.ApproveRequest(
            user_id="title_user", session_id="sess-busy2", call_id="c1"
        )
        with pytest.raises(HTTPException) as ei:
            await conversation_router.approve_tool(req)
        assert ei.value.status_code == 409
        # Pending entry restored for the retry the 409 hint promises…
        assert conversation_router._pending_interrupts[skey]["call_id"] == "c1"
        # …and the cached loop was NOT told the tool was approved.
        assert approved == []
    finally:
        conversation_router._pending_interrupts.pop(skey, None)
        await reg.release(key)
