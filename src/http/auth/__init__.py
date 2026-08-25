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

from fastapi import Request

from src.http.auth.legacy import is_localhost, require_auth, verify_key
from src.http.auth.resolver import IdentityResolver, UserIdentity
from src.http.auth.shared_secret import SharedSecretResolver

__all__ = [
    "IdentityResolver",
    "UserIdentity",
    "SharedSecretResolver",
    "get_resolver",
    "is_localhost",
    "require_auth",
    "verify_key",
]

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
