"""Usage metering (Phase 2 M1.1): sink, store, and loop-side emission."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.sdk.audit import AuditEvent, default_capture_bus
from src.storage.metering import (
    MeteringStore,
    UsageEventRow,
    ensure_metering_sink,
    get_metering_store,
    reset_metering_stores,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolate stores + disable metering by default (OSS no-op baseline)."""
    import src.storage.paths as paths_mod
    from unittest.mock import patch

    monkeypatch.setenv("METERING_ENABLED", "")
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "user_dir",
        property(lambda self: tmp_path / "users" / (self.user_id or "default_user")),
    )
    # MeteringStore db path flows through metering_db() -> user_dir
    import src.storage.metering as metering_mod

    monkeypatch.setattr(metering_mod, "_metering_stores", {})
    monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)
    yield
    reset_metering_stores()


def _enable(monkeypatch):
    from src.config.settings import MeteringConfig

    monkeypatch.setattr("src.config.settings.AppConfig.model_fields", {}, raising=False)
    import src.config.settings as settings_mod

    # patch metering_enabled directly for sink tests
    import src.storage.metering as metering_mod

    monkeypatch.setattr(metering_mod, "metering_enabled", lambda: True)
    return MeteringConfig(enabled=True)


class TestMeteringStore:
    def test_record_and_exact_counts_roundtrip(self, tmp_path):
        store = MeteringStore(str(tmp_path / "metering.db"))
        store.record(
            UsageEventRow(
                event_id="e1",
                ts=datetime.now(UTC),
                user_id="u1",
                model_id="openai:gpt-4o",
                input_tokens=123,
                output_tokens=45,
                reasoning_tokens=7,
                cost_usd=0.0015,
                tool_calls=2,
            )
        )
        rows = store.events()
        assert len(rows) == 1
        assert rows[0].input_tokens == 123
        assert rows[0].output_tokens == 45
        assert rows[0].reasoning_tokens == 7
        assert rows[0].cost_usd == pytest.approx(0.0015)

    def test_summary_groups_by_day_model(self, tmp_path):
        store = MeteringStore(str(tmp_path / "m.db"))
        now = datetime.now(UTC)
        for i, (model, out_tokens) in enumerate(
            [("a:model", 10), ("a:model", 20), ("b:model", 5)]
        ):
            store.record(
                UsageEventRow(
                    event_id=f"e{i}",
                    ts=now - timedelta(hours=i),
                    model_id=model,
                    input_tokens=100,
                    output_tokens=out_tokens,
                    cost_usd=0.01,
                )
            )
        summary = store.summary(window_days=30)
        by = {(r.day, r.model_id): r for r in summary}
        assert len(by) == 2
        a = by[(summary[0].day, "a:model")] or min(by.values())
        model_a = [r for r in summary if r.model_id == "a:model"][0]
        assert model_a.llm_calls == 2
        assert model_a.input_tokens == 200
        assert model_a.output_tokens == 30

    def test_pagination_limit_offset_newest_first(self, tmp_path):
        store = MeteringStore(str(tmp_path / "m.db"))
        now = datetime.now(UTC)
        for i in range(5):
            store.record(
                UsageEventRow(event_id=f"e{i}", ts=now - timedelta(minutes=i))
            )
        page1 = store.events(limit=2, offset=0)
        page2 = store.events(limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 2
        # newest first: page1 starts at e0, page2 continues
        assert page1[0].event_id == "e0"
        assert page2[0].event_id == "e2"

    def test_total_cost_window(self, tmp_path):
        store = MeteringStore(str(tmp_path / "m.db"))
        now = datetime.now(UTC)
        store.record(UsageEventRow(event_id="a", ts=now, cost_usd=1.5))
        store.record(
            UsageEventRow(
                event_id="b", ts=now - timedelta(days=40), cost_usd=99.0
            )
        )
        assert store.total_cost(window_days=30) == 1.5

    def test_empty_state_zeros_not_errors(self, tmp_path):
        store = MeteringStore(str(tmp_path / "m.db"))
        assert store.summary() == []
        assert store.events() == []
        assert store.total_cost() == 0.0


class TestSinkSubscription:
    def test_disabled_default_no_subscribe(self, monkeypatch):
        import src.storage.metering as metering_mod

        monkeypatch.setattr(metering_mod, "metering_enabled", lambda: False)
        result = ensure_metering_sink("u1")
        assert result is None
        # no sink subscribed: emitting reaches nothing (no crash)
        default_capture_bus.emit(
            AuditEvent(
                kind="usage",
                usage_input_tokens=10,
            )
        )
        assert metering_mod._metering_sink_subscribed is False

    def test_enabled_subscribes_and_routes_by_event_user(self, monkeypatch, tmp_path):
        import src.storage.metering as metering_mod
        from unittest.mock import patch

        monkeypatch.setattr(metering_mod, "metering_enabled", lambda: True)
        sink_store = MeteringStore(str(tmp_path / "sink.db"))
        monkeypatch.setattr(metering_mod, "_metering_stores", {"u1": sink_store})

        monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)
        result = ensure_metering_sink("u1")
        assert result is not None

        default_capture_bus.emit(
            AuditEvent(
                kind="usage",
                user_id="u1",
                session_id="s1",
                model_id="ollama-cloud:deepseek-v4-flash:0731",
                usage_input_tokens=42,
                usage_output_tokens=17,
                usage_reasoning_tokens=3,
                usage_cost_usd=0.002,
                tool_calls=1,
            )
        )
        rows = sink_store.events()
        assert len(rows) == 1
        assert rows[0].input_tokens == 42
        assert rows[0].output_tokens == 17
        assert rows[0].reasoning_tokens == 3
        assert rows[0].model_id == "ollama-cloud:deepseek-v4-flash:0731"


