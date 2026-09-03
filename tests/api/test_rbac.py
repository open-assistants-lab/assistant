"""T3.2 API RBAC: role gates on tenancy endpoints + governance self-approval 403."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def rbac_env(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod
    from src.config.settings import reload_settings

    monkeypatch.setenv("PER_USER_AUTH", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    import src.http.auth as http_auth

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    monkeypatch.setattr(http_auth, "_DEFAULT_RESOLVER", None)
    from src.config import settings as settings_module

    settings_module._config = None
    import src.storage.paths as pm

    pm._paths_cache.clear()
    yield
    monkeypatch.undo()
    pm._paths_cache.clear()
    reload_settings()


def _client():
    from src.http.main import app

    return TestClient(app, raise_server_exceptions=False)


def _key_for(tmp_path, user_id, scopes=""):
    from src.auth.keys import get_key_store

    # Same store the resolver uses (paths.root/auth.db) — the fixture has
    # already patched DataPaths.root and cleared the cache.
    return get_key_store().generate(user_id, scopes=scopes)


def test_staff_cannot_create_org(rbac_env, tmp_path):
    key = _key_for(tmp_path, "staff_u")
    r = _client().post(
        "/v1/tenancy/orgs",
        json={"name": "acme"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403


def test_admin_scoped_key_can_create_org_and_members(rbac_env, tmp_path):
    key = _key_for(tmp_path, "owner_u", scopes="admin")
    c = _client()
    r = c.post(
        "/v1/tenancy/orgs",
        json={"name": "acme"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    tid = r.json()["tenant_id"]
    r2 = c.post(
        f"/v1/tenancy/{tid}/members",
        json={"user_id": "new_hire"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r2.status_code == 200


def test_role_gate_401_without_key_nonlocalhost(rbac_env):
    c = _client()
    r = c.post("/v1/tenancy/orgs", json={"name": "x"})
    assert r.status_code in (401, 403)
