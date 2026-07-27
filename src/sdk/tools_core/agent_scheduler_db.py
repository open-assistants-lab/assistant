"""Scheduler database — notification store + personality memory for agent scheduler.

Uses aiosqlite for async access. Per-user database at data/users/{user_id}/companion/.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.app_logging import get_logger
from src.storage.paths import get_paths

logger = get_logger()

_NOTIF_SCHEMA = """
CREATE TABLE IF NOT EXISTS companion_notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    workspace_id TEXT,
    dismissed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comp_notif_user
    ON companion_notifications(user_id, dismissed, created_at);
CREATE INDEX IF NOT EXISTS idx_comp_notif_ws
    ON companion_notifications(workspace_id, dismissed);
"""

_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS companion_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'inferred',
    confidence REAL DEFAULT 0.5,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_comp_mem_user ON companion_memory(user_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _notif_id() -> str:
    return uuid.uuid4().hex[:16]


class SchedulerNotificationDB:
    """Async SQLite notification store for companion nudges."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._db: aiosqlite.Connection | None = None
        self._db_path = str(get_paths(user_id).companion_notifications_db())

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.executescript(_NOTIF_SCHEMA)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def insert(
        self,
        message: str,
        category: str = "general",
        workspace_id: str | None = None,
    ) -> str:
        notif_id = _notif_id()
        db = await self._get_db()
        await db.execute(
            "INSERT INTO companion_notifications (id, user_id, message, category, workspace_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (notif_id, self.user_id, message, category, workspace_id, _now()),
        )
        await db.commit()
        logger.info(
            "companion.notification_inserted",
            {"id": notif_id, "category": category, "workspace_id": workspace_id or "none"},
            user_id=self.user_id,
        )
        return notif_id

    async def list(
        self,
        limit: int = 50,
        include_dismissed: bool = False,
    ) -> list[dict[str, Any]]:
        db = await self._get_db()
        query = (
            "SELECT * FROM companion_notifications WHERE user_id = ?"
        )
        params: list[Any] = [self.user_id]
        if not include_dismissed:
            query += " AND dismissed = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def dismiss(self, notif_id: str) -> bool:
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE companion_notifications SET dismissed = 1 WHERE id = ? AND user_id = ?",
            (notif_id, self.user_id),
        )
        await db.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info("companion.notification_dismissed", {"id": notif_id}, user_id=self.user_id)
        return updated

    async def recent_messages(self, count: int = 3) -> str:
        recent = await self.list(limit=count)
        if not recent:
            return "none"
        msgs = [r["message"] for r in recent]
        return " | ".join(str(m) for m in msgs)

    async def last_check_time(self) -> str:
        recent = await self.list(limit=1)
        if not recent:
            return "never"
        return str(recent[0]["created_at"])

    async def dismissal_streak(self) -> int:
        """Count consecutive dismissed notifications to detect fatigue."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT dismissed FROM companion_notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
            (self.user_id,),
        )
        rows = await cursor.fetchall()
        streak = 0
        for row in rows:
            if row["dismissed"]:
                streak += 1
            else:
                break
        return streak


class SchedulerMemoryDB:
    """Async SQLite store for companion personality facts."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._db: aiosqlite.Connection | None = None
        self._db_path = str(get_paths(user_id).companion_memory_db())

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.executescript(_MEMORY_SCHEMA)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def get_all(self) -> dict[str, str]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT key, value FROM companion_memory WHERE user_id = ? AND confidence > 0.1",
            (self.user_id,),
        )
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}

    async def list_all(self) -> list[dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM companion_memory WHERE user_id = ? AND confidence > 0.1 ORDER BY confidence DESC",
            (self.user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete(self, mem_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM companion_memory WHERE id = ? AND user_id = ?",
            (mem_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def _apply_decay(self) -> None:
        """Reduce confidence by 0.01 for all facts older than 1 day."""
        db = await self._get_db()
        await db.execute(
            "UPDATE companion_memory SET confidence = MAX(0.0, confidence - 0.01) "
            "WHERE user_id = ? AND updated_at < ?",
            (self.user_id, _now()),
        )
        await db.commit()
