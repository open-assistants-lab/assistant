"""Per-user API key auth (Phase 2 M2.1): generate -> use -> verify -> revoke."""

from __future__ import annotations

import pytest


@pytest.fixture()
def _isolated_auth(monkeypatch, tmp_path):
    """Flag on, API_KEY set (admin gate), isolated data root."""
    monkeypatch.setenv("PER_USER_AUTH", "true")
    monkeypatch.setenv("API_KEY", "admin-secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    import src.storage.paths as paths_mod
    from src.config.settings import reload_settings

    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    import src.auth.keys as keys_mod

    monkeypatch.setattr(keys_mod, "_STORES", {})
    import src.http.auth as http_auth

    monkeypatch.setattr(http_auth, "_DEFAULT_RESOLVER", None)
    reload_settings()
    yield
    # Undo THIS fixture's patches first (env + DataPaths.root) — reloading
    # settings while API_KEY/SOLO_BYPASS are still patched would persist an
    # auth-on singleton into every later test (401 pollution).
    monkeypatch.undo()
    # get_paths caches DataPaths instances by (user_id, tenant, workspace) —
    # instances created under the patched root must not leak into other tests.
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def client(_isolated_auth):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _gen_key(client, user_id="alice", **kw):
    r = client.post(
        "/auth/keys",
        json={"user_id": user_id, **kw},
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert r.status_code == 200
    return r.json()["key"]


class TestKeyLifecycle:
    def test_generate_returns_plaintext_once(self, client):
        key = _gen_key(client, "alice")
        assert key.startswith("oak_")

    def test_generate_verify_use(self, client):
        key = _gen_key(client, "alice")
        from src.auth.keys import get_key_store

        verified = get_key_store().verify(key)
        assert verified == ("alice", "")
        # The key authenticates as its owner on a data endpoint.
        r = client.get(
            "/conversation", params={"user_id": "alice"},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200

    def test_revoked_key_rejected(self, client):
        key = _gen_key(client, "bob")
        r = client.post(
            "/auth/keys",
            json={"revoke": key},
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert r.status_code == 200 and r.json()["revoked"] is True
        # Revoked key -> 401 (middleware, non-localhost test client).
        r = client.get(
            "/conversation", params={"user_id": "bob"},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 401

    def test_wrong_user_id_403(self, client):
        key = _gen_key(client, "alice")
        r = client.get(
            "/conversation", params={"user_id": "mallory"},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 403

    def test_absent_key_401_non_localhost(self, client):
        r = client.get("/conversation", params={"user_id": "alice"})
        assert r.status_code == 401

    def test_admin_gate_required(self, client):
        # No admin key -> key generation 401s (middleware) even though the
        # endpoint would otherwise mint keys.
        r = client.post("/auth/keys", json={"user_id": "x"})
        assert r.status_code == 401

    def test_flag_off_legacy_unchanged(self, client, monkeypatch):
        """Flag off: shared-secret path unchanged, oak_ keys are inert."""
        from src.config.settings import reload_settings

        monkeypatch.setenv("PER_USER_AUTH", "false")
        reload_settings()
        key = _gen_key(client, "alice")  # admin still works via API_KEY
        r = client.get(
            "/conversation", params={"user_id": "alice"},
            headers={"Authorization": f"Bearer {key}"},
        )
        # Per-user resolver inactive -> oak_ key isn't a shared secret -> 401.
        assert r.status_code == 401