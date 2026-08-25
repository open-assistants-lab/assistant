"""Session worker registry — one async worker per active session.

Serializes same-session runs. Different sessions run concurrently.
"""

from __future__ import annotations

import asyncio


def session_key(user_id: str, session_id: str | None) -> str:
    """Canonical registry key shared by ALL callers (audit E26 hazard #2).

    RunService and both routers previously formatted divergent keys
    ("user::session" vs "user:session-or-default"), so registry.stop() from a
    router silently missed the RunService-registered lock. Use this builder
    for every acquire/release/holds/stop call.
    """
    return f"{user_id}::{session_id or 'default'}"


_registry: SessionWorkerRegistry | None = None


def get_session_registry() -> SessionWorkerRegistry:
    """Process-global registry (audit E26).

    A module-level singleton so REST/SSE and WS serialize the same session
    on the same lock. The class stays public for tests that want isolated
    instances.
    """
    global _registry
    if _registry is None:
        _registry = SessionWorkerRegistry()
    return _registry


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

    def holds(self, session_id: str) -> bool:
        """Synchronous advisory check: is a lock currently held for this key?

        Atomic within the event loop (no await inside). Advisory only — the
        authoritative check remains ``acquire`` (audit B12 lets HTTP endpoints
        probe BEFORE mutating cancel/slot dicts so a doomed request cannot
        clobber the live stream's registration).
        """
        return session_id in self._locks

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

    async def stop_user_sessions(self, user_id: str) -> list[str]:
        """Cancel every active run belonging to user_id (E26 detach).

        Used by profile-change lifecycle (roadmap P0-T7): a mid-session profile
        swap must never leave a stale loop serving an approved turn.
        Returns the canonical keys that were cancelled.
        """
        prefix = f"{user_id}::"
        async with self._mutex:
            keys = [k for k in self._locks if k.startswith(prefix)]
            locks = [(k, self._locks[k]) for k in keys]
        cancelled = []
        for k, lock in locks:
            lock.request_cancel()
            cancelled.append(k)
        return cancelled
