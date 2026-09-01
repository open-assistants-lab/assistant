"""Metering tests (Phase 2 M1.1/M1.3): store, sink, emission, snapshot."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import src.storage.metering as metering
from src.storage.metering import (
    MeteringStore,
    UsageEventRow,
    ensure_metering_sink,
    get_metering_store,
    reset_metering_stores,
)


def _patch_user_dir(monkeypatch, tmp_path) -> None:
    """Isolate per-user DataPaths under tmp_path (metering.db placement)."""
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "user_dir",
        property(lambda self: tmp_path / "users" / (self.user_id or "default_user")),
    )


def _store(tmp_path, name="m.db") -> MeteringStore:
    return MeteringStore(str(tmp_path / name))


def _sink_count() -> int:
    from src.sdk.audit import default_capture_bus

    return len(default_capture_bus._sinks)


class TestMeteringStore:
    def test_record_and_exact_counts_roundtrip(self, tmp_path):
        store = _store(tmp_path, "rt.db")
        store.record(
            UsageEventRow(
                event_id="e1",
                ts=datetime.now(UTC),
                user_id="u1",
                model_id="m1",
                input_tokens=123,
                output_tokens=45,
                reasoning_tokens=7,
                cost_usd=0.5,
                run_id="run-1",
            )
        )
        rows = store.events()
        assert len(rows) == 1
        assert rows[0].input_tokens == 123
        assert rows[0].output_tokens == 45
        assert rows[0].reasoning_tokens == 7
        assert rows[0].run_id == "run-1"
        # user_id persisted (was silently dropped before the schema fix)
        assert rows[0].user_id == "u1"

    def test_summary_groups_by_day_model(self, tmp_path):
        store = _store(tmp_path, "s.db")
        now = datetime.now(UTC)
        store.record(UsageEventRow(event_id="a", ts=now, model_id="m1", input_tokens=10, output_tokens=5, cost_usd=0.1))
        store.record(UsageEventRow(event_id="b", ts=now, model_id="m2", input_tokens=20, output_tokens=8, cost_usd=0.2))
        summary = store.summary(30)
        assert len(summary) == 2
        assert sum(r.input_tokens for r in summary) == 30

    def test_pagination_limit_offset_newest_first(self, tmp_path):
        store = _store(tmp_path, "p.db")
        now = datetime.now(UTC)
        for i in range(5):
            store.record(UsageEventRow(event_id=f"e{i}", ts=now, input_tokens=i))
        page1 = store.events(limit=2, offset=0)
        page2 = store.events(limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 2
        assert {r.event_id for r in page1}.isdisjoint({r.event_id for r in page2})

    def test_total_cost_window(self, tmp_path):
        store = _store(tmp_path, "c.db")
        store.record(UsageEventRow(event_id="c1", ts=datetime.now(UTC), cost_usd=0.25))
        store.record(UsageEventRow(event_id="c2", ts=datetime.now(UTC), cost_usd=0.25))
        assert store.total_cost(30) == 0.5

    def test_empty_state_zeros_not_errors(self, tmp_path):
        store = _store(tmp_path, "e.db")
        assert store.total_cost(30) == 0.0
        assert store.events() == []
        assert store.summary(30) == []
        snap = store.snapshot(30)
        assert snap.rows == 0
        assert snap.cost_usd == 0.0
        assert snap.by_model == {}


class TestSinkSubscription:
    def test_disabled_default_no_subscribe(self, monkeypatch):
        monkeypatch.setattr(metering, "metering_enabled", lambda: False)
        assert ensure_metering_sink("x") is None

    def test_enabled_subscribes_and_routes_by_event_user(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metering, "metering_enabled", lambda: True)
        _patch_user_dir(monkeypatch, tmp_path)
        from src.sdk.audit import AuditEvent, default_capture_bus

        assert ensure_metering_sink("sub_user") is not None
        ev = AuditEvent(kind="usage", user_id="routed")
        ev.usage_input_tokens = 10
        ev.usage_output_tokens = 5
        default_capture_bus.emit(ev)
        rows = get_metering_store("routed").events()
        assert len(rows) == 1
        assert rows[0].user_id == "routed"

    def test_no_duplicate_sink_on_repeated_ensure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metering, "metering_enabled", lambda: True)
        _patch_user_dir(monkeypatch, tmp_path)
        ensure_metering_sink("d1")
        n1 = _sink_count()
        ensure_metering_sink("d2")
        # Identity guard: repeated ensure never stacks a second sink.
        assert _sink_count() == n1

    def test_reset_unsubscribes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metering, "metering_enabled", lambda: True)
        _patch_user_dir(monkeypatch, tmp_path)
        ensure_metering_sink("r1")
        assert _sink_count() >= 1
        reset_metering_stores()
        assert _sink_count() == 0


class TestLoopEndToEndMetering:
    async def test_loop_run_writes_metering_row_exact_counts(
        self, monkeypatch, tmp_path
    ):
        """Plan M1.1 acceptance: loop + fake provider -> metering row with
        exact token counts, stamped user/model/run (M1 review P1/P2)."""
        import asyncio

        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message, Usage

        monkeypatch.setattr(metering, "metering_enabled", lambda: True)
        monkeypatch.setattr(metering, "_metering_stores", {})
        monkeypatch.setattr(metering, "_metering_sink_fn", None)
        monkeypatch.setattr(metering, "_metering_sink_subscribed", False)
        _patch_user_dir(monkeypatch, tmp_path)

        class UsageProvider:
            async def chat(self, *a, **k):
                return Message.assistant(
                    "Hello!",
                    usage=Usage(input_tokens=123, output_tokens=45, reasoning_tokens=7),
                )

        assert ensure_metering_sink("meter_user") is not None

        loop = AgentLoop(
            provider=UsageProvider(),
            tools=[],
            user_id="meter_user",
            run_config=RunConfig(max_llm_calls=3),
        )
        # Runner wiring stamps _flow_model on production loops; simulate it.
        loop._flow_model = "test-model"
        await asyncio.wait_for(loop.run([Message.user("Hi")]), timeout=10)

        rows = get_metering_store("meter_user").events()
        assert len(rows) == 1
        assert rows[0].input_tokens == 123
        assert rows[0].output_tokens == 45
        assert rows[0].reasoning_tokens == 7
        assert rows[0].user_id == "meter_user"
        assert rows[0].model_id == "test-model"  # review P1: model stamped
        assert rows[0].run_id    # review P2: per-run correlation stamped


class TestSnapshotAggregation:
    """M1.3: per-seat snapshot + multi-user aggregate."""

    def _seed(self, store, user, model="m1", cost=0.5, n=2):
        for i in range(n):
            store.record(
                UsageEventRow(
                    event_id=f"{user}-{model}-{i}",
                    ts=datetime.now(UTC),
                    user_id=user,
                    model_id=model,
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=1,
                    cost_usd=cost,
                )
            )

    def test_snapshot_totals_match_seeded_rows(self, tmp_path):
        store = _store(tmp_path, "snap.db")
        store.record(UsageEventRow(event_id="s1", ts=datetime.now(UTC), model_id="m1", input_tokens=123, output_tokens=45, reasoning_tokens=7, cost_usd=0.5, tool_calls=2))
        store.record(UsageEventRow(event_id="s2", ts=datetime.now(UTC), model_id="m1", input_tokens=10, output_tokens=5, cost_usd=0.25))
        store.record(UsageEventRow(event_id="s3", ts=datetime.now(UTC), model_id="m2", input_tokens=50, output_tokens=50, cost_usd=1.0, tool_calls=3))
        snap = store.snapshot(30)
        assert snap.rows == 3
        assert snap.llm_calls == 3
        assert snap.input_tokens == 183  # 123 + 10 + 50
        assert snap.by_model["m1"]["llm_calls"] == 2
        assert snap.by_model["m1"]["cost_usd"] == pytest.approx(0.75, abs=1e-6)
        assert snap.by_model["m2"]["llm_calls"] == 1

    def test_snapshot_per_model_breakdown(self, tmp_path):
        store = _store(tmp_path, "pm.db")
        store.record(UsageEventRow(event_id="x1", ts=datetime.now(UTC), model_id="expensive", input_tokens=1, cost_usd=1.0))
        store.record(UsageEventRow(event_id="x2", ts=datetime.now(UTC), model_id="cheap", input_tokens=2, cost_usd=0.1))
        snap = store.snapshot(30)
        assert set(snap.by_model) == {"expensive", "cheap"}
        # by_model ordered by cost desc
        assert list(snap.by_model)[0] == "expensive"

    def test_aggregate_users_no_cross_leak(self, tmp_path, monkeypatch):
        _patch_user_dir(monkeypatch, tmp_path)
        a = get_metering_store("alice")
        b = get_metering_store("bob")
        a.record(UsageEventRow(event_id="a1", ts=datetime.now(UTC), user_id="alice", model_id="m1", input_tokens=10, cost_usd=0.1))
        b.record(UsageEventRow(event_id="b1", ts=datetime.now(UTC), user_id="bob", model_id="m2", input_tokens=999, cost_usd=9.0))

        agg = metering.aggregate_users({"alice": a, "b_user": b})

        assert set(agg) == {"alice", "b_user"}
        # No leak: each user's snapshot reflects only their own store.
        assert agg["alice"].input_tokens == 10
        assert agg["alice"].cost_usd == 0.1
        assert agg["b_user"].input_tokens == 999
        assert agg["b_user"].cost_usd == 9.0
        # alice's snapshot never contains b_user data
        assert "m2" not in agg["alice"].by_model

class _UsageProvider:
    async def chat(self, *a, **k):
        from src.sdk.messages import Message, Usage

        return Message.assistant(
            "Hello!",
            usage=Usage(input_tokens=123, output_tokens=45, reasoning_tokens=7),
        )


def _run_config(**kw):
    from src.sdk.loop import RunConfig

    return RunConfig(**kw)


def _user_msg(text):
    from src.sdk.messages import Message

    return Message.user(text)


def metering_mod():
    return metering


def ensure_sink_for(user):
    return metering.ensure_metering_sink(user)


def get_store(user):
    return get_metering_store(user)

    async def test_run_stream_rows_have_fresh_run_id(self, monkeypatch, tmp_path):
        """M1 review P1: run_stream() must stamp a fresh per-run run_id —
        cached loops previously re-emitted the previous run's id. Two
        streamed runs must produce rows with distinct run_ids."""
        import asyncio

        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message, Usage

        monkeypatch.setattr(metering, "metering_enabled", lambda: True)
        monkeypatch.setattr(metering, "_metering_stores", {})
        monkeypatch.setattr(metering, "_metering_sink_fn", None)
        monkeypatch.setattr(metering, "_metering_sink_subscribed", False)
        _patch_user_dir(monkeypatch, tmp_path)

        class UsageProvider:
            async def chat(self, *a, **k):
                return Message.assistant(
                    "Hello!",
                    usage=Usage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
                )

        assert ensure_metering_sink("stream_user") is not None

        loop = AgentLoop(
            provider=UsageProvider(),
            tools=[],
            user_id="stream_user",
            run_config=RunConfig(max_llm_calls=3),
        )
        loop._flow_model = "test-model"
        async def _consume():
            async for _ in loop.run_stream([Message.user("Hi")]):
                pass
        await asyncio.wait_for(_consume(loop.run_stream([Message.user("Hi")])), timeout=10)
        first = get_metering_store("stream_user").events()
        assert first and first[0].run_id

        await asyncio.wait_for(_consume(loop.run_stream([Message.user("Hi again")])), timeout=10)
        rows = get_metering_store("stream_user").events()
        assert len(rows) == 2
        assert rows[0].run_id and rows[1].run_id
        assert rows[0].run_id != rows[1].run_id  # fresh per streamed run
