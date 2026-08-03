"""Tests for SessionWorkerRegistry and SessionLock."""

from __future__ import annotations

import pytest

from src.sdk.session_worker import SessionBusyError, SessionLock, SessionWorkerRegistry


class TestSessionLock:
    def test_not_cancelled_by_default(self) -> None:
        lock = SessionLock()
        assert not lock.cancelled

    def test_request_cancel_sets_flag(self) -> None:
        lock = SessionLock()
        lock.request_cancel()
        assert lock.cancelled

    def test_cancel_is_idempotent(self) -> None:
        lock = SessionLock()
        lock.request_cancel()
        lock.request_cancel()
        assert lock.cancelled


class TestSessionWorkerRegistry:
    def test_acquire_release_cycle(self) -> None:
        registry = SessionWorkerRegistry()
        lock = registry.acquire("chat-1")
        assert "chat-1" in registry.active_sessions
        registry.release("chat-1")
        assert "chat-1" not in registry.active_sessions

    def test_second_acquire_raises_session_busy(self) -> None:
        registry = SessionWorkerRegistry()
        registry.acquire("chat-1")
        with pytest.raises(SessionBusyError, match="already has an active run"):
            registry.acquire("chat-1")

    def test_different_sessions_acquire_independently(self) -> None:
        registry = SessionWorkerRegistry()
        lock_a = registry.acquire("chat-1")
        lock_b = registry.acquire("chat-2")
        assert "chat-1" in registry.active_sessions
        assert "chat-2" in registry.active_sessions
        assert lock_a is not lock_b

    def test_stop_sets_cancel_on_active_lock(self) -> None:
        registry = SessionWorkerRegistry()
        lock = registry.acquire("chat-1")
        assert not lock.cancelled
        registry.stop("chat-1")
        assert lock.cancelled

    def test_stop_nonexistent_session_does_nothing(self) -> None:
        registry = SessionWorkerRegistry()
        registry.stop("chat-1")  # should not raise

    def test_release_after_stop_works(self) -> None:
        registry = SessionWorkerRegistry()
        registry.acquire("chat-1")
        registry.stop("chat-1")
        registry.release("chat-1")
        assert "chat-1" not in registry.active_sessions

    def test_active_sessions_reflects_current_locks(self) -> None:
        registry = SessionWorkerRegistry()
        assert registry.active_sessions == frozenset()
        registry.acquire("chat-1")
        assert registry.active_sessions == frozenset({"chat-1"})
        registry.acquire("chat-2")
        assert registry.active_sessions == frozenset({"chat-1", "chat-2"})
        registry.release("chat-1")
        assert registry.active_sessions == frozenset({"chat-2"})
