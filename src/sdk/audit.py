"""Audit event capture layer (roadmap P0-T3).

ONE capture bus at the loop/tool boundary; sinks subscribe. The audit store
subscribes today; the Phase-2 metering sidecar subscribes to the SAME bus
(audit + metering read one stream). This bus is also the substrate of the
event-sourced session log (R-SL1): P0-T9 defines the SessionEvent schema the
bus emits.

Contract: emission is emit-only — sinks can never alter control flow.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.storage.paths import DataPaths

AuditKind = Literal[
    "tool_call", "tool_result", "approve", "interrupt", "error", "usage"
]

AuditSink = Callable[["AuditEvent"], None]


class AuditEvent(BaseModel):
    """One auditable action at the loop/tool boundary."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str | None = None
    session_id: str | None = None
    kind: AuditKind
    tool: str | None = None
    call_id: str | None = None
    approved: bool | None = None
    detail: str | None = None
    # Optional usage payload (Phase 2 M1.1 — "usage"-kind events): all
    # default None so existing audit export shapes are byte-identical.
    model_id: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    usage_reasoning_tokens: int | None = None
    usage_cost_usd: float | None = None
    tool_calls: int | None = None


class CaptureBus:
    """Fan-out bus for audit events. Subscribers never break emission."""

    def __init__(self) -> None:
        self._sinks: list[AuditSink] = []

    def subscribe(self, sink: AuditSink) -> None:
        """Register a sink. Sinks receive every subsequently emitted event."""
        self._sinks.append(sink)

    def emit(self, event: AuditEvent) -> None:
        """Deliver an event to all sinks. Emit-only: sink errors are swallowed."""
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:
                # Emit-only by contract — a failing sink must never break the
                # loop/tool flow. (Logging is intentionally avoided here to
                # stay off the hot path; P0-T9/telemetry may surface drops.)
                pass


class AuditStore:
    """Append-only SQLite audit store.

    Write-only from the loop (via `record`); read-only via `export`. There is
    intentionally no update/delete/upsert surface.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # check_same_thread=False + a lock: safe across the loop thread AND
        # sync test/threadpool callers (export endpoint can run on a threadpool
        # worker under some ASGI servers). The lock serializes record/export.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                kind TEXT NOT NULL,
                tool TEXT,
                call_id TEXT,
                approved INTEGER,
                detail TEXT
            )
            """
        )
        self._conn.commit()

    def record(self, event: AuditEvent) -> None:
        """Append one event. Duplicate event_id is a no-op (append-only)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO audit_events
                    (event_id, ts, user_id, session_id, kind, tool, call_id, approved, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.ts.isoformat(),
                    event.user_id,
                    event.session_id,
                    event.kind,
                    event.tool,
                    event.call_id,
                    int(event.approved) if event.approved is not None else None,
                    event.detail,
                ),
            )
            self._conn.commit()

    def export(self, user_id: str, since: datetime | None = None) -> list[AuditEvent]:
        """Read-only export of one user's events, newest-last."""
        with self._lock:
            params: list[object] = [user_id]
            clause = "WHERE user_id = ?"
            if since is not None:
                clause += " AND ts >= ?"
                params.append(since.isoformat())
            rows = self._conn.execute(
                f"SELECT event_id, ts, user_id, session_id, kind, tool, call_id, approved, detail "
                f"FROM audit_events {clause} ORDER BY ts",
                params,
            ).fetchall()
        return [
            AuditEvent(
                event_id=r[0],
                ts=datetime.fromisoformat(r[1]),
                user_id=r[2],
                session_id=r[3],
                kind=r[4],
                tool=r[5],
                call_id=r[6],
                approved=bool(r[7]) if r[7] is not None else None,
                detail=r[8],
            )
            for r in rows
        ]


# One bus for the process: loop emits, WS approve sites emit, sinks subscribe.
default_capture_bus = CaptureBus()

# Production wiring (P0-T3 fix round): lazily-created per-user AuditStore,
# subscribed once to default_capture_bus. Per-user matches the codebase's
# single-writer per-user store pattern (work_queue.db etc.); events carry
# user_id so a shared store would also work, but per-user keeps export and
# future retention/purge per-user without cross-user scans.
_audit_stores: dict[str, AuditStore] = {}
_subscribe_lock = threading.Lock()


def ensure_audit_store_subscribed(user_id: str) -> AuditStore:
    """Return the per-user AuditStore, subscribing it to default_capture_bus once.

    Idempotent per user. Called from create_sdk_loop so every production loop
    (REST + SSE + WS paths) flows through it.
    """
    store = _audit_stores.get(user_id)
    if store is not None:
        return store

    with _subscribe_lock:
        store = _audit_stores.get(user_id)
        if store is not None:
            return store
        store = AuditStore(str(DataPaths(user_id=user_id).audit_db()))
        _audit_stores[user_id] = store
        default_capture_bus.subscribe(store.record)
        return store
