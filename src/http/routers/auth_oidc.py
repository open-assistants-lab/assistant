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
import json
import urllib.parse
from pathlib import Path

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


def _claim_bindings_path() -> Path:
    from pathlib import Path as _Path

    from src.config.settings import get_settings as _gs

    return _Path(_gs().deployment.data_path) / "oidc_user_bindings.json"


def _claim_bindings() -> dict[str, str]:
    """Subject -> user_id bindings. Missing file = no bindings yet."""
    try:
        return dict(json.loads(_claim_bindings_path().read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("oidc_bindings.unreadable", {})
        return {}


def _save_claim_bindings(bindings: dict[str, str]) -> None:
    import os as _os

    path = _claim_bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = _os.open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
    with _os.fdopen(fd, "w") as f:
        json.dump(bindings, f, sort_keys=True)
    _os.replace(tmp, path)


def claims_bind_user(claims: dict[str, object]) -> str:
    """Map a token's claims to a stable user_id without merging accounts.

    The old resolver preferred preferred_username, then the email local-part,
    then sub — after normalisation. Distinct IdP subjects that share a
    username or email local-part (different realms, matching display names)
    therefore collapsed into one user scope, blending two people's data and
    sessions (issue #120).

    Now the IdP subject "sub" is the identity anchor, and the readable name
    is only the FIRST user_id a subject was bound to:
      - new subject -> user_id from username/email/sub (as before)
      - same subject -> its existing user_id, even if the username changes
      - a DIFFERENT subject claiming a name already bound to another subject
        is refused rather than merged
    """
    import re as _re

    sub = str(claims.get("sub") or "")
    if not sub:
        raise OidcError("id_token carries no stable subject claim (sub)")

    raw = (
        str(claims.get("preferred_username") or "")
        or str(claims.get("email") or "").split("@")[0]
        or sub
    )
    if not raw or raw == "None":
        raise OidcError("id_token carries no usable identity claim")
    candidate = _re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_").lower() or "oidc_user"

    bindings = _claim_bindings()
    existing_user = bindings.get(f"sub:{sub}")
    if existing_user:
        return existing_user

    bound_subject = bindings.get(f"user:{candidate}")
    if bound_subject is not None and bound_subject != sub:
        raise OidcError(
            "this account's name is already bound to a different identity; "
            "login refused rather than merging two accounts"
        )
    bindings[f"sub:{sub}"] = candidate
    bindings[f"user:{candidate}"] = sub
    _save_claim_bindings(bindings)
    get_logger().info("oidc.identity_bound", {"subject": "ok"}, user_id=candidate)
    return candidate


def _claims_to_user_id(claims: dict[str, object]) -> str:
    """Backward-compatible name kept for import sites; binds via claims_bind_user."""
    return claims_bind_user(claims)


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
    resp = RedirectResponse(f"{auth_ep}?{params}", status_code=302)
    # T3.3 review P1: bind the flow to the initiating browser (login CSRF).
    # The callback must see the same state in the cookie and the query.
    _set_state_cookie(resp, state, cfg.session_hours)
    return resp


def _set_state_cookie(resp: Response, state: str, session_hours: float) -> None:
    resp.set_cookie(
        "oidc_state",
        state,
        httponly=True,
        samesite="lax",
        max_age=int(session_hours * 3600),
        secure=_cookie_secure(),
    )


def _cookie_secure() -> bool:
    """Secure cookies by default; plain-HTTP localhost dev opts out."""
    import os

    return os.environ.get("OIDC_COOKIE_SECURE", "true").lower() not in (
        "false",
        "0",
        "no",
    )


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
    # T3.3 review P1 (login CSRF): the callback must carry the same state
    # value the initiating browser stored in its oidc_state cookie.
    cookie_state = request.cookies.get("oidc_state", "")
    if not state or not cookie_state or state != cookie_state:
        raise OidcError("state mismatch between browser and flow")
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
        secure=_cookie_secure(),  # #121: was missing - state had it, session didn't
    )
    return resp


@router.get("/logout")
async def oidc_logout(request: Request) -> Response:
    _require_enabled()  # P2a: flag-off = 404, same as login/callback
    sid = request.cookies.get(SESSION_COOKIE)
    _store().revoke_session(sid)
    _store().revoke_session(sid)
    # IdP-side revocation is best-effort and never blocks (plan T3.3).
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    resp.delete_cookie("oidc_state")
    return resp


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
