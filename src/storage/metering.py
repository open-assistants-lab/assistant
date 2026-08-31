"""Usage metering store (Phase 2 M1.1).

Per-user SQLite `metering.db` holding one row per usage event. Mirrors
AuditStore conventions (P0-T3): write-only `record` from the loop-side sink,
read-only aggregates for the billing API (M1.2). No update/delete/upsert.

Subscribes as the SECOND sink on the same CaptureBus as the audit store —
only when metering is enabled (`METERING_ENABLED` env); default disabled
makes the whole path an OSS no-op (plan M1.1 acceptance).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from src.storage.paths import DataPaths


class UsageEventRow(BaseModel):
    """One LLM-usage record (mirrors AuditEvent usage fields)."""

    event_id: str
    ts: datetime
    user_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0


class UsageSummaryRow(BaseModel):
    day: str = Field(description="UTC date, YYYY-MM-DD")
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0


def metering_enabled() -> bool:
    """Metering is OFF by default (OSS no-op) unless METERING_ENABLED."""
    from src.config import get_settings

    metering = getattr(get_settings(), "metering", None)
    return bool(metering is not None and metering.enabled)


_metering_stores: dict[str, MeteringStore] = {}
# RLock: ensure_metering_sink holds it while calling get_metering_store — a
# re-entrant acquisition on the same thread (plain Lock deadlocked there).
_metering_lock = threading.RLock()
_metering_sink_subscribed = False


def get_metering_store(user_id: str) -> MeteringStore:
    """Per-user MeteringStore singleton (mirrors audit _audit_stores)."""
    store = _metering_stores.get(user_id)
    if store is not None:
        return store
    with _metering_lock:
        store = _metering_stores.get(user_id)
        if store is not None:
            return store
        store = MeteringStore(str(DataPaths(user_id=user_id).metering_db()))
        _metering_stores[user_id] = store
        return store


def reset_metering_stores() -> None:
    """Test helper: drop the per-user store cache and sink subscription."""
    global _metering_sink_subscribed
    with _metering_lock:
        _metering_stores.clear()
        _metering_sink_subscribed = False


def ensure_metering_sink(user_id: str) -> MeteringStore | None:
    """Subscribe a metering sink to the default CaptureBus — once per process,
    only when metering is enabled. Disabled => returns None, nothing is
    subscribed (OSS default: no-op). Events carry their own user_id, so one
    subscription serves every user's per-user store.
    """
    global _metering_sink_subscribed
    if not metering_enabled():
        return None

    with _metering_lock:
        if _metering_sink_subscribed:
            return _metering_stores.get(user_id)
        from src.sdk.audit import default_capture_bus

        def _sink(event: Any) -> None:
            if getattr(event, "kind", None) != "usage":
                return
            user = event.user_id or "default_user"
            store = get_metering_store(user)
            store.record(
                UsageEventRow(
                    event_id=event.event_id,
                    ts=event.ts,
                    user_id=user,
                    session_id=event.session_id,
                    run_id=event.call_id,
                    model_id=event.model_id,
                    input_tokens=event.usage_input_tokens or 0,
                    output_tokens=event.usage_output_tokens or 0,
                    reasoning_tokens=event.usage_reasoning_tokens or 0,
                    cost_usd=event.usage_cost_usd or 0.0,
                    tool_calls=event.tool_calls or 0,
                )
            )

        default_capture_bus.subscribe(_sink)
        _metering_sink_subscribed = True
        return get_metering_store(user_id)


class MeteringStore:
    """Per-user SQLite metering store. Write-only record; read via aggregates."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                event_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                run_id TEXT,
                model_id TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                tool_calls INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events (ts)"
        )
        self._conn.commit()

    def record(self, row: UsageEventRow) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO usage_events
                    (event_id, ts, user_id, session_id, run_id, model_id,
                     input_tokens, output_tokens, reasoning_tokens,
                     cost_usd, tool_calls)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.event_id,
                    row.ts.isoformat(),
                    row.user_id,
                    row.session_id,
                    row.run_id,
                    row.model_id,
                    row.input_tokens,
                    row.output_tokens,
                    row.reasoning_tokens,
                    row.cost_usd,
                    row.tool_calls,
                ),
            )

    def _cutoff(self, window_days: int) -> str:
        return (datetime.now(UTC) - timedelta(days=window_days)).isoformat()

    def summary(self, window_days: int = 30) -> list[UsageSummaryRow]:
        """Tokens + cost per (day, model_id), newest day first."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT substr(ts, 1, 10) AS day, model_id,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(reasoning_tokens) AS reasoning_tokens,
                       SUM(cost_usd) AS cost_usd,
                       COUNT(*) AS llm_calls
                FROM usage_events
                WHERE ts >= ?
                GROUP BY day, model_id
                ORDER BY day DESC, model_id
                """,
                (self._cutoff(window_days),),
            ).fetchall()
        return [
            UsageSummaryRow(
                day=day,
                model_id=model_id,
                input_tokens=int(in_t),
                output_tokens=int(out_t),
                reasoning_tokens=int(r_t),
                cost_usd=float(c_usd),
                llm_calls=int(calls),
            )
            for day, model_id, in_t, out_t, r_t, c_usd, calls in rows
        ]

    def events(
        self, limit: int = 50, offset: int = 0, window_days: int = 365
    ) -> list[UsageEventRow]:
        """Paginated usage events, newest first."""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock, self._conn:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                """
                SELECT * FROM usage_events
                WHERE ts >= ?
                ORDER BY ts DESC
                LIMIT ? OFFSET ?
                """,
                (self._cutoff(window_days), limit, offset),
            ).fetchall()
            self._conn.row_factory = None
        return [UsageEventRow(**dict(r)) for r in rows]

    def total_cost(self, window_days: int = 30) -> float:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM usage_events WHERE ts >= ?",
                (self._cutoff(window_days),),
            ).fetchone()
        return float(row[0]) if row else 0.0
