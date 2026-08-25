"""Legacy auth helpers, moved from the former `src/http/auth.py` module.

Kept as the shared-secret implementation primitives and re-exported from
the package `__init__` so existing callers (`require_auth`, `verify_key`,
`is_localhost`) keep working unchanged (migration hook, roadmap P0-T1).
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def verify_key(key: str) -> bool:
    """Check if the provided API key matches the configured key."""
    from src.config.settings import get_settings

    settings = get_settings()
    if not settings.auth.api_key:
        return True  # auth disabled — accept everything
    return _hash(key) == _hash(settings.auth.api_key)


def is_localhost(request: Request) -> bool:
    """Check if the request originates from localhost."""
    client = request.client
    if client is None:
        return False
    # Exact-match set (audit B17): includes the IPv4-mapped IPv6 form that
    # dual-stack binds report. Deliberately NO prefix matching — a broad
    # match would let LAN/proxied clients spoof the solo bypass.
    return client.host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


async def require_auth(request: Request) -> None:
    """FastAPI dependency. Require valid Bearer token unless auth is disabled.

    Flow:
      1. If API_KEY is empty → allow all (solo mode, auth disabled)
      2. If request is from localhost and solo_bypass is True → allow
      3. Otherwise → validate Bearer token against API_KEY
    """
    from src.config.settings import get_settings

    settings = get_settings()

    # Auth disabled — solo mode, no key configured
    if not settings.auth.api_key:
        return

    # Localhost bypass for multi-device WAN (desktop localhost still works)
    if settings.auth.solo_bypass and is_localhost(request):
        return

    # Validate Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    key = auth_header[7:]
    if not verify_key(key):
        raise HTTPException(status_code=401, detail="Invalid API key")
