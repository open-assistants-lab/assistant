"""Telemetry sidecar aggregate store (Phase 2 D1-1).

Periodic flush of metering/usage aggregates into an analytics store backing
the owner dashboard. Two backends:

- **duckdb mirror** when the `analytics` extra is installed (HybridDB's
  duckdb attach pattern — see the messages DuckDB mirror in
  `src/storage/messages.py`): the sqlite aggregate table is mirrored into a
  duckdb file for columnar queries.
- **Plain sqlite fallback** when duckdb is not installed (the extra is
  optional — never hard-fail on it).

Single-writer, append/upsert by (user_id, day) — same patterns as the
metering store. The heavy lifting for the dashboard lives here; the sdk-side
telemetry module stays a thin flush/opt-out shim.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from src.app_logging import get_logger

logger = get_logger()

_analytics_stores: dict[str, AnalyticsStore] = {}

# Documented derivation assumption (D1-1): one automated LLM call replaces a
# 5-minute manual step. hours_saved = llm_calls * 5 / 60 unless real turn
# durations are flushed (avg_turn_seconds > 0 wins when present).
MANUAL_MINUTES_PER_CALL = 5.0


class AnalyticsStore:
    """Sidecar aggregate store: sqlite source of truth, optional duckdb mirror.

    One row per (user_id, day): drafts count, llm calls, tokens, cost,
    tool calls, average turn seconds. Written by telemetry.flush; read by the
    dashboard router. Write-only flush + read-only summary — no update/delete
    surface beyond the daily upsert.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_days (
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,
                drafts_count INTEGER NOT NULL DEFAULT 0,
                llm_calls INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                tool_calls INTEGER NOT NULL DEFAULT 0,
                avg_turn_seconds REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
            """
        )
        self._conn.commit()
        self._mirror_duckdb()

    def _mirror_duckdb(self) -> None:
        """Idempotent duckdb mirror when the analytics extra is installed.

        Plain sqlite is the source of truth; the duckdb file is a mirror for
        heavier analytics. Never hard-fails on the optional extra.
        """
        try:
            import duckdb
        except ImportError:
            return
        try:
            duck_path = str(Path(self._db_path).with_suffix(".duckdb"))
            con = duckdb.connect(duck_path)
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_days AS
                SELECT * FROM sqlite_scan(?) WHERE 0
                """,
                [self._db_path],
            )
            con.close()
        except Exception:
            # duckdb missing or extension unavailable — sqlite is the store.
            return

    def record_day(
        self,
        user_id: str,
        day: str,
        drafts_count: int,
        llm_calls: int,
        cost_usd: float,
        tool_calls: int,
        avg_turn_seconds: float,
    ) -> None:
        """Upsert the aggregate row for (user_id, day)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO dashboard_days
                    (user_id, day, drafts_count, llm_calls, cost_usd,
                     tool_calls, avg_turn_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, day) DO UPDATE SET
                    drafts_count=excluded.drafts_count,
                    llm_calls=excluded.llm_calls,
                    cost_usd=excluded.cost_usd,
                    tool_calls=excluded.tool_calls,
                    avg_turn_seconds=excluded.avg_turn_seconds
                """,
                (user_id, day, drafts_count, llm_calls, cost_usd, tool_calls, avg_turn_seconds),
            )

    def summary(self, user_id: str, window_days: int = 30) -> dict[str, object]:
        """Aggregate over the window for one user."""
        with self._lock, self._conn:
            self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(drafts_count), 0) AS drafts,
                       COALESCE(SUM(llm_calls), 0) AS llm_calls,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(AVG(NULLIF(avg_turn_seconds, 0)), 0) AS avg_turn_seconds
                FROM dashboard_days
                WHERE user_id = ?
                  AND day >= ?
                """,
                (user_id, _cutoff(window_days)),
            ).fetchone()
        return {
            "drafts": int(row["drafts"]) if row else 0,
            "llm_calls": int(row["llm_calls"]) if row else 0,
            "avg_turn_seconds": float(row["avg_turn_seconds"]) if row else 0.0,
        }

def _cutoff(window_days: int) -> str:
    """ISO date cutoff for the window (inclusive lower bound)."""
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(days=window_days)).strftime("%Y-%m-%d")


def get_analytics_store(user_id: str) -> AnalyticsStore:
    """Per-user analytics store singleton (per-process cache)."""
    from src.storage.paths import get_paths

    store = _analytics_stores.get(user_id)
    if store is None:
        paths = get_paths(user_id=user_id)
        private_dir = Path(paths.user_dir) / "private"
        private_dir.mkdir(parents=True, exist_ok=True)
        store = AnalyticsStore(private_dir / "analytics.db")
        _analytics_stores[user_id] = store
    return store


def reset_analytics_stores() -> None:
    """Test helper: drop cached stores (they re-create on next access)."""
    _analytics_stores.clear()


def flush_user(user_id: str, turn_seconds: float | None = None) -> dict[str, object]:
    """Recompute today's aggregate row for the user and upsert it.

    Sources: MeteringStore.snapshot (tokens/cost/llm_calls/tool_calls —
    M1.3) + the review-queue drafts count (skill drafts). Returns the
    flushed summary for callers that want it.
    """
    from src.skills.registry import SkillRegistry
    from src.storage.metering import get_metering_store

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    snapshot = get_metering_store(user_id).snapshot(window_days=1)

    try:
        drafts = len(SkillRegistry(user_id=user_id).list_skill_drafts())
    except Exception:
        drafts = 0
    avg_turn_seconds = (
        float(turn_seconds) / max(1, snapshot.llm_calls)
        if turn_seconds is not None and turn_seconds > 0
        else 0.0
    )
    store = get_analytics_store(user_id)
    store.record_day(
        user_id=user_id,
        day=day,
        drafts_count=drafts,
        llm_calls=snapshot.llm_calls,
        cost_usd=snapshot.cost_usd,
        tool_calls=snapshot.tool_calls,
        avg_turn_seconds=avg_turn_seconds,
    )
    return {
        "user_id": user_id,
        "day": day,
        "drafts": drafts,
        "llm_calls": snapshot.llm_calls,
        "cost_usd": snapshot.cost_usd,
    }
