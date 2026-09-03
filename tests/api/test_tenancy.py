"""T3.1 API: org/sub-tenant CRUD under /v1/tenancy (admin-gated).

Auth patterns mirror billing.py: trusted-network (flag off) is admin by
definition; per-user-key deployments require an admin-scope key.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tenancy_env(monkeypatch, tmp_path):
    """Auth-off (trusted-network) deployment: admin by definition."""
    import src.storage.paths as paths_mod
    from src.config import reload_settings

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    import src.storage.tenancy as ten

    monkeypatch.setattr(ten, "_TENANCY_STORE", None)
    monkeypatch.setattr("src.storage.tenant._TENANT_STORE", None)
    paths_mod._paths_cache.clear()
    reload_settings()
    yield
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def client(tenancy_env):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app) as c:
        yield c


class TestOrgCrud:
    def test_create_org_returns_id_and_kind(self, client):
        r = client.post("/v1/tenancy/orgs", json={"name": "Acme Legal"})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "org"
        assert body["tenant_id"]

    def test_create_sub_tenant_under_org(self, client):
        org = client.post("/v1/tenancy/orgs", json={"name": "O"}).json()
        r = client.post(
            f"/v1/tenancy/{org['tenant_id']}/sub-tenants",
            json={"name": "Conveyancing"},
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "sub_tenant"

    def test_sub_tenant_under_missing_org_422(self, client):
        r = client.post(
            "/v1/tenancy/missing/sub-tenants", json={"name": "X"}
        )
        assert r.status_code == 422


class TestMembers:
    def test_admin_lists_members_across_org_tree(self, client):
        org = client.post("/v1/tenancy/orgs", json={"name": "O"}).json()
        sub = client.post(
            f"/v1/tenancy/{org['tenant_id']}/sub-tenants", json={"name": "S"}
        ).json()
        client.post(
            f"/v1/tenancy/{sub['tenant_id']}/members",
            json={"user_id": "alice"},
        )
        r = client.get(f"/v1/tenancy/{org['tenant_id']}/members")
        assert r.status_code == 200
        ids = [m["user_id"] for m in r.json()["members"]]
        assert "alice" in ids

    def test_member_add(self, client):
        org = client.post("/v1/tenancy/orgs", json={"name": "O"}).json()
        r = client.post(
            f"/v1/tenancy/{org['tenant_id']}/members",
            json={"user_id": "bob"},
        )
        assert r.status_code == 200

    def test_user_resolution_walks_to_org(self, client):
        org = client.post("/v1/tenancy/orgs", json={"name": "O"}).json()
        sub = client.post(
            f"/v1/tenancy/{org['tenant_id']}/sub-tenants", json={"name": "S"}
        ).json()
        client.post(
            f"/v1/tenancy/{sub['tenant_id']}/members",
            json={"user_id": "carol"},
        )
        r = client.get("/v1/tenancy/memberships", params={"user_id": "carol"})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == sub["tenant_id"]
        assert body["org_id"] == org["tenant_id"]
