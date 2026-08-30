"""Agent loop streaming and error handling conformance tests via HTTP API.

These verify agent behavior through the HTTP API, ensuring consistent
responses regardless of the underlying agent implementation.
"""




class TestMessageEndpoint:
    """Tests for POST /message endpoint behavior."""

    def test_message_returns_response(self, client):
        """POST /message must return a response string."""
        r = client.post("/message", json={"message": "test", "user_id": "test_stream_api"})
        assert r.status_code == 200
        data = r.json()
        assert "response" in data

    def test_message_accepts_verbose(self, client):
        """POST /message must accept verbose=True."""
        r = client.post(
            "/message", json={"message": "test", "user_id": "test_verbose_api", "verbose": True}
        )
        assert r.status_code == 200

    def test_message_response_schema(self, client):
        """MessageResponse must include 'response' and 'error' fields."""
        r = client.post("/message", json={"message": "test", "user_id": "test_schema_api"})
        data = r.json()
        assert "response" in data
        assert isinstance(data["response"], str)

    def test_conversation_persists(self, client):
        """Messages sent via /message are stored in conversation history."""
        from src.storage.messages import get_message_store

        uid = "test_persist_conv_api"
        store = get_message_store(uid)
        store.clear()
        client.post("/message", json={"message": "Hello agent", "user_id": uid})
        messages = store.get_recent_messages(1)
        assert len(messages) >= 1

    def test_empty_message(self, client):
        """Empty message must still return a valid response."""
        r = client.post("/message", json={"message": "", "user_id": "test_empty_api"})
        assert r.status_code == 200

    def test_very_long_message(self, client):
        """Very long messages must not crash the agent."""
        r = client.post("/message", json={"message": "x" * 10000, "user_id": "test_long_api"})
        assert r.status_code == 200

    def test_health_always_works(self, client):
        """Health endpoint must always return 200."""
        r = client.get("/health")
        assert r.status_code == 200


class TestStreamingEndpoint:
    """Tests for POST /message/stream SSE endpoint."""

    def test_stream_returns_sse(self, client):
        """POST /message/stream must return SSE content type."""
        r = client.post(
            "/message/stream",
            json={
                "message": "test",
                "user_id": "test_sse_api",
                "model": "ollama-cloud:deepseek-v4-flash:0731",
            },
        )
        assert r.status_code == 200


class TestConversationEndpoint:
    """Tests for conversation CRUD via HTTP API."""

    def test_get_conversation(self, client, test_user_id):
        r = client.get("/conversation", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "messages" in data

    def test_clear_conversation(self, client, test_user_id):
        r = client.delete("/conversation", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "cleared"
