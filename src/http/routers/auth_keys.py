"""Admin API for per-user API key generation (Phase 2 M2.1).

POST /auth/keys — admin-gated (require_auth dependency: API_KEY when
configured; solo/localhost policy unchanged). Returns the generated key
plaintext EXACTLY ONCE (only the SHA-256 hash is stored).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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
    # T3.2 review P1-2: key minting/revocation is a deployment-admin act.
    # Per-user key holders (trust_domain == "untrusted") must NOT mint keys —
    # an admin-scope key for any user_id is full escalation (org CRUD,
    # cross-user pending approval, plan changes, impersonation).
    # Trusted: the API_KEY holder (trusted-network shared secret), solo mode,
    # or localhost (operator on the box).
    from src.config.settings import get_settings as _gs

    if _gs().auth.per_user_auth:
        identity = getattr(getattr(request, "state", None), "identity", None)
        trust = getattr(identity, "trust_domain", None)
        from src.http.auth.legacy import is_localhost

        if trust not in (None, "solo", "trusted-network") and not is_localhost(request):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "forbidden",
                    "message": (
                        "key minting/revocation requires the deployment "
                        "admin (API_KEY holder); per-user keys cannot mint keys"
                    ),
                },
            )
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
