"""SSO via OIDC (Phase 3 T3.3): session store + IdentityResolver.

The resolver implements the Phase 0 `IdentityResolver` seam: a valid OIDC
session cookie maps to `UserIdentity(user_id, trust_domain="untrusted")`
with the role from the T3.1 TenancyStore membership (staff fallback).
Flag-off (default) means the resolver is never installed — zero behavior
change.
"""

from __future__ import annotations

import secrets
import threading
import time

from fastapi import Request

from src.http.auth.resolver import UserIdentity

SESSION_COOKIE = "assistant_oidc_sid"


def _now() -> float:
    return time.time()


class OidcSessions:
    """In-process OIDC session + login-pending store.

    Sessions are per-process (single-writer per user store — AGENTS.md rule);
    a restart logs browser sessions out (the IdP session survives, users
    re-authenticate transparently).
    """

    def __init__(self, session_hours: float = 8.0) -> None:
        self.session_hours = session_hours
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, str | float]] = {}
        self._pending: dict[str, dict[str, str | float]] = {}

    # -- login pendings (state -> PKCE verifier + nonce) -------------------

    def create_pending(self) -> tuple[str, str, str]:
        """Create a login pending. Returns (state, code_verifier, nonce)."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        nonce = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[state] = {
                "verifier": verifier,
                "nonce": nonce,
                "created": _now(),
            }
        return state, verifier, nonce

    def pop_pending(self, state: str) -> dict[str, str | float] | None:
        """Consume a pending (single-use; forged/unknown states fail)."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None:
            return None
        # Pending entries are single-use and short-lived (10 min).
        if _now() - float(pending["created"]) > 600:
            return None
        return pending

    # -- sessions ----------------------------------------------------------

    def create_session(self, user_id: str, role: str) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = {
                "user_id": user_id,
                "role": role,
                "expires": _now() + self.session_hours * 3600,
            }
        return sid

    def get_session(self, sid: str | None) -> dict[str, str | float] | None:
        if not sid:
            return None
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return None
        if _now() - 0 > float(session["expires"]):
            self.revoke_session(sid)
            return None
        return session

    def revoke_session(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(sid, None)


_STORE: OidcSessions | None = None


def _store() -> OidcSessions:
    """Process-wide session store (singleton — pendings and sessions must
    share one map; per-user stores remain the data truth)."""
    global _STORE
    if _STORE is None:
        from src.config.settings import get_settings

        _STORE = OidcSessions(session_hours=get_settings().oidc.session_hours)
    return _STORE


class OidcResolver:
    """Resolve requests via the OIDC session cookie, else the base chain."""

    def __init__(self, fallback: object) -> None:
        self._fallback = fallback

    def resolve(self, request: Request) -> UserIdentity | None:
        from src.config.settings import get_settings

        settings = get_settings()
        if not settings.oidc.enabled:
            fallback = self._fallback
            resolve = getattr(fallback, "resolve")
            fallback_result: UserIdentity | None = resolve(request)
            return fallback_result

        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            session = _store().get_session(sid)
            if session is not None:
                return UserIdentity(
                    user_id=str(session["user_id"]),
                    key_id="oidc",
                    trust_domain="untrusted",
                    role=str(session["role"]),
                )
        # No/expired session: the deployment's base auth applies (shared
        # secret for admins, per-user keys, localhost solo bypass).
        fallback = self._fallback
        resolve = getattr(fallback, "resolve")
        base_result: UserIdentity | None = resolve(request)
        return base_result
