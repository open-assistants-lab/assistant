"""P0-T5: /v1 route aliasing — same handlers, same bodies, no redirect."""

import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _solo_mode(monkeypatch):
    """Keep tests in solo mode (no API_KEY) so both prefixes are auth-free."""
    from src.config import reload_settings

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
    reload_settings()
    yield
    reload_settings()


def _post(client, path, payload=None):
    return client.post(path, json=payload if payload is not None else {"message": "hi"})


def _paths(app):
    from starlette.routing import Route, WebSocketRoute

    return [
        r.path
        for r in app.routes
        if isinstance(r, (Route, WebSocketRoute))
    ]


class TestV1Message:
    def test_v1_message_same_handler(self, client):
        """Both prefixes resolve to the SAME endpoint function (the ≡ proof,
        deterministic — avoids LLM non-determinism across two calls)."""
        from starlette.routing import Route

        routes = {r.path: r for r in client.app.routes if isinstance(r, Route)}
        assert routes["/v1/message"].endpoint is routes["/message"].endpoint
        assert routes["/v1/message/stream"].endpoint is routes["/message/stream"].endpoint
        assert routes["/v1/conversation"].endpoint is routes["/conversation"].endpoint
        assert routes["/v1/models"].endpoint is routes["/models"].endpoint
        assert routes["/v1/context-info"].endpoint is routes["/context-info"].endpoint

    def test_v1_message_smoke_200(self, client):
        """Functional smoke: v1 message returns 200 with a response."""
        r = client.post("/v1/message", json={"message": "hi"})
        assert r.status_code == 200
        assert "response" in r.json()

    def test_v1_message_stream_equals_legacy(self, client):
        # The repo ships with no pinned model (D0-5) — alias-equivalence tests
        # pass the model explicitly per request (the documented bypass).
        body = {"message": "hi", "model": "ollama-cloud:deepseek-v4-flash:0731"}
        legacy = client.post("/message/stream", json=body)
        v1 = client.post("/v1/message/stream", json=body)
        assert legacy.status_code == v1.status_code == 200
        # SSE streams: heartbeat comments may interleave differently, so
        # compare the first data: payload semantically, not raw bytes.
        assert b"data: " in legacy.content and b"data: " in v1.content

    def test_v1_conversation_equals_legacy(self, client):
        legacy = client.get("/conversation")
        v1 = client.get("/v1/conversation")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()

    def test_v1_models_equals_legacy(self, client):
        legacy = client.get("/models")
        v1 = client.get("/v1/models")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()

    def test_v1_context_info_equals_legacy(self, client):
        legacy = client.get("/context-info")
        v1 = client.get("/v1/context-info")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()


class TestV1PrefixedRouters:
    def test_v1_tools_equals_legacy(self, client):
        legacy = client.get("/tools")
        v1 = client.get("/v1/tools")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()

    def test_v1_audit_equals_legacy(self, client):
        # /audit is NDJSON; compare status + content-type + body, not .json().
        legacy = client.get("/audit")
        v1 = client.get("/v1/audit")
        assert legacy.status_code == v1.status_code == 200
        assert legacy.headers.get("content-type") == v1.headers.get("content-type") == "application/x-ndjson"
        assert legacy.content == v1.content

    def test_v1_skills_equals_legacy(self, client):
        legacy = client.get("/skills")
        v1 = client.get("/v1/skills")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()

    def test_v1_capabilities_equals_legacy(self, client):
        legacy = client.get("/capabilities")
        v1 = client.get("/v1/capabilities")
        assert legacy.status_code == v1.status_code
        assert legacy.json() == v1.json()


class TestV1Registration:
    def test_legacy_paths_still_present(self, client):
        paths = _paths(client.app)
        for p in ("/message", "/message/stream", "/conversation",
                  "/context-info", "/models", "/tools", "/capabilities",
                  "/skills", "/subagents", "/audit", "/ws/conversation"):
            assert p in paths, f"legacy path missing: {p}"

    def test_v1_paths_present(self, client):
        paths = _paths(client.app)
        for p in ("/v1/message", "/v1/message/stream", "/v1/conversation",
                  "/v1/context-info", "/v1/models", "/v1/tools",
                  "/v1/capabilities", "/v1/skills", "/v1/subagents",
                  "/v1/audit", "/v1/ws/conversation"):
            assert p in paths, f"v1 path missing: {p}"

    def test_no_redirect(self, client):
        """/v1/* must be the same handler, not a 307 redirect to legacy."""
        r = client.post("/v1/message", json={"message": "hi"})
        assert r.status_code != 307
        assert r.status_code == 200
