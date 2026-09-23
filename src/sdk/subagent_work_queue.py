"""Work queue database — SQLite-backed task coordination for subagents.

Uses aiosqlite for async access (matches design contract in SUBAGENT_RESEARCH.md).
Per-user database at data/private/subagents/work_queue.db.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
from agentprofile.models import AgentProfile

from src.app_logging import get_logger
from src.sdk.subagent_models import SubagentResult, TaskStatus
from src.storage.paths import get_paths

logger = get_logger()

USER_LEVEL_WORKSPACE_ID = "user"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_queue (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    parent_session_id TEXT,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'personal',
    agent_name TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress TEXT DEFAULT '{}',
    result TEXT,
    error TEXT,
    instructions TEXT DEFAULT '[]',
    config TEXT DEFAULT '{}',
    launch_plan TEXT NOT NULL DEFAULT '{}',
    terminal_reason TEXT,
    cancel_requested INTEGER DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wq_user_status ON work_queue(user_id, status);
CREATE INDEX IF NOT EXISTS idx_wq_parent ON work_queue(parent_id);
CREATE INDEX IF NOT EXISTS idx_wq_workspace ON work_queue(workspace_id, status);

CREATE TABLE IF NOT EXISTS completion_events (
    task_id TEXT PRIMARY KEY,
    parent_session_id TEXT,
    agent_name TEXT,
    workspace_id TEXT,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _task_id() -> str:
    return uuid.uuid4().hex[:16]


class SubagentWorkQueueDB:
    """Async SQLite work queue for subagent coordination."""

    def __init__(self, user_id: str, workspace_id: str = "personal"):
        self.user_id = user_id
        self.requested_workspace_id = workspace_id
        self.workspace_id = USER_LEVEL_WORKSPACE_ID
        self._db: aiosqlite.Connection | None = None
        self._db_path = str(get_paths(user_id).work_queue_db())
        self._init_lock = asyncio.Lock()

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            async with self._init_lock:
                if self._db is None:
                    self._db = await aiosqlite.connect(self._db_path)
                    self._db.row_factory = aiosqlite.Row
                    await self._db.execute("PRAGMA journal_mode=WAL")
                    await self._db.executescript(_SCHEMA)
                    await self._ensure_columns()
                    await self._db.commit()
        return self._db

    async def _ensure_columns(self) -> None:
        db = self._db
        if db is None:
            return
        cursor = await db.execute("PRAGMA table_info(work_queue)")
        rows = await cursor.fetchall()
        existing = {row["name"] for row in rows}
        columns = {
            "claimed_by": "TEXT",
            "claimed_at": "TEXT",
            "heartbeat_at": "TEXT",
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "parent_session_id": "TEXT",
            "launch_plan": "TEXT NOT NULL DEFAULT '{}'",
            "terminal_reason": "TEXT",
        }
        for name, ddl in columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE work_queue ADD COLUMN {name} {ddl}")
        cursor = await db.execute("PRAGMA table_info(completion_events)")
        event_columns = {row["name"] for row in await cursor.fetchall()}
        for name, ddl in {
            "parent_session_id": "TEXT",
            "agent_name": "TEXT",
            "workspace_id": "TEXT",
        }.items():
            if name not in event_columns:
                await db.execute(f"ALTER TABLE completion_events ADD COLUMN {name} {ddl}")
        await db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def insert_task(
        self,
        agent_name: str,
        task: str,
        config: AgentProfile,
        parent_id: str | None = None,
        parent_session_id: str | None = None,
        launch_plan: dict[str, Any] | None = None,
    ) -> str:
        db = await self._get_db()
        task_id = _task_id()
        now = _now()
        await db.execute(
            """INSERT INTO work_queue
            (id, parent_id, parent_session_id, user_id, workspace_id, agent_name, task, status, progress, config, launch_plan, instructions, cancel_requested, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) """,
            (
                task_id,
                parent_id,
                parent_session_id,
                self.user_id,
                self.workspace_id,
                agent_name,
                task,
                TaskStatus.PENDING.value,
                "{}",
                config.model_dump_json(),
                json.dumps(launch_plan or {}, sort_keys=True),
                "[]",
                0,
                now,
                now,
            ),
        )
        await db.commit()
        return task_id

    async def set_status(self, task_id: str, status: TaskStatus) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            "UPDATE work_queue SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (status.value, now, task_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def set_running(self, task_id: str) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, started_at = COALESCE(started_at, ?),
                heartbeat_at = COALESCE(heartbeat_at, ?), updated_at = ?
            WHERE id = ? AND user_id = ?""",
            (TaskStatus.RUNNING.value, now, now, now, task_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def claim_task(self, task_id: str, worker_id: str) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, claimed_by = ?, claimed_at = ?, heartbeat_at = ?,
                started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ? AND user_id = ? AND status = ?""",
            (
                TaskStatus.RUNNING.value,
                worker_id,
                now,
                now,
                now,
                now,
                task_id,
                self.user_id,
                TaskStatus.PENDING.value,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def heartbeat(self, task_id: str, worker_id: str) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            """UPDATE work_queue
            SET heartbeat_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND claimed_by = ? AND status IN (?, ?)""",
            (
                now,
                now,
                task_id,
                self.user_id,
                worker_id,
                TaskStatus.RUNNING.value,
                TaskStatus.CANCELLING.value,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def set_completed(self, task_id: str, result: SubagentResult) -> bool:
        db = await self._get_db()
        now = _now()
        row = await self.get_task(task_id)
        if row and row.get("launch_plan"):
            plan = json.loads(row["launch_plan"] or "{}")
            result = result.model_copy(update={"launch_plan_id": plan.get("plan_id")})
        cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, result = ?, terminal_reason = ?, completed_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND status IN (?, ?) AND cancel_requested = 0""",
            (
                TaskStatus.COMPLETED.value,
                result.model_dump_json(),
                result.terminal_reason,
                now,
                now,
                task_id,
                self.user_id,
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount > 0:
            await db.execute(
                """INSERT INTO completion_events
                (task_id, parent_session_id, agent_name, workspace_id, status, result, error, created_at)
                SELECT id, parent_session_id, agent_name, workspace_id, ?, ?, ?, ?
                FROM work_queue WHERE id = ? AND user_id = ?
                ON CONFLICT(task_id) DO NOTHING""",
                (
                    TaskStatus.COMPLETED.value,
                    result.model_dump_json(),
                    None,
                    now,
                    task_id,
                    self.user_id,
                ),
            )
        await db.commit()
        return cursor.rowcount > 0

    async def list_undelivered_completion_events(self) -> list[dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT task_id, parent_session_id, agent_name, workspace_id,
            status, result, error, attempts, created_at
            FROM completion_events WHERE delivered_at IS NULL ORDER BY created_at"""
        )
        rows = await cursor.fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["result"] = json.loads(event["result"]) if event["result"] else None
            events.append(event)
        return events

    async def mark_completion_event_delivered(self, task_id: str) -> bool:
        """Acknowledge an outbox event only after its consumer succeeds."""
        db = await self._get_db()
        cursor = await db.execute(
            """UPDATE completion_events
            SET delivered_at = ?, attempts = attempts + 1
            WHERE task_id = ? AND delivered_at IS NULL""",
            (_now(), task_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def record_completion_delivery_attempt(self, task_id: str) -> bool:
        """Record a failed delivery attempt without suppressing replay."""
        db = await self._get_db()
        cursor = await db.execute(
            """UPDATE completion_events SET attempts = attempts + 1
            WHERE task_id = ? AND delivered_at IS NULL""",
            (task_id,),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def _insert_completion_event(
        self,
        task_id: str,
        status: TaskStatus,
        result: SubagentResult,
        error: str | None,
        created_at: str,
    ) -> None:
        db = await self._get_db()
        task_row = await self.get_task(task_id)
        if task_row and task_row.get("launch_plan"):
            plan = json.loads(task_row["launch_plan"] or "{}")
            result = result.model_copy(update={"launch_plan_id": plan.get("plan_id")})
        await db.execute(
            """INSERT INTO completion_events
            (task_id, parent_session_id, agent_name, workspace_id, status, result, error, created_at)
            SELECT id, parent_session_id, agent_name, workspace_id, ?, ?, ?, ?
            FROM work_queue WHERE id = ? AND user_id = ?
            ON CONFLICT(task_id) DO NOTHING""",
            (status.value, result.model_dump_json(), error, created_at, task_id, self.user_id),
        )

    async def set_failed(
        self,
        task_id: str,
        error: str,
        terminal_status: TaskStatus = TaskStatus.FAILED,
    ) -> bool:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            raise ValueError("terminal_status must be failed or timed_out")
        db = await self._get_db()
        now = _now()
        result = SubagentResult(
            name="",
            task="",
            success=False,
            output="",
            error=error,
            terminal_reason=terminal_status.value,
        )
        cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, result = ?, error = ?, terminal_reason = ?, completed_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND status IN (?, ?) AND cancel_requested = 0""",
            (
                terminal_status.value,
                result.model_dump_json(),
                error,
                result.terminal_reason,
                now,
                now,
                task_id,
                self.user_id,
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount > 0:
            await self._insert_completion_event(
                task_id, terminal_status, result, error, now
            )
        await db.commit()
        return cursor.rowcount > 0

    async def set_cancelled(self, task_id: str) -> bool:
        db = await self._get_db()
        now = _now()
        result = SubagentResult(
            name="",
            task="",
            success=False,
            output="",
            error="cancelled by supervisor",
            terminal_reason="cancelled",
        )
        cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, result = ?, terminal_reason = ?, cancel_requested = 1,
                completed_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ?""",
            (
                TaskStatus.CANCELLED.value,
                result.model_dump_json(),
                result.terminal_reason,
                now,
                now,
                task_id,
                self.user_id,
            ),
        )
        if cursor.rowcount > 0:
            await self._insert_completion_event(
                task_id,
                TaskStatus.CANCELLED,
                result,
                "cancelled by supervisor",
                now,
            )
        await db.commit()
        return cursor.rowcount > 0

    async def mark_stale_running_failed(self, max_age_seconds: int = 300) -> int:
        db = await self._get_db()
        now = _now()
        cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_seconds)).isoformat()
        error = "subagent task interrupted by restart; last heartbeat is stale"
        result = SubagentResult(name="", task="", success=False, output="", error=error)
        cursor = await db.execute(
            """SELECT id FROM work_queue
            WHERE user_id = ? AND status IN (?, ?)
            AND (heartbeat_at IS NULL OR heartbeat_at < ?)""",
            (
                self.user_id,
                TaskStatus.RUNNING.value,
                TaskStatus.CANCELLING.value,
                cutoff,
            ),
        )
        task_ids = [row["id"] for row in await cursor.fetchall()]
        for task_id in task_ids:
            changed = await db.execute(
                """UPDATE work_queue
                SET status = ?, result = ?, error = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND status IN (?, ?)""",
                (
                    TaskStatus.FAILED.value,
                    result.model_dump_json(),
                    error,
                    now,
                    now,
                    task_id,
                    self.user_id,
                    TaskStatus.RUNNING.value,
                    TaskStatus.CANCELLING.value,
                ),
            )
            if changed.rowcount:
                await self._insert_completion_event(
                    task_id, TaskStatus.FAILED, result, error, now
                )
        await db.commit()
        return len(task_ids)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM work_queue WHERE id = ? AND user_id = ?",
            (task_id, self.user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def update_progress(self, task_id: str, progress: dict[str, Any]) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            "UPDATE work_queue SET progress = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (json.dumps(progress, ensure_ascii=True), now, task_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def add_instruction(self, task_id: str, message: str) -> bool:
        db = await self._get_db()
        now = _now()
        row = await self.get_task(task_id)
        if row is None:
            return False
        instructions = json.loads(row.get("instructions") or "[]")
        instructions.append({"added_at": now, "message": message})
        cursor = await db.execute(
            "UPDATE work_queue SET instructions = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (json.dumps(instructions, ensure_ascii=True), now, task_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def request_cancel(self, task_id: str) -> bool:
        db = await self._get_db()
        now = _now()
        cursor = await db.execute(
            """UPDATE work_queue
            SET cancel_requested = 1,
                status = CASE WHEN status = ? THEN ? ELSE status END,
                updated_at = ?
            WHERE id = ? AND user_id = ?""",
            (TaskStatus.RUNNING.value, TaskStatus.CANCELLING.value, now, task_id, self.user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def request_cancel_active_tasks_for_agent(self, agent_name: str) -> int:
        db = await self._get_db()
        now = _now()
        error = "cancelled before start"
        result = SubagentResult(name=agent_name, task="", success=False, output="", error=error)
        pending_cursor = await db.execute(
            """UPDATE work_queue
            SET status = ?, result = ?, error = ?, cancel_requested = 1,
                completed_at = ?, updated_at = ?
            WHERE user_id = ? AND agent_name = ? AND status = ?""",
            (
                TaskStatus.CANCELLED.value,
                result.model_dump_json(),
                error,
                now,
                now,
                self.user_id,
                agent_name,
                TaskStatus.PENDING.value,
            ),
        )
        active_cursor = await db.execute(
            """UPDATE work_queue
            SET cancel_requested = 1, status = ?, updated_at = ?
            WHERE user_id = ? AND agent_name = ? AND status IN (?, ?)""",
            (
                TaskStatus.CANCELLING.value,
                now,
                self.user_id,
                agent_name,
                TaskStatus.RUNNING.value,
                TaskStatus.CANCELLING.value,
            ),
        )
        await db.commit()
        return pending_cursor.rowcount + active_cursor.rowcount

    async def is_cancel_requested(self, task_id: str) -> bool:
        row = await self.get_task(task_id)
        if row is None:
            return False
        return bool(row.get("cancel_requested"))

    async def check_progress(
        self,
        parent_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[dict[str, Any]]:
        db = await self._get_db()
        if parent_id is not None and status is not None:
            cursor = await db.execute(
                """SELECT id, agent_name, task, status, progress, error, created_at, updated_at
                FROM work_queue
                WHERE user_id = ? AND parent_id = ? AND status = ?
                ORDER BY created_at""",
                (self.user_id, parent_id, status.value),
            )
        elif parent_id is not None:
            cursor = await db.execute(
                """SELECT id, agent_name, task, status, progress, error, created_at, updated_at
                FROM work_queue
                WHERE user_id = ? AND parent_id = ?
                ORDER BY created_at""",
                (self.user_id, parent_id),
            )
        elif status is not None:
            cursor = await db.execute(
                """SELECT id, agent_name, task, status, progress, error, created_at, updated_at
                FROM work_queue
                WHERE user_id = ? AND status = ?
                ORDER BY created_at""",
                (self.user_id, status.value),
            )
        else:
            cursor = await db.execute(
                """SELECT id, agent_name, task, status, progress, error, created_at, updated_at
                FROM work_queue
                WHERE user_id = ?
                ORDER BY created_at""",
                (self.user_id,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        return await self.check_progress(status=TaskStatus.RUNNING)

    async def get_result(self, task_id: str) -> SubagentResult | None:
        row = await self.get_task(task_id)
        if row is None:
            return None
        result_json = row.get("result")
        if not result_json:
            return None
        data = json.loads(result_json)
        return SubagentResult(**data)


_db_cache: dict[str, SubagentWorkQueueDB] = {}


async def get_work_queue(user_id: str, workspace_id: str = "personal") -> SubagentWorkQueueDB:
    key = user_id
    if key not in _db_cache:
        _db_cache[key] = SubagentWorkQueueDB(user_id, workspace_id)
    return _db_cache[key]
