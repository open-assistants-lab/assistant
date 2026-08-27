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


@pytest.fixture(autouse=True)
def _restore_settings_after_each():
    """Reload settings on teardown so the process-global singleton never keeps
    a monkeypatched API_KEY past the test (order-dependent 401s downstream,
    e.g. test_memories — found 2026-08-25)."""
    yield
    from src.config import reload_settings

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
    # P0-T2: the router now enforces user_id against the resolved identity,
    # so the allowed request must carry the identity's user_id.
    r = client.get("/conversation", params={"user_id": "default_user"})
    assert r.status_code == 200
    r = client.get("/conversation", params={"user_id": "auth_user"})
    assert r.status_code == 403


def test_resolver_protocol_shape():
    """UserIdentity carries user_id/key_id/trust_domain; protocol is async-agnostic."""
    from src.http.auth.resolver import IdentityResolver, UserIdentity

    assert hasattr(IdentityResolver, "resolve")
    u = UserIdentity(user_id="u1", key_id="k1", trust_domain="trusted-network")
    assert u.user_id == "u1"
    assert u.key_id == "k1"
    assert u.trust_domain == "trusted-network"


class TestUserEnforcement:
    """P0-T2: authenticated requests cannot spoof another user_id (Batch A)."""

    @pytest.mark.parametrize(
        "method,path,params,body",
        [
            ("GET", "/context-info", {"user_id": "alice"}, None),
            ("GET", "/conversation", {"user_id": "alice"}, None),
            ("GET", "/conversation/turns", {"user_id": "alice"}, None),
            ("GET", "/conversation/sessions", {"user_id": "alice"}, None),
            ("DELETE", "/conversation/session", {"user_id": "alice", "session_id": "s1"}, None),
            ("GET", "/models", {"user_id": "alice"}, None),
            ("DELETE", "/conversation", {"user_id": "alice"}, None),
            ("POST", "/conversation/title", None, {"user_id": "alice", "session_id": "s1"}),
            ("POST", "/message", None, {"user_id": "alice", "message": "hi"}),
            ("POST", "/message/reject", None, {"user_id": "alice", "call_id": "c1"}),
            ("POST", "/message/cancel", None, {"user_id": "alice", "session_id": "s1"}),
            ("POST", "/conversation/import", None, {"user_id": "alice", "session_id": "s1", "messages": []}),
            # Batch B+C2
            ("GET", "/workspace/json", {"user_id": "alice"}, None),
            ("GET", "/workspaces", {"user_id": "alice"}, None),
            ("POST", "/workspaces", {"user_id": "alice"}, {"name": "ws1"}),
            ("GET", "/skills", {"user_id": "alice"}, None),
            ("GET", "/capabilities", {"user_id": "alice"}, None),
            ("PUT", "/capabilities", {"user_id": "alice"}, {"tools": {"x": "all"}}),
            ("GET", "/tools", {"user_id": "alice"}, None),
            ("GET", "/settings", {"user_id": "alice"}, None),
            ("PATCH", "/settings", {"user_id": "alice"}, {"expected_revision": 1}),
            ("GET", "/settings/api-keys", {"user_id": "alice"}, None),
            # Batch D
            ("GET", "/user/prompt", {"user_id": "alice"}, None),
            ("PUT", "/user/prompt", {"user_id": "alice"}, {"prompt": "p"}),
            ("GET", "/user/grader-prompt", {"user_id": "alice"}, None),
            ("PUT", "/user/grader-prompt", {"user_id": "alice"}, {"expected_revision": 1, "prompt": "p"}),
            ("POST", "/user/grader-prompt/reset", {"user_id": "alice"}, {"expected_revision": 1}),
            ("GET", "/scheduler/notifications", {"user_id": "alice"}, None),
            ("POST", "/scheduler/notifications/1/dismiss", {"user_id": "alice"}, None),
            ("POST", "/scheduler/pause", {"user_id": "alice"}, None),
            ("POST", "/scheduler/resume", {"user_id": "alice"}, None),
            ("GET", "/scheduler/status", {"user_id": "alice"}, None),
            ("GET", "/scheduler/memory", {"user_id": "alice"}, None),
            ("DELETE", "/scheduler/memory/1", {"user_id": "alice"}, None),
            ("GET", "/improvements", {"user_id": "alice"}, None),
            ("GET", "/run-outcomes", {"user_id": "alice"}, None),
            ("GET", "/connectors/catalog", {"user_id": "alice"}, None),
            ("GET", "/connectors/catalog/gmail", {"user_id": "alice"}, None),
            ("DELETE", "/connectors/disconnect", {"user_id": "alice", "service": "gmail"}, None),
            ("POST", "/profile/reload", {"user_id": "alice"}, None),
        ],
    )
    def test_mismatched_user_id_403(self, client, monkeypatch, method, path, params, body):
        """Per-user resolver identity + mismatched user_id -> 403.

        Uses an injected per-user resolver (Phase-2 shape): the shared-secret
        resolver returns user_id=None (one key per deployment) and therefore
        correctly does NOT enforce — enforcement activates only when a
        resolver knows the caller.
        """
        _reload_with(monkeypatch, api_key="secret", solo_bypass="false")

        class _PerUserResolver:
            def resolve(self, request):
                from src.http.auth.resolver import UserIdentity

                return UserIdentity(
                    user_id="charlie", key_id="k1", trust_domain="trusted-network"
                )

        monkeypatch.setattr("src.http.auth.get_resolver", lambda: _PerUserResolver())
        kw = {}
        if params:
            kw["params"] = params
        if body is not None:
            kw["json"] = body
        r = client.request(
            method, path, headers={"Authorization": "Bearer secret"}, **kw
        )
        assert r.status_code == 403

    @pytest.mark.parametrize(
        "path,params,body",
        [
            ("/conversation", {"user_id": "default_user"}, None),
            ("/context-info", {"user_id": "default_user"}, None),
            ("/conversation/turns", {"user_id": "default_user"}, None),
            ("/conversation/sessions", {"user_id": "default_user"}, None),
            ("/models", {"user_id": "default_user"}, None),
        ],
    )
    def test_matching_user_id_allowed(self, client, monkeypatch, path, params, body):
        """Per-user resolver + matching user_id -> not a 403."""
        _reload_with(monkeypatch, api_key="secret", solo_bypass="false")

        class _PerUserResolver:
            def resolve(self, request):
                from src.http.auth.resolver import UserIdentity

                return UserIdentity(
                    user_id="default_user", key_id="k1", trust_domain="trusted-network"
                )

        monkeypatch.setattr("src.http.auth.get_resolver", lambda: _PerUserResolver())
        kw = {"params": params}
        if body is not None:
            kw["json"] = body
        r = client.request(
            "GET", path, headers={"Authorization": "Bearer secret"}, **kw
        )
        assert r.status_code != 403


