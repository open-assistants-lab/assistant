"""Contract tests for memory endpoints (post-CoreMem-0.10: profile + clear)."""


class TestProfile:
    """Tests for GET /memories/profile."""

    def test_profile_default(self, client, test_user_id):
        r = client.get("/memories/profile", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "profile" in data
        assert isinstance(data["profile"], list)

    def test_profile_with_params(self, client, test_user_id):
        r = client.get(
            "/memories/profile",
            params={"user_id": test_user_id, "days": 14, "limit": 5},
        )
        assert r.status_code == 200
        assert isinstance(r.json()["profile"], list)


class TestClear:
    """Tests for DELETE /memories/clear."""

    def test_clear_memories(self, client, test_user_id):
        r = client.delete("/memories/clear", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "cleared"
        assert data["user_id"] == test_user_id
