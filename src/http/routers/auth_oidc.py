"""SSO via OIDC (Phase 3 T3.3): authorization-code flow with PKCE.

Routes (mounted ONLY when `oidc.enabled` — absent = 404, zero behavior
change when off):
  - GET /auth/oidc/login    state + PKCE verifier, redirect to the IdP
  - GET /auth/oidc/callback code exchange, id_token verification, session
  - GET /auth/oidc/logout   local session revoked (IdP revocation best-effort)

Claims -> identity: `preferred_username` -> `email` local-part -> `sub`
(sanitized to store-path-safe characters). Role resolved from the T3.1
TenancyStore membership; unaffiliated users are staff.
"""

from __future__ import annotations

import base64
import hashlib
import urllib.parse

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from src.app_logging import get_logger
from src.auth.oidc import SESSION_COOKIE, _store

logger = get_logger()
router = APIRouter(prefix="/auth/oidc", tags=["sso"])


class OidcError(Exception):
    """Any OIDC flow failure surfaced as 401 to the browser."""


# -- HTTP helpers (module-level so tests can stub them; no network in tests) --


def _http_get_json(url: str) -> dict[str, object]:
    import httpx

    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return dict(resp.json())


def _http_post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    import httpx

    resp = httpx.post(url, data=data, timeout=15)
    resp.raise_for_status()
    return dict(resp.json())


def _discovery(issuer: str) -> dict[str, object]:
    return _http_get_json(f"{issuer.rstrip('/')}/.well-known/openid-configuration")


def _verify_id_token(token: str, *, issuer: str, audience: str, client_secret: str, jwks_uri: str, nonce: str) -> dict[str, object]:
    """Verify signature + iss/aud/exp/nonce. RS256 via the IdP JWKS;
    HS256 (confidential-client symmetric) via the client_secret."""
    import jwt as pyjwt

    header = pyjwt.get_unverified_header(token)
    key_opts: dict[str, object] = {
        "audience": audience,
        "issuer": issuer,
        "options": {"require": ["exp", "iss", "aud"]},
    }
    if header.get("alg") == "HS256":
        claims = dict(
            pyjwt.decode(
                token,
                client_secret,
                algorithms=["HS256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        )
    else:
        jwks_client = pyjwt.PyJWKClient(jwks_uri, cache_keys=True)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = dict(
            pyjwt.decode(
                token,
                signing_key.key,
                algorithms=[header.get("alg", "RS256")],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        )
    if str(claims.get("nonce", "")) != nonce:
        raise OidcError("nonce mismatch")
    return dict(claims)


def _claims_to_user_id(claims: dict[str, object]) -> str:
    """Preferred username -> email local-part -> sub, store-path safe."""
    import re

    raw = (
        str(claims.get("preferred_username"))
        or str(claims.get("email", "")).split("@")[0]
        or str(claims.get("sub", ""))
    )
    if not raw:
        raise OidcError("id_token carries no usable identity claim")
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_").lower() or "oidc_user"


def _role_for(user_id: str) -> str:
    """T3.1 membership role; unaffiliated users are staff."""
    try:
        from src.storage.tenancy import get_tenancy_store

        return get_tenancy_store().role_of(user_id)
    except Exception:
        return "staff"


def _redirect_uri(request: Request) -> str:
    from src.config.settings import get_settings

    configured = get_settings().oidc.redirect_uri
    if configured:
        return configured
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/callback"


def _require_enabled() -> None:
    """Mounted-but-404 contract: flag off -> every route 404s (HTTPException)."""
    from fastapi import HTTPException

    from src.config.settings import get_settings

    if not get_settings().oidc.enabled:
        raise HTTPException(status_code=404, detail="not found")


@router.get("/login")
async def oidc_login(request: Request) -> Response:
    _require_enabled()
    try:
        return await _login(request)
    except OidcError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})


async def _login(request: Request) -> RedirectResponse:
    from src.config.settings import get_settings

    cfg = get_settings().oidc
    if not cfg.enabled or not cfg.issuer:
        return RedirectResponse("/", status_code=302)
    try:
        discovery = _discovery(cfg.issuer)
    except Exception as e:
        logger.error("oidc.discovery_failed", {"error_type": type(e).__name__})
        raise OidcError(f"IdP discovery failed: {type(e).__name__}") from e

    auth_ep = str(discovery.get("authorization_endpoint", ""))
    if not auth_ep:
        raise OidcError("IdP discovery has no authorization_endpoint")

    state, verifier, nonce = _store().create_pending()
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": cfg.client_id,
            "redirect_uri": _redirect_uri(request),
            "scope": cfg.scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{auth_ep}?{params}", status_code=302)


@router.get("/callback")
async def oidc_callback(request: Request) -> Response:
    _require_enabled()
    try:
        return await _callback(request)
    except OidcError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})


async def _callback(request: Request) -> RedirectResponse:
    from src.config.settings import get_settings

    cfg = get_settings().oidc
    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    pending = _store().pop_pending(state)
    if pending is None:
        raise OidcError("unknown or expired state")
    if not code:
        raise OidcError("authorization code missing")

    discovery = _discovery(cfg.issuer)
    token_ep = str(discovery.get("token_endpoint", ""))
    jwks_uri = str(discovery.get("jwks_uri", ""))
    redirect_uri = _redirect_uri(request)

    try:
        token_resp = _http_post_form(
            token_ep,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "code_verifier": str(pending["verifier"]),
            },
        )
    except Exception as e:
        logger.error("oidc.token_exchange_failed", {"error_type": type(e).__name__})
        raise OidcError("token exchange failed") from e

    id_token = str(token_resp.get("id_token", ""))
    if not id_token:
        raise OidcError("token response carries no id_token")

    try:
        claims = _verify_id_token(
            id_token,
            issuer=cfg.issuer,
            audience=cfg.client_id,
            client_secret=cfg.client_secret,
            jwks_uri=jwks_uri,
            nonce=str(pending["nonce"]),
        )
    except OidcError:
        raise
    except Exception as e:
        logger.error("oidc.id_token_invalid", {"error_type": type(e).__name__})
        raise OidcError("id_token verification failed") from e

    user_id = _claims_to_user_id(claims)
    role = _role_for(user_id)
    sid = _store().create_session(user_id, role)
    logger.info(
        "oidc.session_established", {"role": role}, user_id=user_id
    )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="lax",
        max_age=int(cfg.session_hours * 3600),
    )
    return resp


@router.get("/logout")
async def oidc_logout(request: Request) -> RedirectResponse:
    sid = request.cookies.get(SESSION_COOKIE)
    _store().revoke_session(sid)
    # IdP-side revocation is best-effort and never blocks (plan T3.3).
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
