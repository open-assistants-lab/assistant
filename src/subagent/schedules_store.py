"""Durable schedule definitions for governed subagent scheduling (issue #46).

Schedule *definitions* live here; execution *results* stay in the existing
work queue, receipts, and completion outbox. Keeping them apart means a
restored trigger never has to reconstruct its own history.

The store is the only writer of ``data/subagent_schedules.db``. Every read and
write is scoped by ``user_id``: a schedule ID alone grants nothing.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from src.app_logging import get_logger
from src.storage.paths import get_paths

logger = get_logger()

#: Statuses a schedule may hold. The first six come from the #46 design spec;
#: ``expired``/``invalid`` record restore-time outcomes and ``needs_review``
#: records a legacy row whose trigger type could not be determined.
SCHEDULE_STATUSES: frozenset[str] = frozenset(
    {
        "scheduled",
        "running",
        "completed",
        "failed",
        "cancelled",
        "rejected",
        "expired",
        "invalid",
        "needs_review",
    }
)

#: Statuses from which a schedule will never fire again.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "rejected", "expired", "invalid"}
)

TRIGGER_KINDS: frozenset[str] = frozenset({"once", "cron"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subagent_schedules (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    task TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    run_at TEXT,
    cron TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'scheduled',
    manifest_json TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_id TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sched_user_status
    ON subagent_schedules(user_id, status);
CREATE INDEX IF NOT EXISTS idx_sched_status
    ON subagent_schedules(status);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _schedule_id() -> str:
    return f"sched_{uuid.uuid4().hex[:16]}"


def _require_text(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required and must be a non-empty string")
    return value.strip()


def _validate_trigger(
    trigger_kind: str,
    run_at: str | None,
    cron: str | None,
    timezone: str,
) -> None:
    if trigger_kind not in TRIGGER_KINDS:
        raise ValueError(
            f"trigger_kind must be one of {sorted(TRIGGER_KINDS)}, got {trigger_kind!r}"
        )
    if trigger_kind == "once" and not run_at:
        raise ValueError("a 'once' schedule requires run_at")
    if trigger_kind == "cron" and not cron:
        raise ValueError("a 'cron' schedule requires cron")
    if not (isinstance(timezone, str) and timezone.strip()):
        raise ValueError("timezone is required and must be a non-empty string")


def _row_to_dict(row: Any) -> dict[str, Any]:
    record = dict(row)
    # Expose the design spec's `schedule_id` instead of leaking the SQL column.
    if "id" in record:
        record["schedule_id"] = record.pop("id")
    raw = record.pop("manifest_json", "{}")
    try:
        manifest = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.error("subagent.schedule_manifest_unreadable", {}, user_id="system")
        manifest = {}
    record["manifest"] = manifest if isinstance(manifest, dict) else {}
    return record


class SubagentScheduleStore:
    """Async SQLite store for durable subagent schedule definitions."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._db_path = Path(path) if path is not None else get_paths().subagent_schedules_db_path()
        self._db: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            async with self._init_lock:
                if self._db is None:
                    self._db = await self._connect()
        return self._db

    async def _connect(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return await self._open()
        except sqlite3.DatabaseError as exc:
            # Preserve the unreadable file for forensics instead of deleting a
            # user's schedules outright, then rebuild from the schema.
            quarantine = self._db_path.with_suffix(f"{self._db_path.suffix}.corrupt")
            logger.error(
                "subagent.schedule_db_corrupt",
                {"error": str(exc), "quarantined_to": str(quarantine)},
                user_id="system",
            )
            self._db_path.replace(quarantine)
            return await self._open()

    async def _open(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_SCHEMA)
            await db.commit()
        except sqlite3.DatabaseError:
            await db.close()
            raise
        return db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # -- writes ------------------------------------------------------------

    async def create(
        self,
        *,
        user_id: str,
        workspace_id: str,
        subagent_name: str,
        task: str,
        trigger_kind: str,
        run_at: datetime | str | None,
        cron: str | None,
        timezone: str,
        manifest: Mapping[str, Any],
        manifest_hash: str,
    ) -> dict[str, Any]:
        """Persist a new schedule. Validation happens before any write."""
        user_id = _require_text("user_id", user_id)
        workspace_id = _require_text("workspace_id", workspace_id)
        subagent_name = _require_text("subagent_name", subagent_name)
        task = _require_text("task", task)
        manifest_hash = _require_text("manifest_hash", manifest_hash)
        run_at_text = _as_timestamp(run_at)
        cron_text = cron.strip() if isinstance(cron, str) and cron.strip() else None
        _validate_trigger(trigger_kind, run_at_text, cron_text, timezone)

        schedule_id = _schedule_id()
        now = _now()
        db = await self._get_db()
        await db.execute(
            """INSERT INTO subagent_schedules
            (id, user_id, workspace_id, subagent_name, task, trigger_kind, run_at,
             cron, timezone, status, manifest_json, manifest_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)""",
            (
                schedule_id,
                user_id,
                workspace_id,
                subagent_name,
                task,
                trigger_kind,
                run_at_text,
                cron_text,
                timezone.strip(),
                json.dumps(dict(manifest), sort_keys=True),
                manifest_hash,
                now,
                now,
            ),
        )
        await db.commit()
        created = await self.get(user_id, schedule_id)
        if created is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError(f"schedule {schedule_id} vanished after insert")
        return created

    async def update_status(
        self,
        user_id: str,
        schedule_id: str,
        status: str,
        *,
        last_run_id: str | None = None,
        last_error: str | None = None,
    ) -> bool:
        """Move a schedule to a new status, preserving unset fields."""
        if status not in SCHEDULE_STATUSES:
            raise ValueError(f"unknown schedule status {status!r}")
        db = await self._get_db()
        cursor = await db.execute(
            """UPDATE subagent_schedules
            SET status = ?, updated_at = ?,
                last_run_id = COALESCE(?, last_run_id),
                last_error = COALESCE(?, last_error)
            WHERE id = ? AND user_id = ?""",
            (status, _now(), last_run_id, last_error, schedule_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def cancel(self, user_id: str, schedule_id: str) -> bool:
        """Cancel a schedule. Idempotent; never rewrites a terminal outcome."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT status FROM subagent_schedules WHERE id = ? AND user_id = ?",
            (schedule_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        if str(row["status"]) in TERMINAL_STATUSES:
            return True
        await db.execute(
            "UPDATE subagent_schedules SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (_now(), schedule_id, user_id),
        )
        await db.commit()
        return True

    # -- reads -------------------------------------------------------------

    async def get(self, user_id: str, schedule_id: str) -> dict[str, Any] | None:
        """Fetch one schedule owned by ``user_id``; ``None`` for anyone else."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM subagent_schedules WHERE id = ? AND user_id = ?",
            (schedule_id, user_id),
        )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List a user's schedules, newest first, optionally filtered/paginated."""
        db = await self._get_db()
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status is not None:
            if status not in SCHEDULE_STATUSES:
                raise ValueError(f"unknown schedule status {status!r}")
            clauses.append("status = ?")
            params.append(status)
        sql = (
            "SELECT * FROM subagent_schedules WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id DESC"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(0, int(limit)), max(0, int(offset))])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(max(0, int(offset)))

        cursor = await db.execute(sql, params)
        return [_row_to_dict(row) for row in await cursor.fetchall()]

    async def due_for_restore(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """Return schedules awaiting trigger reconstruction at startup.

        ``user_id=None`` reconciles every user's rows, which is what startup
        needs; per-user calls keep callers from reaching across users.
        """
        db = await self._get_db()
        if user_id is None:
            cursor = await db.execute(
                "SELECT * FROM subagent_schedules WHERE status = 'scheduled' "
                "ORDER BY created_at ASC, id ASC"
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM subagent_schedules WHERE status = 'scheduled' "
                "AND user_id = ? ORDER BY created_at ASC, id ASC",
                (user_id,),
            )
        return [_row_to_dict(row) for row in await cursor.fetchall()]


def _as_timestamp(value: datetime | str | None) -> str | None:
    """Normalize a trigger time to an explicit-offset ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("run_at must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
