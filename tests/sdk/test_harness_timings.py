"""Tests for the harness latency waterfall accumulator (E spec)."""

from __future__ import annotations

import time

from src.sdk.harness_timings import HarnessTimings


class TestHarnessTimings:
    def test_record_and_aggregate(self):
        t = HarnessTimings()
        t.record("context_assembly", 12.4)
        t.record("provider_total", 4100.0)
        t.add("tool_exec", 100.0)
        t.add("tool_exec", 250.0)
        t.add("tool_exec", 50.0)

        data = t.to_log_data()
        assert data["stages_ms"]["context_assembly"] == 12
        assert data["stages_ms"]["provider_total"] == 4100
        assert data["stages_ms"]["tool_exec"] == 400
        assert data["stages_ms"]["tool_exec_count"] == 3
        assert data["total_ms"] >= 0

    def test_record_last_wins(self):
        t = HarnessTimings()
        t.record("verification", 100.0)
        t.record("verification", 250.0)
        assert t.stage_ms("verification") == 250.0

    def test_stage_ms_absent_returns_none(self):
        t = HarnessTimings()
        assert t.stage_ms("nope") is None
        assert t.count("nope") == 0

    def test_total_ms_tracks_elapsed(self):
        t = HarnessTimings()
        time.sleep(0.01)
        assert t.total_ms() >= 5.0

    def test_bad_values_degrade_to_noop(self):
        t = HarnessTimings()
        t.record("x", "not-a-number")  # noqa: BLE001 - must not raise
        t.add("y", None)  # noqa: BLE001
        assert t.stage_ms("x") is None
        assert t.count("y") == 0

    def test_to_log_data_has_no_aggregate_count_without_adds(self):
        t = HarnessTimings()
        t.record("context_assembly", 5.0)
        data = t.to_log_data()
        assert "context_assembly_count" not in data["stages_ms"]
