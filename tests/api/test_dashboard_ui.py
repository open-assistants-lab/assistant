"""D1-2 dashboard UI + CSV + trend tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ui_client(monkeypatch, tmp_path):
    """Telemetry on, isolated data root, metering enabled."""
    import src.storage.paths as paths_mod
    from src.config import reload_settings

    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("METERING_ENABLED", "true")
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    import src.storage.metering as metering_mod

    monkeypatch.setattr(metering_mod, "_metering_stores", {})
    monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)
    import src.storage.analytics as analytics_mod

    monkeypatch.setattr(analytics_mod, "_ANALYTICS_STORES", {}, raising=False)
    reload_settings()
    yield
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    analytics_mod.reset_analytics_stores()
    reload_settings()


@pytest.fixture()
def client(ui_client):

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _flush_two_days() -> None:
    """Two days ending today (inside any 30d window): 100s -> 50s trend."""
    from datetime import date, timedelta

    from src.storage.analytics import get_analytics_store
    from src.storage.paths import DEFAULT_USER_ID

    today = date.today()
    day1 = (today - timedelta(days=1)).isoformat()
    day2 = today.isoformat()
    store = get_analytics_store(DEFAULT_USER_ID)
    store.record_day(
        DEFAULT_USER_ID, day1, drafts_count=2, llm_calls=10,
        cost_usd=0.5, tool_calls=3, avg_turn_seconds=100.0,
    )
    store.record_day(
        DEFAULT_USER_ID, day2, drafts_count=1, llm_calls=8,
        cost_usd=0.2, tool_calls=2, avg_turn_seconds=50.0,
    )


def test_dashboard_page_renders_live_cards(client):
    _flush_two_days()
    r = client.get("/dashboard")
    assert r.status_code == 200
    html = r.text
    for marker in ("drafts produced", "hours saved", "cost / seat", "models in use"):
        assert marker in html
    # Live numbers: 3 drafts total across the two days
    assert ">3<" in html
    # Top models + trend tables present
    assert 'id="top-models"' in html
    assert 'id="trends"' in html
    assert "-50.0%" in html  # 100s -> 50s


def test_dashboard_csv_export(client):
    _flush_two_days()
    r = client.get("/v1/dashboard/summary.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "day,drafts,cost_usd,avg_turn_seconds"
    assert len(lines) == 3  # header + two day rows


def test_dashboard_trend_delta(client):
    _flush_two_days()
    r = client.get("/v1/dashboard/trend")
    body = r.json()
    assert body["enabled"] is True
    trend = body["trends"][0]
    assert trend["task_label"] == "all tasks"
    assert trend["first_duration_s"] == 100.0
    assert trend["latest_duration_s"] == 50.0
    assert trend["delta_pct"] == -50.0


def test_dashboard_opted_out_state(client, monkeypatch):
    monkeypatch.setenv("TELEMETRY_ENABLED", "false")
    from src.config import reload_settings

    reload_settings()
    r = client.get("/dashboard")
    t = client.get("/v1/dashboard/trend")
    reload_settings()
    assert r.status_code == 200
    assert "opted out" in r.text
    assert t.json()["opted_out"] is True
