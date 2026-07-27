"""Capabilities API tests."""


def test_capabilities_patch_writes_user_level_file_and_ignores_workspace(client, test_user_id):
    response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": False}},
    )

    assert response.status_code == 200
    assert response.json()["tools"]["time_get"] is False

    other_workspace = client.get(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "support"},
    )
    assert other_workspace.status_code == 200
    assert other_workspace.json()["tools"]["time_get"] is False


def test_capabilities_replace_writes_user_level_file_and_ignores_workspace(client, test_user_id):
    response = client.put(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": False}, "skills": {}, "subagents": {}},
    )

    assert response.status_code == 200
    assert response.json()["tools"]["time_get"] is False

    other_workspace = client.get(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "support"},
    )
    assert other_workspace.status_code == 200
    assert other_workspace.json()["tools"]["time_get"] is False


def test_capabilities_replace_rejects_selected_string_values(client, test_user_id):
    response = client.put(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": "selected"}, "skills": {}, "subagents": {}},
    )

    assert response.status_code == 400
    assert "tools.time_get must be a boolean" in response.json()["detail"]


def test_capabilities_replace_rejects_scope_shaped_values(client, test_user_id):
    response = client.put(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={
            "tools": {},
            "skills": {"helper": {"scope": "selected", "workspace_ids": ["sales"]}},
            "subagents": {},
        },
    )

    assert response.status_code == 400
    assert "skills.helper must be a boolean" in response.json()["detail"]


def test_capabilities_replace_rejects_unknown_top_level_keys(client, test_user_id):
    response = client.put(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {}, "skills": {}, "subagents": {}, "scope": "selected"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown capabilities section: scope"


def test_capabilities_patch_rejects_unknown_top_level_keys(client, test_user_id):
    response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"workspace_ids": ["sales"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown capabilities section: workspace_ids"


def test_capabilities_patch_rejects_selected_string_values(client, test_user_id):
    response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"subagents": {"worker": "selected"}},
    )

    assert response.status_code == 400
    assert "subagents.worker must be a boolean or null" in response.json()["detail"]


def test_capabilities_patch_rejects_scope_shaped_values(client, test_user_id):
    response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": {"scope": "selected", "workspace_ids": ["sales"]}}},
    )

    assert response.status_code == 400
    assert "tools.time_get must be a boolean or null" in response.json()["detail"]


def test_capabilities_patch_allows_null_to_remove_key(client, test_user_id):
    disable_response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": False}},
    )
    assert disable_response.status_code == 200

    remove_response = client.patch(
        "/capabilities",
        params={"user_id": test_user_id, "workspace_id": "sales"},
        json={"tools": {"time_get": None}},
    )

    assert remove_response.status_code == 200
    assert "time_get" not in remove_response.json()["tools"]
