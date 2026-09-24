"""Session worker registry — one async worker per active session.

Serializes same-session runs. Different sessions run concurrently.
"""

from __future__ import annotations

import asyncio
from time import monotonic


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
        try:
            from src.config import get_settings

            timeout = get_settings().deployment.session_lease_timeout_seconds
        except Exception:
            timeout = 300
        _registry = SessionWorkerRegistry(lease_timeout_seconds=timeout)
    return _registry


class SessionLock:
    """Exclusive session lock with cancellation support."""

    def __init__(self) -> None:
        self._cancel_event = asyncio.Event()
        self._last_activity = monotonic()

    def touch(self) -> None:
        self._last_activity = monotonic()

    @property
    def idle_seconds(self) -> float:
        return max(0.0, monotonic() - self._last_activity)

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()


class SessionBusyError(Exception):
    """Raised when a session already has an active run."""


class SessionWorkerRegistry:
    """One async worker per active session. Serializes same-session runs."""

    def __init__(self, lease_timeout_seconds: float = 300.0) -> None:
        self._locks: dict[str, SessionLock] = {}
        self._mutex = asyncio.Lock()
        self._lease_timeout_seconds = max(1.0, float(lease_timeout_seconds))
        self._stale_requested: set[str] = set()

    async def acquire(self, session_id: str) -> SessionLock:
        """Acquire exclusive session lock. Raises SessionBusy if held."""
        await self.reap_stale(self._lease_timeout_seconds)
        async with self._mutex:
            if session_id in self._locks:
                raise SessionBusyError(f"Session {session_id} already has an active run")
            lock = SessionLock()
            self._locks[session_id] = lock
            self._stale_requested.discard(session_id)
            return lock

    async def reap_stale(self, max_idle_seconds: float) -> list[str]:
        """Request cancellation for idle locks without releasing ownership."""
        threshold = max(0.0, float(max_idle_seconds))
        async with self._mutex:
            stale = [
                session_id
                for session_id, lock in self._locks.items()
                if session_id not in self._stale_requested
                and lock.idle_seconds > threshold
            ]
            for session_id in stale:
                self._stale_requested.add(session_id)
        for session_id in stale:
            async with self._mutex:
                lock = self._locks.get(session_id)
            if lock is not None:
                lock.request_cancel()
        return stale

    async def touch(self, session_id: str) -> None:
        async with self._mutex:
            lock = self._locks.get(session_id)
            if lock is not None:
                lock.touch()
                self._stale_requested.discard(session_id)

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
            self._stale_requested.discard(session_id)

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
