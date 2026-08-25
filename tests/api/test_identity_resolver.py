"""IdentityResolver seam tests (roadmap P0-T1).

Covers the resolver protocol, the shared-secret reference implementation,
and the middleware wiring (resolver is the single enforcement point).
"""

import pytest


def _reload_with(monkeypatch, api_key: str | None, solo_bypass: str | None):
    from src.config import reload_settings

    if api_key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", api_key)
    if solo_bypass is None:
        monkeypatch.delenv("SOLO_BYPASS", raising=False)
    else:
        monkeypatch.setenv("SOLO_BYPASS", solo_bypass)
    reload_settings()


def _make_request(headers: dict[str, str] | None = None, host: str = "127.0.0.1"):
    from starlette.requests import Request
    from starlette.types import Scope

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/conversation",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (host, 12345),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "root_path": "",
        "app": None,
    }
    return Request(scope)


def test_resolve_solo_no_key(monkeypatch):
    """No API_KEY configured -> solo identity, never None."""
    from src.http.auth import get_resolver

    _reload_with(monkeypatch, api_key=None, solo_bypass=None)
    resolver = get_resolver()
    identity = resolver.resolve(_make_request())
    assert identity is not None
    assert identity.trust_domain == "solo"
    assert identity.key_id is None


def test_resolve_trusted_valid_key(monkeypatch):
    """API_KEY set + valid Bearer -> trusted-network identity."""
    from src.http.auth import get_resolver

    _reload_with(monkeypatch, api_key="secret", solo_bypass="false")
    resolver = get_resolver()
    identity = resolver.resolve(
        _make_request(headers={"Authorization": "Bearer secret"})
    )
    assert identity is not None
    assert identity.trust_domain == "trusted-network"
    assert identity.key_id == "shared-secret"


def test_resolve_trusted_invalid_key_401(monkeypatch):
    """API_KEY set + invalid/missing Bearer -> None (unauthenticated)."""
    from src.http.auth import get_resolver

    _reload_with(monkeypatch, api_key="secret", solo_bypass="false")
    resolver = get_resolver()
    assert resolver.resolve(_make_request()) is None
    assert (
        resolver.resolve(_make_request(headers={"Authorization": "Bearer wrong"}))
        is None
    )


def test_resolve_localhost_bypass(monkeypatch):
    """API_KEY set + solo_bypass + localhost -> solo identity (bypass)."""
    from src.http.auth import get_resolver

    _reload_with(monkeypatch, api_key="secret", solo_bypass="true")
    resolver = get_resolver()
    identity = resolver.resolve(_make_request(host="127.0.0.1"))
    assert identity is not None
    assert identity.trust_domain == "solo"


def test_middleware_uses_resolver(client, monkeypatch):
    """Middleware 401s when the resolver returns None; passes otherwise."""
    from src.http.auth import get_resolver

    _reload_with(monkeypatch, api_key="secret", solo_bypass="false")

    class _DenyResolver:
        def resolve(self, request):
            return None

    monkeypatch.setattr("src.http.auth.get_resolver", lambda: _DenyResolver())
    r = client.get("/conversation", params={"user_id": "auth_user"})
    assert r.status_code == 401

    class _AllowResolver:
        def resolve(self, request):
            from src.http.auth.resolver import UserIdentity

            return UserIdentity(
                user_id="default_user", key_id="shared-secret", trust_domain="trusted-network"
            )

    monkeypatch.setattr("src.http.auth.get_resolver", lambda: _AllowResolver())
    r = client.get("/conversation", params={"user_id": "auth_user"})
    assert r.status_code == 200


def test_resolver_protocol_shape():
    """UserIdentity carries user_id/key_id/trust_domain; protocol is async-agnostic."""
    from src.http.auth.resolver import IdentityResolver, UserIdentity

    assert hasattr(IdentityResolver, "resolve")
    u = UserIdentity(user_id="u1", key_id="k1", trust_domain="trusted-network")
    assert u.user_id == "u1"
    assert u.key_id == "k1"
    assert u.trust_domain == "trusted-network"
