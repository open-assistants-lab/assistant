"""Authentication coverage tests for HTTP routes."""


def test_non_health_routes_require_api_key_when_configured(client, monkeypatch):
    from src.config import reload_settings

    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()
    try:
        r = client.get("/conversation", params={"user_id": "auth_user"})
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("SOLO_BYPASS", raising=False)
        reload_settings()


def test_bearer_api_key_allows_protected_routes(client, monkeypatch):
    from src.config import reload_settings

    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()
    try:
        r = client.get(
            "/conversation",
            params={"user_id": "auth_user"},
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code == 200
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("SOLO_BYPASS", raising=False)
        reload_settings()


def test_health_route_remains_public_when_api_key_configured(client, monkeypatch):
    from src.config import reload_settings

    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()
    try:
        r = client.get("/health")
        assert r.status_code == 200
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("SOLO_BYPASS", raising=False)
        reload_settings()
