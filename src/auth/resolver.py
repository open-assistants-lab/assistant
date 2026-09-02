"""Per-user API key IdentityResolver (Phase 2 M2.1).

Production hosted-tier resolver: `Authorization: Bearer <oak_...>` keys map
1:1 to identities. Active only when `auth.per_user_auth` is enabled — the
SharedSecretResolver path is untouched when the flag is off.

Contract (M2.1):
  - valid key    -> UserIdentity(user_id=<key owner>, trust_domain="untrusted")
  - no/invalid key on non-localhost -> None (middleware 401s)
  - no key on localhost  -> fall back to solo (localhost bypass preserved)
"""

from __future__ import annotations

from fastapi import Request

from src.http.auth.legacy import is_localhost
from src.http.auth.resolver import UserIdentity
from src.http.auth.shared_secret import SharedSecretResolver


class PerUserKeyResolver:
    """Resolve requests against per-user generated API keys."""

    def __init__(self) -> None:
        self._fallback = SharedSecretResolver()

    def resolve(self, request: Request) -> UserIdentity | None:
        from src.auth.keys import get_key_store
        from src.config.settings import get_settings

        settings = get_settings()

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            verified = get_key_store().verify(auth_header[7:])
            if verified is not None:
                user_id, scopes = verified
                return UserIdentity(
                    user_id=user_id,
                    key_id="oak",
                    trust_domain="untrusted",
                    scopes=tuple(
                        s.strip() for s in (scopes or "").split(",") if s.strip()
                    ),
                )
            # Not a per-user key. Fall through to the shared-secret path:
            # the deployment's API_KEY (admin) and localhost-bypass contracts
            # stay intact when per-user auth is on. Misses -> None (401).
            return self._fallback.resolve(request)

        # No key presented. Localhost keeps the solo bypass (dev tooling,
        # the native app, tests). Non-localhost requires a key -> 401.
        if is_localhost(request):
            return self._fallback.resolve(request)

        if not settings.auth.api_key and not settings.auth.per_user_auth:
            return self._fallback.resolve(request)

        return None
