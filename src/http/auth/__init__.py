"""Identity resolution seam (roadmap P0-T1).

The resolver is the single enforcement point for authentication: every
HTTP/WS request resolves to a `UserIdentity` (or None = unauthenticated).
Reference implementations live in `shared_secret.py`; production per-user
key->identity mapping lands in Phase 2 (hosted tier).

This package replaces the former `src/http/auth.py` module; the legacy
helpers (`verify_key`, `is_localhost`, `require_auth`) are re-exported
here so existing callers keep working unchanged.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.http.auth.legacy import is_localhost, require_auth, verify_key
from src.http.auth.resolver import IdentityResolver, UserIdentity
from src.http.auth.shared_secret import SharedSecretResolver

__all__ = [
    "IdentityResolver",
    "UserIdentity",
    "SharedSecretResolver",
    "enforce_user_id",
    "get_resolver",
    "is_localhost",
    "require_auth",
    "verify_key",
]

def enforce_user_id(request_user_id: str, resolved: UserIdentity | None) -> None:
    """Raise 403 when an authenticated request targets a different user.

    Roadmap P0-T2: every router keeps accepting `user_id` for backward-compat
    (solo mode), but when a resolver is active (trusted/untrusted domain) the
    request's `user_id` must match the resolved identity's `user_id`. Solo
    identities are exempt (no auth configured / localhost bypass) — behavior
    unchanged there.
    """
    if resolved is None or resolved.trust_domain == "solo":
        return
    if resolved.user_id is None:
        # Authenticated but the resolver can't scope to a user (e.g. the
        # shared-secret reference impl — one key per deployment). Enforcement
        # activates when a per-user resolver (Phase 2 keys) is plugged in.
        return
    if resolved.user_id != request_user_id:
        raise HTTPException(
            status_code=403,
            detail="user_id does not match authenticated identity",
        )


_DEFAULT_RESOLVER: IdentityResolver | None = None


def get_resolver() -> IdentityResolver:
    """Return the active IdentityResolver (default: shared-secret impl).

    The seam is the single enforcement point: middleware and routers call
    this instead of inlining auth logic. Phase 2 swaps in the per-user key
    resolver here.
    """
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = SharedSecretResolver()
    return _DEFAULT_RESOLVER
