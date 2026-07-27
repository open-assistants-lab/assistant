"""Contract tests for workspace/file endpoints."""

import pytest

from src.http.routers import workspaces as workspaces_router
from src.sdk.workspace_models import Workspace, list_workspaces
from src.storage.messages import get_message_store
from src.storage.paths import DataPaths


class TestWorkspaceDeletionAndRecreation:
    """Workspace metadata operations do not scope user-level runtime data."""

    def test_delete_workspace_keeps_user_session_messages(self, client, test_user_id):
        ws_name = "LLM"
        workspace_id = "llm"
        session_id = "llm-session"

        r = client.post("/workspaces", params={"user_id": test_user_id}, json={"name": ws_name})
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == workspace_id

        store = get_message_store(test_user_id, workspace_id)
        store.clear()
        store.add_message("user", "Hello, this is a test message.", session_id=session_id)
        store.add_message("assistant", "Hi, I am an assistant.", session_id=session_id)

        r = client.get("/conversation", params={"user_id": test_user_id, "session_id": session_id})
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert len(msgs) == 2, f"Expected 2 messages before delete, got {len(msgs)}"

        r = client.delete(f"/workspaces/{workspace_id}", params={"user_id": test_user_id})
        assert r.status_code == 200

        r = client.post("/workspaces", params={"user_id": test_user_id}, json={"name": ws_name})
        assert r.status_code == 200
        new_data = r.json()
        assert new_data["id"] == workspace_id

        r = client.get(
            "/conversation",
            params={"user_id": test_user_id, "session_id": session_id, "limit": 50},
        )
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_update_workspace_resets_user_loops(self, monkeypatch):
        calls = []
        workspace = Workspace(id="project", name="Project")

        monkeypatch.setattr(workspaces_router, "load_workspace", lambda workspace_id, user_id=None: workspace)
        monkeypatch.setattr(workspaces_router, "save_workspace", lambda ws, user_id=None: None)
        monkeypatch.setattr(
            workspaces_router,
            "reset_user_sdk_loops",
            lambda user_id, reason=None: calls.append((user_id, reason)),
            raising=False,
        )

        result = await workspaces_router.update_workspace(
            "project", workspaces_router.UpdateWorkspaceRequest(name="Renamed"), user_id="test_user"
        )

        assert result["name"] == "Renamed"
        assert calls == [("test_user", "workspace_updated:project")]

    @pytest.mark.asyncio
    async def test_delete_workspace_resets_user_loops(self, monkeypatch):
        calls = []
        deleted = []
        workspace = Workspace(id="project", name="Project")

        monkeypatch.setattr(workspaces_router, "load_workspace", lambda workspace_id, user_id=None: workspace)
        monkeypatch.setattr(
            workspaces_router,
            "_delete_ws",
            lambda workspace_id, user_id=None: deleted.append((workspace_id, user_id)),
        )
        monkeypatch.setattr(
            workspaces_router,
            "reset_user_sdk_loops",
            lambda user_id, reason=None: calls.append((user_id, reason)),
            raising=False,
        )

        result = await workspaces_router.delete_workspace_endpoint("project", user_id="test_user")

        assert result == {"status": "deleted", "messages_deleted": 0}
        assert deleted == [("project", "test_user")]
        assert calls == [("test_user", "workspace_deleted:project")]

    def test_workspace_metadata_is_isolated_per_user(self, client, test_user_id, test_user_id_2):
        r = client.post("/workspaces", params={"user_id": test_user_id}, json={"name": "Private"})
        assert r.status_code == 200

        assert any(w.id == "private" for w in list_workspaces(user_id=test_user_id))
        assert all(w.id != "private" for w in list_workspaces(user_id=test_user_id_2))

        first_root = DataPaths(user_id=test_user_id).user_dir / "Workspaces"
        second_root = DataPaths(user_id=test_user_id_2).user_dir / "Workspaces"
        assert first_root != second_root

    def test_clear_conversation_ignores_workspace_id_and_clears_user_store(self, client, test_user_id):
        ws_keep = "keep_ws"
        ws_clear = "clear_ws"
        store = get_message_store(test_user_id, ws_keep)
        store.clear()
        store.add_message("user", "keep message", session_id=ws_keep)
        store.add_message("user", "clear message", session_id=ws_clear)

        r = client.delete(
            "/conversation",
            params={"user_id": test_user_id, "workspace_id": ws_clear},
        )
        assert r.status_code == 200

        r = client.get("/conversation", params={"user_id": test_user_id, "session_id": ws_keep})
        assert r.json()["messages"] == []

        r = client.get("/conversation", params={"user_id": test_user_id, "session_id": ws_clear})
        assert r.json()["messages"] == []


class TestWorkspaceFiles:
    """Tests for workspace file endpoints."""

    def test_list_workspace_root(self, client, test_user_id):
        r = client.get("/workspace", params={"user_id": test_user_id})
        assert r.status_code == 200

    def test_list_workspace_subpath(self, client, test_user_id):
        r = client.get("/workspace/documents", params={"user_id": test_user_id})
        assert r.status_code == 200

    def test_file_routes_ignore_workspace_id_for_user_files(self, client, test_user_id):
        project_params = {"user_id": test_user_id, "workspace_id": "project"}
        personal_params = {"user_id": test_user_id, "workspace_id": "personal"}
        filename = f"note_{test_user_id}.txt"

        r = client.post(f"/workspace/{filename}", params=project_params, json={"content": "project-only"})
        assert r.status_code == 200

        r = client.get(f"/workspace/read/{filename}", params=project_params)
        assert r.status_code == 200
        assert "project-only" in r.json()["response"]

        r = client.get(f"/workspace/read/{filename}", params=personal_params)
        assert r.status_code == 200
        assert "project-only" in r.json()["response"]


class TestFileSync:
    """Tests for file sync endpoints."""

    def test_get_sync_status(self, client, test_user_id):
        r = client.get("/sync/status", params={"user_id": test_user_id})
        assert r.status_code == 200

    def test_pin_file(self, client, test_user_id):
        r = client.post("/sync/pin/test_file.txt", params={"user_id": test_user_id})
        assert r.status_code == 200

    def test_unpin_file(self, client, test_user_id):
        r = client.delete("/sync/pin/test_file.txt", params={"user_id": test_user_id})
        assert r.status_code == 200

    def test_mark_downloaded(self, client, test_user_id):
        r = client.post("/sync/download/test_file.txt", params={"user_id": test_user_id})
        assert r.status_code == 200
