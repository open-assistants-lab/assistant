"""Contract tests for subagent V1 endpoints."""

import builtins
import importlib
from uuid import uuid4


def _agent_name() -> str:
    return f"worker_{uuid4().hex[:8]}"


class TestSubagentsEndpoints:
    def test_list_subagents(self, client, test_user_id):
        r = client.get(
            "/subagents",
            params={"user_id": test_user_id, "workspace_id": "personal"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data

    def test_list_subagent_jobs(self, client, test_user_id):
        r = client.get(
            "/subagents/jobs",
            params={"user_id": test_user_id, "workspace_id": "personal"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "jobs" in data

    def test_get_subagent_job_not_found(self, client):
        r = client.get(
            "/subagents/jobs/nonexistent_job_id",
            params={"user_id": "test_user", "workspace_id": "personal"},
        )
        assert r.status_code == 404

    def test_invalid_user_id_returns_client_error(self, client):
        response = client.get(
            "/subagents",
            params={"user_id": "bad/user", "workspace_id": "personal"},
        )
        assert 400 <= response.status_code < 500
        assert response.json()["detail"]

    def test_invalid_workspace_id_returns_client_error(self, client):
        response = client.get(
            "/subagents/jobs",
            params={"user_id": "test_user", "workspace_id": "bad/workspace"},
        )
        assert 400 <= response.status_code < 500
        assert response.json()["detail"]

    def test_scope_selected_is_rejected_without_broadening_access(
        self, client, test_user_id
    ):
        name = _agent_name()
        params = {"user_id": test_user_id, "workspace_id": "personal"}
        create_response = client.post(
            "/subagents",
            params=params,
            json={"name": name, "description": "Selected worker"},
        )
        assert create_response.status_code == 200

        response = client.patch(
            f"/subagents/{name}/scope",
            params={"user_id": test_user_id},
            json={"scope": "selected", "workspace_ids": ["personal"]},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "workspace-selected scope is no longer supported; use 'all' or 'none'"
        )

        list_response = client.get(
            "/subagents",
            params={"user_id": test_user_id, "workspace_id": "other-workspace"},
        )
        assert list_response.status_code == 200
        agents = {agent["name"]: agent for agent in list_response.json()["agents"]}
        assert agents[name]["scope"] == "all"
        assert agents[name]["workspace_ids"] == []

        client.delete(f"/subagents/{name}", params=params)

    def test_disabled_subagent_remains_visible_with_none_scope(self, client, test_user_id):
        name = _agent_name()
        params = {"user_id": test_user_id, "workspace_id": "personal"}
        create_response = client.post(
            "/subagents",
            params=params,
            json={"name": name, "description": "Disabled worker"},
        )
        assert create_response.status_code == 200

        disable_response = client.patch(
            f"/subagents/{name}/scope",
            params={"user_id": test_user_id},
            json={"scope": "none"},
        )
        assert disable_response.status_code == 200

        list_response = client.get("/subagents", params=params)
        assert list_response.status_code == 200
        agents = {agent["name"]: agent for agent in list_response.json()["agents"]}
        assert agents[name]["scope"] == "none"
        assert agents[name]["enabled"] is False
        assert agents[name]["workspace_ids"] == []

        start_response = client.post(
            f"/subagents/{name}/start",
            params=params,
            json={"task": "do work"},
        )
        assert start_response.status_code == 404

        client.delete(f"/subagents/{name}", params=params)

    def test_enabled_legacy_payload_rejects_non_bool(self, client, test_user_id):
        name = _agent_name()
        params = {"user_id": test_user_id, "workspace_id": "personal"}
        create_response = client.post(
            "/subagents",
            params=params,
            json={"name": name, "description": "Enabled string worker"},
        )
        assert create_response.status_code == 200

        response = client.patch(
            f"/subagents/{name}/scope",
            params={"user_id": test_user_id},
            json={"enabled": "false"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "enabled must be a boolean"

        client.delete(f"/subagents/{name}", params=params)


def test_subagents_router_imports_without_item_scopes(monkeypatch):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise AssertionError("subagents router must not import item_scopes")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    module = importlib.import_module("src.http.routers.subagents")
    importlib.reload(module)


class TestSubagentV1Invocations:
    def test_create_start_list_detail_and_instruct_subagent_job(self, client, test_user_id):
        name = _agent_name()
        params = {"user_id": test_user_id, "workspace_id": "personal"}

        create_response = client.post(
            "/subagents",
            params=params,
            json={"name": name, "description": "Test worker"},
        )
        assert create_response.status_code == 200
        assert create_response.json() == {
            "status": "created",
            "name": name,
            "workspace_id": "personal",
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

    def test_subagent_jobs_are_user_level_across_workspace_ids(self, client, test_user_id):
        name = _agent_name()
        create_params = {"user_id": test_user_id, "workspace_id": "sales"}
        read_params = {"user_id": test_user_id, "workspace_id": "support"}

        create_response = client.post(
            "/subagents",
            params=create_params,
            json={"name": name, "description": "Cross workspace worker"},
        )
        assert create_response.status_code == 200

        start_response = client.post(
            f"/subagents/{name}/start",
            params=create_params,
            json={"task": "do cross workspace work"},
        )
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]

        detail_response = client.get(f"/subagents/jobs/{job_id}", params=read_params)
        assert detail_response.status_code == 200
        assert detail_response.json()["job"]["id"] == job_id

        list_response = client.get("/subagents/jobs", params=read_params)
        assert list_response.status_code == 200
        assert any(job["id"] == job_id for job in list_response.json()["jobs"])

        instruction_response = client.post(
            f"/subagents/jobs/{job_id}/instructions",
            params=read_params,
            json={"instruction": "continue"},
        )
        assert instruction_response.status_code == 200

        client.delete(f"/subagents/{name}", params=create_params)

    def test_create_subagent_invalid_name_returns_client_error(self, client, test_user_id):
        response = client.post(
            "/subagents",
            params={"user_id": test_user_id, "workspace_id": "personal"},
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
            params={"user_id": "test_user", "workspace_id": "personal"},
        )
        assert r.status_code == 404

    def test_old_path_cancel_route_removed(self, client):
        response = client.post(
            "/subagents/nonexistent_job/cancel",
            params={"user_id": "test_user", "workspace_id": "personal"},
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
            params={"user_id": test_user_id, "workspace_id": "personal"},
        )
        assert r.status_code in (200, 404)

    def test_delete_subagent(self, client, test_user_id):
        r = client.delete(
            "/subagents/nonexistent_agent",
            params={"user_id": test_user_id, "workspace_id": "personal"},
        )
        assert r.status_code in (200, 404)
