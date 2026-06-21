"""Contract tests for skills endpoints."""


class TestSkillsEndpoints:
    """Tests for skills CRUD endpoints."""

    def test_list_skills(self, client, test_user_id):
        r = client.get("/skills", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)

    def test_list_skills_response_schema(self, client, test_user_id):
        r = client.get("/skills", params={"user_id": test_user_id})
        data = r.json()
        if data["skills"]:
            skill = data["skills"][0]
            for key in (
                "name",
                "description",
                "is_loaded",
                "disable_model_invocation",
            ):
                assert key in skill

    def test_create_skill(self, client, test_user_id):
        r = client.post(
            "/skills",
            json={
                "name": "test-skill-api",
                "description": "A test skill",
                "content": "# Test Skill\nThis is a test.",
                "scope": "user",
            },
            params={
                "user_id": test_user_id,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "test-skill-api"

    def test_delete_skill(self, client, test_user_id):
        client.post(
            "/skills",
            json={
                "name": "test-skill-api",
                "description": "A test skill",
                "content": "# Test Skill\nThis is a test.",
                "scope": "user",
            },
            params={"user_id": test_user_id},
        )
        r = client.delete("/skills/test-skill-api", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "deleted"
