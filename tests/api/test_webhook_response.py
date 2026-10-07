"""#151: the webhook must return the answer it produced.

The 2026-06-09 spec's data flow ends with "optional callback to the trigger
source with outcome"; the 2026-07-27 spec ships `WebhookResponse.response`.
Both were unreachable: `TriggerRegistry.fire` discarded the handler's return
value and the endpoint hardcoded a bare "completed", so an external caller had
no way to see what the agent answered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.sdk.session_worker import SessionBusyError


@pytest.fixture()
def webhook_client(monkeypatch, tmp_path):
    import src.http.routers.webhooks as hooks
    from src.http.main import app

    hooks._secret_store._path = tmp_path / "secrets.json"

    class FakeRegistry:
        def __init__(self) -> None:
            self.answer: str | None = "the answer"
            self.raises: Exception | None = None

        async def fire(self, event):
            if self.raises is not None:
                raise self.raises
            return self.answer

    registry = FakeRegistry()
    monkeypatch.setattr(hooks, "get_trigger_registry", lambda: registry)
    client = TestClient(app)
    created = client.post("/webhooks/t1/secret", params={"user_id": "owner"})
    secret = created.json()["secret"]
    yield client, registry, secret
    hooks._secret_store._path = None


def _fire(client, secret, message="hello"):
    return client.post(
        "/webhooks/t1",
        json={"user_id": "owner", "message": message},
        headers={"X-Webhook-Secret": secret},
    )


def test_webhook_returns_the_agents_answer(webhook_client):
    client, _registry, secret = webhook_client
    body = _fire(client, secret).json()

    assert body["status"] == "completed"
    assert body["response"] == "the answer", (
        "the caller cannot see what the agent answered — the handler's return "
        "value is discarded"
    )


def test_webhook_reports_a_busy_session_as_a_retryable_error(webhook_client):
    """Queueing behind a live run must fail cleanly, not as an opaque crash."""
    client, registry, secret = webhook_client
    registry.raises = SessionBusyError("Session busy: a run is already active")

    body = _fire(client, secret).json()

    assert body["status"] == "error"
    assert "busy" in (body["error"] or "").lower()
