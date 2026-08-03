"""Session worker registry — one async worker per active session.

Serializes same-session runs. Different sessions run concurrently.
"""

from __future__ import annotations

import asyncio


class SessionLock:
    """Exclusive session lock with cancellation support."""

    def __init__(self) -> None:
        self._cancel_event = asyncio.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()


class SessionBusyError(Exception):
    """Raised when a session already has an active run."""


class SessionWorkerRegistry:
    """One async worker per active session. Serializes same-session runs."""

    def __init__(self) -> None:
        self._locks: dict[str, SessionLock] = {}
        self._mutex = asyncio.Lock()

    async def acquire(self, session_id: str) -> SessionLock:
        """Acquire exclusive session lock. Raises SessionBusy if held."""
        async with self._mutex:
            if session_id in self._locks:
                raise SessionBusyError(f"Session {session_id} already has an active run")
            lock = SessionLock()
            self._locks[session_id] = lock
            return lock

    async def release(self, session_id: str) -> None:
        """Release session lock."""
        async with self._mutex:
            self._locks.pop(session_id, None)

    async def stop(self, session_id: str) -> None:
        """Request cancellation of the active run in this session."""
        async with self._mutex:
            lock = self._locks.get(session_id)
        if lock is not None:
            lock.request_cancel()

    @property
    def active_sessions(self) -> frozenset[str]:
        return frozenset(self._locks)
