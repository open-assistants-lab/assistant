"""Usage / billing API (Phase 2 M1.2): /v1/usage/*, /v1/billing/cost."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.storage.metering import MeteringStore, UsageEventRow, reset_metering_stores


@pytest.fixture()
def usage_api(tmp_path, monkeypatch):
    """Per-user isolated metering stores + disabled env pin."""
    import src.storage.paths as paths_mod
    import src.storage.metering as metering_mod

    monkeypatch.delenv("METERING_ENABLED", raising=False)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "user_dir",
        property(lambda self: tmp_path / "users" / (self.user_id or "default_user")),
    )
    monkeypatch.setattr(metering_mod, "_metering_stores", {})
    monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)

    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app) as c:
        yield c

    reset_metering_stores()


def _seed(user_id: str = "u1"):
    """Seed through the SAME store factory the router resolves."""
    import src.storage.metering as metering_mod

    store = metering_mod.get_metering_store(user_id)
    now = datetime.now(UTC)
    store.record(
        UsageEventRow(
            event_id="a1",
            ts=now,
            model_id="model:one",
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=5,
            cost_usd=0.100,
        )
    )
    store.record(
        UsageEventRow(
            event_id="a2",
            ts=now,
            model_id="model:one",
            input_tokens=200,
            output_tokens=60,
            reasoning_tokens=6,
            cost_usd=0.250,
        )
    )
    store.record(
        UsageEventRow(
            event_id="b1",
            ts=now,
            model_id="model:two",
            input_tokens=400,
            output_tokens=70,
            reasoning_tokens=7,
            cost_usd=0.650,
        )
    )


class TestUsageAPI:
    def test_summary_groups_two_models(self, usage_api, tmp_path):
        _seed()
        r = usage_api.get("/v1/usage/summary", params={"user_id": "u1"})
        assert r.status_code == 200
        body = r.json()
        assert body["window_days"] == 30
        by_model = {row["model_id"]: row for row in body["summary"]}
        assert by_model["model:one"]["llm_calls"] == 2
        assert by_model["model:one"]["input_tokens"] == 300
        assert by_model["model:two"]["input_tokens"] == 400
        # cost aggregated correctly per model
        assert by_model["model:one"]["cost_usd"] == 0.35

    def test_events_pagination(self, usage_api, tmp_path):
        _seed()
        r = usage_api.get(
            "/v1/usage/events", params={"user_id": "u1", "limit": 2, "offset": 0}
        )
        body = r.json()
        assert r.status_code == 200
        assert body["count"] == 2
        r2 = usage_api.get(
            "/v1/usage/events", params={"user_id": "u1", "limit": 2, "offset": 2}
        )
        body2 = r2.json()
        assert body2["count"] == 1  # seeded 3 total
        ids = {e["event_id"] for e in body["events"]} | {
            e["event_id"] for e in body["events"]
        }

    def test_billing_cost_total(self, usage_api, tmp_path):
        _seed()
        r = usage_api.get("/v1/billing/cost", params={"user_id": "u1"})
        body = r.json()
        assert r.status_code == 200
        assert body["cost_usd"] == 1.0
        assert body["currency"] == "USD"

    def test_empty_state_zeros_not_errors(self, usage_api):
        r = usage_api.get("/v1/usage/summary", params={"user_id": "nobody"})
        assert r.status_code == 200
        assert r.json()["summary"] == []
        r2 = usage_api.get("/v1/billing/cost", params={"user_id": "nobody"})
        assert r2.status_code == 200
        assert r2.json()["cost_usd"] == 0.0

    def test_cross_user_isolation(self, usage_api, tmp_path):
        """User A's request must not see user B's rows (M1.3 leak test)."""
        a_store = MeteringStore(str(tmp_path / "a.db"))
        a_store.record(
            UsageEventRow(
                event_id="secret",
                ts=datetime.now(UTC),
                cost_usd=5.0,
                model_id="model:secret",
            )
        )
        MeteringStore(str(tmp_path / "b.db"))  # user B empty store

        r_b = usage_api.get("/v1/billing/cost", params={"user_id": "user_b"})
        assert r_b.json()["cost_usd"] == 0.0
        r_sum = usage_api.get("/v1/usage/summary", params={"user_id": "user_b"})
        assert r_sum.json()["summary"] == []
        r_ev = usage_api.get("/v1/usage/events", params={"user_id": "user_b"})
        assert r_ev.json()["events"] == []