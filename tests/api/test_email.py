"""Contract tests for email endpoints.

GET  /emails              — list emails
GET  /emails/:id           — single email
GET  /emails/search?q=...  — search
POST /emails/sync          — trigger sync
"""


class TestEmails:
    """Tests for /emails endpoints."""

    def test_list_emails(self, client, test_user_id):
        r = client.get("/emails", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "emails" in data

    def test_get_email_not_found(self, client, test_user_id):
        r = client.get("/emails/nonexistent", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "error" in data or "email_id" in data

    def test_search_emails(self, client, test_user_id):
        r = client.get("/emails/search", params={"q": "test", "user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "emails" in data


class TestEmailSync:
    """POST /emails/sync — GmailClient path (roadmap G4)."""

    def test_sync_without_token_returns_not_connected(self, client, monkeypatch, test_user_id):
        """No vault token -> frontend-friendly not-connected error (HTTP 200)."""
        import src.http.routers.email as email_router

        class _DisconnectedGmailClient:
            def is_connected(self):
                return False

        monkeypatch.setattr(email_router, "GmailClient", lambda *a, **k: _DisconnectedGmailClient())
        r = client.post("/emails/sync", params={"user_id": test_user_id})
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == "not_connected"
        assert "Sign in with Google" in body["detail"]

    def test_sync_with_token_triggers_background_sync(self, client, monkeypatch, test_user_id):
        """Vault token present -> sync_started and sync_emails scheduled."""
        import src.http.routers.email as email_router

        class _ConnectedGmailClient:
            def is_connected(self):
                return True

        called: list[tuple] = []

        def _fake_sync_emails(user_id, **kwargs):
            called.append(user_id)
            return {"listed": 0, "fetched": 0, "upserted": 0, "errors": 0}

        monkeypatch.setattr(email_router, "GmailClient", lambda *a, **k: _ConnectedGmailClient())
        monkeypatch.setattr(email_router, "sync_emails", _fake_sync_emails)
        r = client.post("/emails/sync", params={"user_id": test_user_id})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "sync_started"
        assert body["provider"] == "gmail"
        # Background task runs on the server event loop; give it a beat.
        import time

        time.sleep(0.3)
        assert called == [test_user_id]
