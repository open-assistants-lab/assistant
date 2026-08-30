from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.http.main import app


def test_mcp_health_versioned_and_legacy_aliases_share_shape():
    manager = AsyncMock()
    manager.health.return_value = {
        "user_id": "u",
        "servers": {
            "healthy": {
                "status": "connected",
                "connected": True,
                "degraded": False,
                "last_refresh": "2026-08-31T00:00:00+00:00",
                "tool_count": 3,
                "last_error": None,
            }
        },
    }
    with patch("src.http.routers.mcp.get_mcp_manager", return_value=manager):
        with TestClient(app) as client:
            legacy = client.get("/mcp/health", params={"user_id": "u"})
            versioned = client.get("/v1/mcp/health", params={"user_id": "u"})

    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()
    assert set(legacy.json()["servers"]["healthy"]) == {
        "status", "connected", "degraded", "last_refresh", "tool_count", "last_error"
    }