class TestMissingUserIdWarning:
    """D0-5 troubleshooting visibility: an omitted user_id warns (rate-limited)
    while the request still succeeds under the default_user namespace."""

    def _spy_warnings(self, monkeypatch):
        import src.app_logging as _al
        import src.http.main as _main

        _main._missing_uid_warned.clear()  # isolate from other tests' warnings
        seen: list[str] = []

        def _fake_warning(
            self,
            event: str,
            data: dict,
            user_id: str = "default_user",
            channel: str = "cli",
        ):
            seen.append(event)

        monkeypatch.setattr(_al.Logger, "warning", _fake_warning)
        return seen

    def test_missing_user_id_warns_once_and_succeeds(self, client, monkeypatch):
        seen = self._spy_warnings(monkeypatch)
        r1 = client.get("/models")  # GET without user_id query param
        assert r1.status_code == 200
        r2 = client.get("/models")  # inside rate-limit window: no re-warn
        assert r2.status_code == 200
        warns = [e for e in seen if "user_id missing" in e]
        assert len(warns) == 1, seen

    def test_explicit_user_id_does_not_warn(self, client, monkeypatch):
        seen = self._spy_warnings(monkeypatch)
        r = client.get("/models", params={"user_id": "named_user"})
        assert r.status_code == 200
        assert not [e for e in seen if "user_id missing" in e]

    def test_body_missing_user_id_warns(self, client, monkeypatch):
        seen = self._spy_warnings(monkeypatch)

        store = type("S", (), {})()
        store.summary_calls = []
        store.raw_calls = []
        store.sessions = {}

        async def fake_aget(*a, **k):
            return store

        class _Dummy:
            model_id = "x:y"

        async def fake_get_sdk_loop(*a, **k):
            return _Dummy()

        async def fake_orchestrate(
            self, loop, messages, run_id, session_id, lock, rubric=None, mode=None
        ):
            from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

            return RunResult(
                run_id=run_id,
                session_id=session_id,
                status=RunStatus.COMPLETED,
                attempt=1,
                model="x:y",
                response="ok",
                usage=RunUsage(),
                verification=VerificationOutcome(),
            )

        import src.http.routers.conversation as conv

        class _Factory:
            async def __call__(self, *a, **k):
                return store

        monkeypatch.setattr("src.http.routers.conversation.aget_message_store", _Factory())
        monkeypatch.setattr(
            "src.http.routers.conversation.RunService._run_bounded_orchestration",
            fake_orchestrate,
        )
        monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)

        r = client.post("/message", json={"message": "hi"})  # no user_id in body
        assert r.status_code == 200
        assert [e for e in seen if "user_id missing" in e]
