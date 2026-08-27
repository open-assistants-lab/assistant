"""Shared-secret reference IdentityResolver (roadmap P0-T1).

Preserves the pre-seam behavior exactly:
  - no API_KEY configured -> solo mode, everything allowed
  - localhost + solo_bypass -> allowed (solo identity)
  - otherwise -> Bearer token must match API_KEY
"""

from __future__ import annotations

from fastapi import Request

from src.http.auth.legacy import is_localhost, verify_key
from src.http.auth.resolver import UserIdentity
from src.storage.paths import DEFAULT_USER_ID


class SharedSecretResolver:
    """Resolve requests against the single shared API_KEY (trusted network).

    This is the reference implementation of `IdentityResolver` and the
    default for OSS deployments. Production per-user key->identity mapping
    replaces it in Phase 2 (hosted tier).
    """

    def resolve(self, request: Request) -> UserIdentity | None:
        from src.config.settings import get_settings

        settings = get_settings()

        # Auth disabled — solo mode, no key configured.
        if not settings.auth.api_key:
            return UserIdentity(
                user_id=DEFAULT_USER_ID, key_id=None, trust_domain="solo"
            )

        # Localhost bypass for multi-device WAN (desktop localhost still works).
        if settings.auth.solo_bypass and is_localhost(request):
            return UserIdentity(
                user_id=DEFAULT_USER_ID, key_id=None, trust_domain="solo"
            )

        # Validate Bearer token.
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        if not verify_key(auth_header[7:]):
            return None
        return UserIdentity(
            user_id=None, key_id="shared-secret", trust_domain="trusted-network"
        )
