"""Admin API for per-user API key generation (Phase 2 M2.1).

POST /auth/keys — admin-gated (require_auth dependency: API_KEY when
configured; solo/localhost policy unchanged). Returns the generated key
plaintext EXACTLY ONCE (only the SHA-256 hash is stored).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.http.auth.legacy import require_auth
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/auth", tags=["auth"])


class KeyCreateRequest(BaseModel):
    """Generate a per-user API key (admin only)."""

    user_id: str = Field(default=DEFAULT_USER_ID)
    scopes: str = Field(default="", description="Comma-separated scopes")
    revoke: str = Field(default="", description="Plaintext key to revoke")


@router.post("/keys")
async def manage_keys(body: KeyCreateRequest, request: Request) -> dict[str, object]:
    from src.auth.keys import get_key_store
    from src.config.settings import get_settings

    await require_auth(request)
    if body.revoke:
        revoked = get_key_store().revoke(body.revoke)
        return {"revoked": revoked}

    settings = get_settings()
    if not settings.auth.api_key and not is_localhost_request(request):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Invalid API key")

    plaintext = get_key_store().generate(body.user_id, body.scopes)
    return {
        "user_id": body.user_id,
        "key": plaintext,
        "scopes": body.scopes,
        "note": "Store this key now — it is not retrievable later.",
    }


def is_localhost_request(request: Request) -> bool:
    from src.http.auth.legacy import is_localhost

    return is_localhost(request)
