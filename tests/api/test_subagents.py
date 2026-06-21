"""Contract tests for subagent V1 endpoints."""

from uuid import uuid4


def _agent_name() -> str:
    return f"worker_{uuid4().hex[:8]}"


class TestSubagentsEndpoints:
    def test_list_subagents(self, client, test_user_id):
        r = client.get(
            "/subagents",
            params={"user_id": test_user_id},
        )
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data

    def test_list_subagent_jobs(self, client, test_user_id):
        r = client.get(
            "/subagents/jobs",
            params={"user_id": test_user_id},
        )
        assert r.status_code == 200
        data = r.json()
        assert "jobs" in data

    def test_get_subagent_job_not_found(self, client):
        r = client.get(
            "/subagents/jobs/nonexistent_job_id",
            params={"user_id": "test_user"},
        )
        assert r.status_code == 404

    def test_invalid_user_id_returns_client_error(self, client):
        response = client.get(
            "/subagents",
            params={"user_id": "bad/user"},
        )
        assert 400 <= response.status_code < 500
        assert response.json()["detail"]


class TestSubagentV1Invocations:
    def test_create_start_list_detail_and_instruct_subagent_job(self, client, test_user_id):
        name = _agent_name()
        params = {"user_id": test_user_id}

        create_response = client.post(
            "/subagents",
            params=params,
            json={"name": name, "description": "Test worker"},
        )
        assert create_response.status_code == 200
        assert create_response.json() == {
            "status": "created",
            "name": name,
        }

        start_response = client.post(
            f"/subagents/{name}/start",
            params=params,
            json={"task": "do work"},
        )
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["status"] == "pending"
        assert start_data["subagent"] == name
        assert start_data["job_id"]

        detail_response = client.get(f"/subagents/jobs/{start_data['job_id']}", params=params)
        assert detail_response.status_code == 200
        detail_job = detail_response.json()["job"]
        assert detail_job["id"] == start_data["job_id"]
        assert detail_job["status"] in {"pending", "running", "failed", "completed"}

        list_response = client.get("/subagents/jobs", params=params)
        assert list_response.status_code == 200
        jobs = list_response.json()["jobs"]
        assert any(job["id"] == start_data["job_id"] for job in jobs)

        instruction_response = client.post(
            f"/subagents/jobs/{start_data['job_id']}/instructions",
            params=params,
            json={"instruction": "focus"},
        )
        assert instruction_response.status_code == 200
        assert instruction_response.json() == {
            "status": "instruction_added",
            "job_id": start_data["job_id"],
        }

        client.delete(f"/subagents/{name}", params=params)

    def test_create_subagent_invalid_name_returns_client_error(self, client, test_user_id):
        response = client.post(
            "/subagents",
            params={"user_id": test_user_id},
            json={"name": "bad/name", "description": "invalid"},
        )
        assert 400 <= response.status_code < 500
        assert response.status_code != 500
        assert response.json()["detail"]

    def test_old_invoke_route_removed(self, client):
        response = client.post("/subagents/invoke", params={"name": "worker", "task": "do work"})
        assert response.status_code in {404, 405}

    def test_legacy_routes_are_not_registered(self, app):
        route_paths = {getattr(route, "path", "") for route in app.routes}
        assert "/subagents/invoke" not in route_paths
        assert "/subagents/instruct" not in route_paths

    def test_cancel_subagent_job(self, client):
        r = client.post(
            "/subagents/jobs/nonexistent_job/cancel",
            params={"user_id": "test_user"},
        )
        assert r.status_code == 404

    def test_old_path_cancel_route_removed(self, client):
        response = client.post(
            "/subagents/nonexistent_job/cancel",
            params={"user_id": "test_user"},
        )
        assert response.status_code == 404

    def test_old_instruct_route_removed(self, client):
        response = client.post(
            "/subagents/instruct",
            params={"name": "worker", "instruction": "focus"},
        )
        assert response.status_code in {404, 405}

    def test_update_subagent(self, client, test_user_id):
        r = client.patch(
            "/subagents/nonexistent_agent",
            json={"tools": ["web_search"]},
            params={"user_id": test_user_id},
        )
        assert r.status_code in (200, 404)

    def test_delete_subagent(self, client, test_user_id):
        r = client.delete(
            "/subagents/nonexistent_agent",
            params={"user_id": test_user_id},
        )
        assert r.status_code in (200, 404)
