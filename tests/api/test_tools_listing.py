"""Issue #8: /v1/tools must list per-user custom TOOL.md tools.

Custom tools from the deployment-shared + per-user Tools/ dirs appear in the
listing alongside native/MCP tools, with the same annotation surface, scoped
to the requesting user only.
"""

from __future__ import annotations

import pytest


def _write_tool(tools_dir, name, description):
    d = tools_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "TOOL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\ncommand: echo hi\n---\nbody",
        encoding="utf-8",
    )


@pytest.fixture()
def _isolated(tmp_path, monkeypatch):
    import src.http.auth as http_auth
    import src.http.routers.tools as tools_mod
    import src.storage.paths as paths_mod
    from src.config.settings import reload_settings

    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    paths_mod._paths_cache.clear()
    reload_settings()
    yield tmp_path / "root"
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def client(_isolated):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_custom_tools_listed_for_owning_user(client, _isolated):
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id="alice")
    _write_tool(dp.user_tools_dir(), "snooze_check", "Alice custom tool")

    resp = client.get("/v1/tools", params={"user_id": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    names = [t["name"] for t in body["tools"]]
    assert "snooze_check" in names
    entry = next(t for t in body["tools"] if t["name"] == "snooze_check")
    assert entry["source"] == "custom"
    # Same annotation surface as core entries
    assert "annotations" in entry and "parameters" in entry and "enabled" in entry
    # Category rollup includes custom tools
    assert body["categories"].get("custom", {"count": 0})["count"] >= 1


def test_other_user_does_not_see_custom_tools(client, _isolated):
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id="alice")
    _write_tool(dp.user_tools_dir(), "snooze_check", "Alice custom tool")

    resp = client.get("/v1/tools", params={"user_id": "bob"})
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["tools"]]
    assert "snooze_check" not in names