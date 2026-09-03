"""T3.2/M4: self-approval separation — creator cannot approve own pending.

Governance pending created by staff user X; the SAME user's approve call
must 403. A deployment-admin identity (trusted-network) can approve.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gov_env(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod
    from src.config.settings import reload_settings

    monkeypatch.setenv("PER_USER_AUTH", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    import src.http.auth as http_auth
    import src.sdk.governance as gov

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    monkeypatch.setattr(http_auth, "_DEFAULT_RESOLVER", None)
    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    from src.config import settings as settings_module

    settings_module._config = None
    import src.storage.paths as pm

    pm._paths_cache.clear()
    yield tmp_path
    monkeypatch.undo()
    pm._paths_cache.clear()
    reload_settings()


def _client():
    from src.http.main import app

    return TestClient(app, raise_server_exceptions=False)


def _key_for(user_id, scopes=""):
    from src.auth.keys import get_key_store

    return get_key_store().generate(user_id, scopes=scopes)


def _make_pending(user_id: str, gov_svc) -> str:
    return gov_svc.create_pending(
        user_id, "jobs_confirm", {"x": "1"}, tier="explicit"
    )


def test_creator_cannot_self_approve(gov_env):
    import src.sdk.governance as gov

    gov_svc = gov.GovernanceService()
    pid = _make_pending("staff_u", gov_svc)

    key = _key_for("staff_u")  # the SAME user who created it
    r = _client().post(
        f"/v1/governance/pendings/{pid}/approve",
        params={"user_id": "staff_u"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
    assert "second party" in r.json()["detail"]


def test_deployment_admin_can_approve_others_pending(gov_env, monkeypatch):
    import src.sdk.governance as gov

    gov_svc = gov.GovernanceService()
    pid = _make_pending("staff_u", gov_svc)

    # Deployment admin: trusted-network identity (API_KEY shared secret).
    monkeypatch.setenv("API_KEY", "operator-key")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    from src.config.settings import reload_settings

    reload_settings()
    r = _client().post(
        f"/v1/governance/pendings/{pid}/approve",
        params={"user_id": "staff_u"},
        headers={"Authorization": "Bearer operator-key"},
    )
    assert r.status_code == 200
    row = gov_svc.get_pending("staff_u", pid)
    assert row is not None and row["status"] in ("approved", "executed")
