"""Durable operation ledger for asynchronous governed tools (issue #21 Phase 1).

The ledger deliberately uses the same per-user ``governance.db`` as proposals.
That lets proposal consumption and one operation creation happen in one SQLite
transaction; it is not a subagent queue or a HybridDB index.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class OperationStatus(StrEnum):
    """Durable governed-operation states."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


_TERMINAL = frozenset(
    {OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.CANCELLED, OperationStatus.UNCERTAIN}
)
_ALLOWED_TRANSITIONS = {
    OperationStatus.QUEUED: {OperationStatus.RUNNING, OperationStatus.CANCELLED, OperationStatus.UNCERTAIN},
    OperationStatus.RUNNING: set(_TERMINAL),
}


class GovernedOperation(BaseModel):
    """Authoritative durable record for one approved asynchronous operation."""

    operation_id: str
    proposal_id: str
    user_id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    status: OperationStatus
    cancel_requested: bool = False
    created_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail_safe: str | None = None


class OperationEvent(BaseModel):
    """Append-only, ordered operation checkpoint."""

    operation_id: str
    sequence: int
    timestamp: str
    kind: str
    message_safe: str
    structured_data_safe: dict[str, Any] | None = None


class GovernanceOperationStore:
    """State machine over the proposal-owning governance SQLite database.

    ``connection_factory`` must resolve the same database used by governance
    proposals. Callers hold no durable operation state in memory.
    """

    def __init__(
        self,
        connection_factory: Callable[[str], sqlite3.Connection],
        lock: threading.Lock,
    ) -> None:
        self._conn = connection_factory
        self._lock = lock

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                result_json TEXT,
                error_code TEXT,
                error_detail_safe TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_events (
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                kind TEXT NOT NULL,
                message_safe TEXT NOT NULL,
                structured_data_safe_json TEXT,
                PRIMARY KEY (operation_id, sequence),
                FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_user_status ON operations(user_id, status)")
        conn.commit()

    @classmethod
    def _operation_from_row(cls, row: sqlite3.Row | tuple[Any, ...] | None) -> GovernedOperation | None:
        if row is None:
            return None
        return GovernedOperation(
            operation_id=row[0],
            proposal_id=row[1],
            user_id=row[2],
            tool_name=row[3],
            arguments=json.loads(row[4]),
            arguments_hash=row[5],
            status=OperationStatus(row[6]),
            cancel_requested=bool(row[7]),
            created_at=row[8],
            started_at=row[9],
            updated_at=row[10],
            completed_at=row[11],
            result=json.loads(row[12]) if row[12] else None,
            error_code=row[13],
            error_detail_safe=row[14],
        )

    @staticmethod
    def _select_operation(conn: sqlite3.Connection, user_id: str, operation_id: str) -> tuple[Any, ...] | None:
        return conn.execute(
            """SELECT operation_id, proposal_id, user_id, tool_name, arguments_json,
                      arguments_hash, status, cancel_requested, created_at, started_at,
                      updated_at, completed_at, result_json, error_code, error_detail_safe
               FROM operations WHERE operation_id = ? AND user_id = ?""",
            (operation_id, user_id),
        ).fetchone()

    def approve_and_create_operation(self, user_id: str, proposal_id: str) -> GovernedOperation:
        """Consume one approved proposal and create/read its one queued operation.

        The proposal binding is unique. Duplicate callers return the original
        operation and never create or dispatch a second operation.
        """
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT operation_id FROM operations WHERE proposal_id = ? AND user_id = ?",
                (proposal_id, user_id),
            ).fetchone()
            if existing is not None:
                row = self._select_operation(conn, user_id, existing[0])
                conn.commit()
                operation = self._operation_from_row(row)
                assert operation is not None
                return operation
            proposal = conn.execute(
                "SELECT tool, arguments FROM proposals WHERE proposal_id = ? AND status = 'approved'",
                (proposal_id,),
            ).fetchone()
            if proposal is None:
                conn.rollback()
                raise ValueError("Proposal is not approved or does not exist")
            now = self._now()
            operation_id = uuid.uuid4().hex
            arguments = json.loads(proposal[1])
            consumed = conn.execute(
                "UPDATE proposals SET status = 'consumed' WHERE proposal_id = ? AND status = 'approved'",
                (proposal_id,),
            )
            if consumed.rowcount != 1:
                conn.rollback()
                raise ValueError("Proposal approval was consumed concurrently")
            conn.execute(
                """INSERT INTO operations (
                    operation_id, proposal_id, user_id, tool_name, arguments_json, arguments_hash,
                    status, cancel_requested, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    operation_id,
                    proposal_id,
                    user_id,
                    proposal[0],
                    json.dumps(arguments, sort_keys=True),
                    self._arguments_hash(arguments),
                    OperationStatus.QUEUED.value,
                    now,
                    now,
                ),
            )
            row = self._select_operation(conn, user_id, operation_id)
            conn.commit()
        operation = self._operation_from_row(row)
        assert operation is not None
        return operation

    def get_operation(self, user_id: str, operation_id: str) -> GovernedOperation | None:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            return self._operation_from_row(self._select_operation(conn, user_id, operation_id))

    def transition(self, user_id: str, operation_id: str, target: OperationStatus) -> bool:
        """Conditionally transition a non-terminal operation once."""
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            current = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            if current is None or target not in _ALLOWED_TRANSITIONS.get(current.status, set()):
                return False
            now = self._now()
            fields = "status = ?, updated_at = ?"
            args: list[Any] = [target.value, now]
            if target is OperationStatus.RUNNING:
                fields += ", started_at = COALESCE(started_at, ?)"
                args.append(now)
            if target in _TERMINAL:
                fields += ", completed_at = COALESCE(completed_at, ?)"
                args.append(now)
            args.extend([operation_id, user_id, current.status.value])
            cursor = conn.execute(
                f"UPDATE operations SET {fields} WHERE operation_id = ? AND user_id = ? AND status = ?", args
            )
            conn.commit()
            return cursor.rowcount == 1

    def append_event(
        self,
        user_id: str,
        operation_id: str,
        kind: str,
        message_safe: str,
        structured_data_safe: dict[str, Any] | None = None,
    ) -> OperationEvent:
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            if self._select_operation(conn, user_id, operation_id) is None:
                raise ValueError("Operation does not exist")
            conn.execute("BEGIN IMMEDIATE")
            next_sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
            timestamp = self._now()
            conn.execute(
                """INSERT INTO operation_events
                   (operation_id, sequence, timestamp, kind, message_safe, structured_data_safe_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    operation_id,
                    next_sequence,
                    timestamp,
                    kind,
                    message_safe,
                    json.dumps(structured_data_safe, sort_keys=True) if structured_data_safe else None,
                ),
            )
            conn.execute(
                "UPDATE operations SET updated_at = ? WHERE operation_id = ? AND user_id = ?",
                (timestamp, operation_id, user_id),
            )
            conn.commit()
        return OperationEvent(
            operation_id=operation_id,
            sequence=next_sequence,
            timestamp=timestamp,
            kind=kind,
            message_safe=message_safe,
            structured_data_safe=structured_data_safe,
        )

    def get_events(self, user_id: str, operation_id: str) -> list[OperationEvent]:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            if self._select_operation(conn, user_id, operation_id) is None:
                return []
            rows = conn.execute(
                """SELECT operation_id, sequence, timestamp, kind, message_safe, structured_data_safe_json
                   FROM operation_events WHERE operation_id = ? ORDER BY sequence""",
                (operation_id,),
            ).fetchall()
        return [
            OperationEvent(
                operation_id=row[0], sequence=row[1], timestamp=row[2], kind=row[3], message_safe=row[4],
                structured_data_safe=json.loads(row[5]) if row[5] else None,
            )
            for row in rows
        ]

    def request_cancel(self, user_id: str, operation_id: str) -> bool:
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            cursor = conn.execute(
                """UPDATE operations SET cancel_requested = 1, updated_at = ?
                   WHERE operation_id = ? AND user_id = ? AND cancel_requested = 0
                     AND status NOT IN (?, ?, ?, ?)""",
                (self._now(), operation_id, user_id, *(status.value for status in _TERMINAL)),
            )
            conn.commit()
            return cursor.rowcount == 1

    def finish(
        self,
        user_id: str,
        operation_id: str,
        status: OperationStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail_safe: str | None = None,
    ) -> GovernedOperation:
        """Record one terminal outcome; repeated terminal callbacks are no-ops."""
        if status not in _TERMINAL:
            raise ValueError("finish requires a terminal operation status")
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            current = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            if current is None:
                raise ValueError("Operation does not exist")
            if current.status in _TERMINAL:
                return current
            if status not in _ALLOWED_TRANSITIONS.get(current.status, set()):
                raise ValueError("Invalid terminal operation transition")
            now = self._now()
            conn.execute(
                """UPDATE operations
                   SET status = ?, result_json = ?, error_code = ?, error_detail_safe = ?,
                       updated_at = ?, completed_at = ?
                   WHERE operation_id = ? AND user_id = ? AND status = ?""",
                (
                    status.value,
                    json.dumps(result, sort_keys=True, default=str) if result is not None else None,
                    error_code,
                    error_detail_safe,
                    now,
                    now,
                    operation_id,
                    user_id,
                    current.status.value,
                ),
            )
            row = self._select_operation(conn, user_id, operation_id)
            conn.commit()
        operation = self._operation_from_row(row)
        assert operation is not None
        return operation

    def complete(
        self,
        user_id: str,
        operation_id: str,
        result: dict[str, Any],
    ) -> GovernedOperation:
        """Set one terminal success result; repeated completion returns it unchanged."""
        return self.finish(
            user_id,
            operation_id,
            OperationStatus.SUCCEEDED,
            result=result,
        )
