"""M3-1: tenant store, budget 402 enforcement, billing endpoints."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# TenantStore CRUD
# ---------------------------------------------------------------------------
class TestTenantStore:
    def test_upsert_and_persist(self, tmp_path):
        from src.storage.tenant import TenantStore

        db = str(tmp_path / "tenant.db")
        store = TenantStore(db)
        tid = store.upsert_tenant(
            name="acme", plan="seat", seat_count=5, monthly_budget_usd=50.0
        )
        # plan switch persists (M3 acceptance)
        store.set_plan(tid, "smb")
        t = store.get_tenant(tid)
        assert t["name"] == "acme"
        assert t["plan"] == "smb"
        assert t["seat_count"] == 5
        # fresh instance reads the same rows (persistence)
        assert TenantStore(db).get_tenant(tid)["plan"] == "smb"

    def test_memberships(self, tmp_path):
        from src.storage.tenant import TenantStore

        store = TenantStore(str(tmp_path / "tenant.db"))
        tid = store.upsert_tenant(name="acme", plan="free", seat_count=1)
        store.add_member(tid, "alice")
        store.add_member(tid, "bob")
        assert sorted(store.members(tid)) == ["alice", "bob"]
        assert store.tenant_for_user("bob")["id"] == tid
        assert store.tenant_for_user("nobody") is None


# ---------------------------------------------------------------------------
# Billing endpoints (budget enforcement tested via endpoints)
# ---------------------------------------------------------------------------
@pytest.fixture()
def billing_api(tmp_path, monkeypatch):
    """Isolated tenant.db + metering stores + solo-mode client."""
    import src.storage.paths as paths_mod
    import src.storage.tenant as tenant_mod
    import src.storage.metering as metering_mod
    from src.config.settings import reload_settings

    monkeypatch.delenv("METERING_ENABLED", raising=False)
    monkeypatch.delenv("PER_USER_AUTH", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SOLO_BYPASS", raising=False)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "user_dir",
        property(lambda self: tmp_path / "users" / (self.user_id or "default_user")),
    )
    monkeypatch.setattr(paths_mod, "get_paths", lambda *a, **k: paths_mod.DataPaths(
        user_id=(a[0] if a else k.get("user_id")),
        data_path=str(tmp_path / "data"),
        data_root=str(tmp_path / "root"),
    ))
    monkeypatch.setattr(tenant_mod, "_TENANT_STORE", None)
    monkeypatch.setattr(metering_mod, "_metering_stores", {})
    monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)
    reload_settings()

    from src.http.main import app

    with TestClient(app) as c:
        yield c

    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def seeded_tenant(billing_api):
    """Firm with 5 seats capped at $10/mo; alice over budget, bob under."""
    from src.storage import metering as metering_mod
    from src.storage.tenant import get_tenant_store

    t = get_tenant_store().upsert_tenant(
        name="firm", plan="seat", seat_count=5, monthly_budget_usd=10.0
    )
    get_tenant_store().add_member(t, "alice")
    get_tenant_store().add_member(t, "bob")
    metering_mod.get_metering_store("alice").record(
        metering_mod.UsageEventRow(
            event_id="e1",
            ts=datetime.now(UTC),
            user_id="alice",
            model_id="model:one",
            input_tokens=10_000,
            output_tokens=5_000,
            reasoning_tokens=0,
            cost_usd=12.0,
            tool_calls=0,
        )
    )
    return t


class TestBudgetEnforcement:
    def test_over_budget_member_402_billing_shape(self, billing_api, seeded_tenant):
        r = billing_api.post("/message", json={"message": "hi", "user_id": "bob"})
        assert r.status_code == 402
        body = r.json()
        assert body["code"] == "billing"
        assert "budget" in body["message"].lower()
        assert body["details"]["budget_usd"] == 10.0
        assert body["details"]["plan"] == "seat"

    def test_v1_alias_enforces_too(self, billing_api, seeded_tenant):
        r = billing_api.post("/v1/message", json={"message": "hi", "user_id": "bob"})
        assert r.status_code == 402
        assert r.json()["code"] == "billing"

    def test_unaffiliated_user_not_blocked(self, billing_api, seeded_tenant):
        # a user with no tenant membership is never budget-blocked
        r = billing_api.post("/message", json={"message": "hi", "user_id": "solo"})
        assert r.status_code != 402

    def test_over_budget_user_also_blocked_on_stream(self, billing_api, seeded_tenant):
        # alice is the over-budget member: blocked on the streaming surface too
        r = billing_api.post(
            "/message/stream", json={"message": "hi", "user_id": "alice"}
        )
        assert r.status_code == 402
        assert r.json()["code"] == "billing"


class TestBillingEndpoints:
    def test_get_tenant_shape(self, billing_api, seeded_tenant):
        r = billing_api.get("/v1/billing/tenant", params={"user_id": "bob"})
        assert r.status_code == 200
        body = r.json()
        assert body["plan"] == "seat"
        assert body["seat_count"] == 5
        assert body["monthly_budget_usd"] == 10.0
        assert body["mtd_cost_usd"] >= 12.0  # alice's overspend aggregates

    def test_plan_switch_persists(self, billing_api, seeded_tenant):
        r = billing_api.post(
            "/v1/billing/plan", json={"tenant_id": seeded_tenant, "plan": "smb"}
        )
        assert r.status_code == 200
        from src.storage.tenant import get_tenant_store

        assert get_tenant_store().get_tenant(seeded_tenant)["plan"] == "smb"

    def test_plan_switch_rejects_unknown_plan(self, billing_api, seeded_tenant):
        r = billing_api.post(
            "/v1/billing/plan", json={"tenant_id": seeded_tenant, "plan": "enterprise"}
        )
        assert r.status_code == 422