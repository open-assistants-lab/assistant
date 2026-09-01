"""Owner dashboard summary (Phase 2 D1-1).

Acceptance: seed 3 runs of metering data + 2 skill drafts -> summary returns
drafts=2, derived hours_saved, cost_per_seat; opt-out user gets opt-out
state, never data.
"""

from collections import OrderedDict

import pytest


@pytest.fixture()
def _dashboard_env(monkeypatch, tmp_path):
    """Telemetry + metering on, isolated root, cache hygiene."""
    import src.storage.analytics as analytics_mod
    import src.storage.metering as metering_mod
    import src.storage.paths as paths_mod

    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("METERING_ENABLED", "true")
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
        raising=False,
    )
    # fresh caches: instances created under the patched root must not leak
    monkeypatch.setattr(paths_mod, "_paths_cache", __import__("collections").OrderedDict())
    monkeypatch.setattr(metering_mod, "_metering_stores", {})
    analytics_mod.reset_analytics_stores()
    from src.config.settings import reload_settings

    reload_settings()
    yield tmp_path
    # undo THIS fixture's patches BEFORE clearing caches / reloading settings
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    metering_mod._metering_stores.clear()
    analytics_mod.reset_analytics_stores()
    from src.config.settings import reload_settings as _rl

    _rl()


def _seed_runs(user_id: str, tmp_path, n: int = 3) -> None:
    """Seed metering rows + drafts dirs, then flush telemetry."""
    from datetime import UTC, datetime

    from src.sdk.telemetry import flush
    from src.storage.metering import UsageEventRow, get_metering_store
    from src.storage.paths import DataPaths

    store = get_metering_store(user_id)
    now = datetime.now(UTC)
    for i in range(n):
        store.record(
            UsageEventRow(
                event_id=f"seed-{user_id}-{i}",
                ts=now,
                user_id=user_id,
                model_id="ollama-cloud:deepseek-v4-flash:0731",
                input_tokens=1000 + i,
                output_tokens=200 + i,
                reasoning_tokens=5,
                cost_usd=0.01 * (i + 1),
                tool_calls=1,
            )
        )
    paths = DataPaths(user_id=user_id)
    drafts_dir = paths.user_dir / ".skill-drafts"
    for name in ("draft-a", "draft-b"):
        d = drafts_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test draft\n---\nbody",
            encoding="utf-8",
        )
    flush(user_id)  # the opportunistic loop-end flush this feature adds


@pytest.fixture()
def client(_dashboard_env):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestDashboardSummary:
    def test_opt_out_returns_state_not_data(self, monkeypatch, client):
        """Telemetry off (shipped default) -> opt-out state, no data."""
        import src.storage.analytics as analytics_mod
        from src.config.settings import reload_settings

        monkeypatch.setenv("TELEMETRY_ENABLED", "false")
        analytics_mod.reset_analytics_stores()
        reload_settings()
        r = client.get("/v1/dashboard/summary", params={"user_id": "optout_user"})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["opted_out"] is True
        assert "drafts" not in body  # no data leaks in opt-out state

    def test_seeded_runs_return_full_summary(self, client, _dashboard_env):
        _seed_runs("dash_user", _dashboard_env, n=3)
        r = client.get("/v1/dashboard/summary", params={"user_id": "dash_user"})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["drafts"] == 2
        assert body["hours_saved"] > 0  # derived from llm_calls
        assert body["cost_per_seat"] == pytest.approx(0.06)  # 0.01+0.02+0.03
        assert body["top_models"][0]["model"] == "ollama-cloud:deepseek-v4-flash:0731"
        # legacy-path parity (v1 alias mounted over the same router)
        r2 = client.get("/dashboard/summary", params={"user_id": "dash_user"})
        assert r2.status_code == 200
        assert r2.json()["drafts"] == 2

    def test_cross_user_isolation(self, client, _dashboard_env):
        _seed_runs("dash_user", _dashboard_env, n=3)
        _seed_runs("other_user", _dashboard_env, n=1)
        r = client.get("/v1/dashboard/summary", params={"user_id": "dash_user"})
        body = r.json()
        assert body["cost_per_seat"] == pytest.approx(0.06)
        assert body["drafts"] == 2
