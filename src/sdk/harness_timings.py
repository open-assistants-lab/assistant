"""Per-run harness latency waterfall accumulator.

Collects stage timings for one agent run and renders the
``harness.waterfall`` log payload. All instrumentation is best-effort:
failures degrade to no-op so timing can never break a run.
"""

from __future__ import annotations

import time
from typing import Any


class HarnessTimings:
    """Accumulates stage durations (ms) for a single run.

    Stages: context_assembly, provider_ttft, provider_total, tool_exec,
    verification, persistence. tool_exec is an aggregate (sum + count).
    """

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._started_at = time.monotonic()

    def record(self, stage: str, duration_ms: float) -> None:
        """Record a stage duration (idempotent per stage: last wins)."""
        try:
            self._stages[stage] = float(duration_ms)
        except Exception:
            pass

    def add(self, stage: str, duration_ms: float) -> None:
        """Accumulate into an aggregate stage (e.g. tool_exec)."""
        try:
            self._stages[stage] = self._stages.get(stage, 0.0) + float(duration_ms)
            self._counts[stage] = self._counts.get(stage, 0) + 1
        except Exception:
            pass

    def stage_ms(self, stage: str) -> float | None:
        return self._stages.get(stage)

    def count(self, stage: str) -> int:
        return self._counts.get(stage, 0)

    def total_ms(self) -> float:
        return (time.monotonic() - self._started_at) * 1000.0

    def to_log_data(self) -> dict[str, Any]:
        """Render the waterfall payload (rounds to integer ms)."""
        stages = {k: int(round(v)) for k, v in self._stages.items()}
        for stage, count in self._counts.items():
            stages[f"{stage}_count"] = count
        return {
            "stages_ms": stages,
            "total_ms": int(round(self.total_ms())),
        }
