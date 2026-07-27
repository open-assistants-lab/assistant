"""Tools API compatibility tests."""

import builtins
import importlib
from types import SimpleNamespace


def test_tools_router_imports_without_item_scopes(monkeypatch):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise AssertionError("tools router must not import item_scopes")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    module = importlib.import_module("src.http.routers.tools")
    importlib.reload(module)


def test_tool_scope_selected_is_rejected_without_broadening_access(client, test_user_id):
    response = client.patch(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "personal"},
        json={"scope": "selected", "workspace_ids": ["personal"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "workspace-selected scope is no longer supported; use 'all' or 'none'"
    )

    get_response = client.get(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "other-workspace"},
    )
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["enabled"] is True
    assert data["scope"] == "all"
    assert data["workspace_ids"] == []


def test_tool_disabled_state_is_user_level_across_workspace_ids(client, test_user_id):
    response = client.patch(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "personal"},
        json={"enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "none"

    get_response = client.get(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "other-workspace"},
    )
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["enabled"] is False
    assert data["scope"] == "none"
    assert data["workspace_ids"] == []


def test_tool_enabled_legacy_payload_rejects_non_bool(client, test_user_id):
    response = client.patch(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "personal"},
        json={"enabled": "false"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "enabled must be a boolean"


def test_tool_disabled_state_is_isolated_per_user(client, test_user_id, test_user_id_2):
    response = client.patch(
        "/tools/time_get",
        params={"user_id": test_user_id, "workspace_id": "personal"},
        json={"enabled": False},
    )
    assert response.status_code == 200

    other_user_response = client.get(
        "/tools/time_get",
        params={"user_id": test_user_id_2, "workspace_id": "personal"},
    )

    assert other_user_response.status_code == 200
    assert other_user_response.json()["enabled"] is True
    assert other_user_response.json()["scope"] == "all"


async def test_tools_category_counts_use_final_enabled_values(monkeypatch):
    from src.http.routers import tools as tools_router

    registry = [
        SimpleNamespace(name="email_list", description="List", parameters={}),
        SimpleNamespace(name="email_send", description="Send", parameters={}),
    ]

    monkeypatch.setattr(tools_router, "_get_registry", lambda: registry)
    monkeypatch.setattr(
        tools_router,
        "_load_user_caps",
        lambda user_id: {"tools": {"email_send": False}},
    )

    result = await tools_router.list_tools(user_id="u", workspace_id="personal")

    assert [tool["enabled"] for tool in result["tools"]] == [True, False]
    assert result["categories"]["email"] == {"count": 2, "enabled": 1}