class TestLoopEmission:
    def test_add_usage_emits_usage_event_with_exact_counts(self, tmp_path):
        """Run CostTracker with an emit hook — capture the emitted AuditEvent."""
        captured: list[AuditEvent] = []

        def emit(event: AuditEvent) -> None:
            captured.append(event)

        from src.sdk.loop import CostTracker

        tracker = CostTracker(emit_usage=emit)
        tracker.add_usage(input_tokens=100, output_tokens=50, reasoning_tokens=10)

        assert len(captured) == 1
        ev = captured[0]
        assert ev.kind == "usage"
        assert ev.usage_input_tokens == 100
        assert ev.usage_output_tokens == 50
        assert ev.usage_reasoning_tokens == 10

    def test_bare_tracker_does_not_emit(self):
        from src.sdk.loop import CostTracker

        tracker = CostTracker()
        tracker.add_usage(input_tokens=10)  # no hook -> silent
        assert tracker.total_input_tokens == 10

    def test_cost_delta_emitted_incrementally(self):
        captured: list[AuditEvent] = []
        from src.sdk.loop import CostTracker
        from src.sdk.providers.base import ModelCost

        tracker = CostTracker(emit_usage=captured.append)
        cost = ModelCost(input=1.0, output=2.0)
        tracker.add_usage(input_tokens=1_000_000, output_tokens=500_000, cost=cost)
        assert captured[-1].usage_cost_usd == pytest.approx(1_000_000 / 1e6 + 500_000 / 1e6 * 2.0, abs=1e-6)

class TestLoopEndToEndMetering:
    async def test_loop_run_writes_metering_row_exact_counts(self, monkeypatch, tmp_path):
        """Plan M1.1 acceptance: run the loop with a fake provider ->
        metering row written with exact token counts."""
        import src.storage.metering as metering_mod
        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message, Usage

        monkeypatch.setattr(metering_mod, "metering_enabled", lambda: True)
        monkeypatch.setattr(metering_mod, "_metering_stores", {})
        monkeypatch.setattr(metering_mod, "_metering_sink_subscribed", False)
        import src.storage.paths as paths_mod

        monkeypatch.setattr(
            paths_mod.DataPaths,
            "user_dir",
            property(lambda self: tmp_path / "users" / (self.user_id or "default_user")),
        )

        class UsageProvider:
            async def chat(self, *a, **k):
                return Message.assistant(
                    "Hello!",
                    usage=Usage(input_tokens=123, output_tokens=45, reasoning_tokens=7),
                )

        sink_store = ensure_metering_sink("meter_user")
        assert sink_store is not None

        loop = AgentLoop(
            provider=UsageProvider(),
            tools=[],
            user_id="meter_user",
            run_config=RunConfig(max_llm_calls=3),
        )
        await asyncio.wait_for(loop.run([Message.user("Hi")]), timeout=10)

        rows = metering_mod.get_metering_store("meter_user").events()
        assert len(rows) == 1
        assert rows[0].input_tokens == 123
        assert rows[0].output_tokens == 45
        assert rows[0].reasoning_tokens == 7
        assert rows[0].user_id == "meter_user"
