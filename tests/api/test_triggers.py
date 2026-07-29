"""API tests for trigger endpoints (manual, webhook)."""

from fastapi.testclient import TestClient

from src.http.main import app


def test_manual_trigger_endpoint():
    client = TestClient(app)
    response = client.post("/trigger", json={
        "user_id": "trigger_test",
        "session_id": "ts1",
        "message": "hello agent",
    })
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["status"] in ("started", "completed", "error")


def test_webhook_endpoint_requires_user_id_and_message():
    client = TestClient(app)
    response = client.post("/webhooks/wh_test", json={"foo": "bar"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "user_id and message are required" in body["error"]


def test_webhook_endpoint_accepts_valid_body():
    client = TestClient(app)
    response = client.post("/webhooks/wh_test", json={
        "user_id": "webhook_test",
        "session_id": "wh-s1",
        "message": "check my email",
        "custom_field": "extra_data",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("completed", "error")
    assert body["trigger_id"] == "wh_test"
