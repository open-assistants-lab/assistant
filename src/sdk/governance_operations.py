"""Durable operation ledger for asynchronous governed tools (issue #21 Phase 1).

The ledger deliberately uses the same per-user ``governance.db`` as proposals.
That lets proposal consumption and one operation creation happen in one SQLite
transaction; it is not a subagent queue or a HybridDB index.
"""

from __future__ import annotations

import hashlib
import hmac
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
    {
        OperationStatus.SUCCEEDED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.UNCERTAIN,
    }
)
_ALLOWED_TRANSITIONS = {
    OperationStatus.QUEUED: {
        OperationStatus.RUNNING,
        OperationStatus.CANCELLED,
        OperationStatus.UNCERTAIN,
    },
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
    executor_url: str | None = None
    manifest_hash: str | None = None
    dispatch_idempotency_key: str | None = None


class ExternalOperationCreation(BaseModel):
    """New external operation and its one-time callback capability."""

    operation: GovernedOperation
    callback_capability: str | None = None


class DispatchClaim(BaseModel):
    operation_id: str
    idempotency_key: str
    executor_url: str
    manifest_hash: str | None = None
    status: str


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
    def _callback_capability(
        secret: str, operation_id: str, proposal_id: str, arguments_hash: str
    ) -> str:
        if not secret:
            raise ValueError("External async execution requires GOVERNANCE_OPERATION_CALLBACK_SECRET")
        binding = f"{operation_id}:{proposal_id}:{arguments_hash}".encode()
        return hmac.new(secret.encode(), binding, hashlib.sha256).hexdigest()

    @staticmethod
    def _callback_secret() -> str:
        from src.config import get_settings

        return str(getattr(get_settings().governance, "operation_callback_secret", "") or "")

    @staticmethod
    def _external_executor_allowed_hosts() -> list[str]:
        from src.config import get_settings

        configured = getattr(get_settings().governance, "external_executor_allowed_hosts", [])
        return [str(host).strip().casefold() for host in configured or [] if str(host).strip()]

    @classmethod
    def _validate_external_executor(cls, executor: Any) -> None:
        """Fail closed unless a deployment allowlist authorizes the dispatch target."""
        from urllib.parse import urlsplit

        parsed = urlsplit(str(executor.dispatch_url))
        host = (parsed.hostname or "").casefold()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("External executor dispatch URL has an invalid port") from exc
        authority = f"{host}:{port}" if port is not None else host
        allowed = cls._external_executor_allowed_hosts()
        if not allowed or (host not in allowed and authority not in allowed):
            raise ValueError("External executor URL host is not deployment-allowlisted")

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
                error_detail_safe TEXT,
                executor_url TEXT,
                manifest_hash TEXT,
                dispatch_idempotency_key TEXT
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operations)")}
        for name, ddl in (
            ("executor_url", "TEXT"),
            ("manifest_hash", "TEXT"),
            ("dispatch_idempotency_key", "TEXT"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE operations ADD COLUMN {name} {ddl}")
        conn.execute("""CREATE TABLE IF NOT EXISTS operation_dispatches (
            operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, executor_url TEXT NOT NULL,
            manifest_hash TEXT, callback_capability_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL, claimed_by TEXT, attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_operations_user_status ON operations(user_id, status)"
        )
        conn.commit()

    @classmethod
    def _operation_from_row(
        cls, row: sqlite3.Row | tuple[Any, ...] | None
    ) -> GovernedOperation | None:
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
            executor_url=row[15] if len(row) > 15 else None,
            manifest_hash=row[16] if len(row) > 16 else None,
            dispatch_idempotency_key=row[17] if len(row) > 17 else None,
        )

    @staticmethod
    def _select_operation(
        conn: sqlite3.Connection, user_id: str, operation_id: str
    ) -> tuple[Any, ...] | None:
        return conn.execute(
            """SELECT operation_id, proposal_id, user_id, tool_name, arguments_json,
                      arguments_hash, status, cancel_requested, created_at, started_at,
                      updated_at, completed_at, result_json, error_code, error_detail_safe, executor_url, manifest_hash, dispatch_idempotency_key
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

    def approve_pending_and_create_operation(
        self, user_id: str, proposal_id: str
    ) -> tuple[GovernedOperation, bool]:
        """Atomically approve a pending proposal and consume it into one operation.

        Returns the durable operation and whether this caller made the approval
        transition. Repeated callers return the existing operation without a
        second approval or operation.
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
                return operation, False
            proposal = conn.execute(
                "SELECT tool, arguments, status FROM proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if proposal is None or proposal[2] not in ("pending", "approved"):
                conn.rollback()
                raise ValueError("Proposal cannot be accepted for async execution")
            approved_now = proposal[2] == "pending"
            if approved_now:
                approved = conn.execute(
                    "UPDATE proposals SET status = 'approved' WHERE proposal_id = ? AND status = 'pending'",
                    (proposal_id,),
                )
                if approved.rowcount != 1:
                    conn.rollback()
                    raise ValueError("Proposal approval was consumed concurrently")
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
        return operation, approved_now

    def get_operation(self, user_id: str, operation_id: str) -> GovernedOperation | None:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            return self._operation_from_row(self._select_operation(conn, user_id, operation_id))

    def list_operations(
        self, user_id: str, status: OperationStatus | str | None = None
    ) -> list[GovernedOperation]:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            sql = """SELECT operation_id, proposal_id, user_id, tool_name, arguments_json,
                            arguments_hash, status, cancel_requested, created_at, started_at,
                            updated_at, completed_at, result_json, error_code, error_detail_safe, executor_url, manifest_hash, dispatch_idempotency_key
                     FROM operations WHERE user_id = ?"""
            params: list[Any] = [user_id]
            if status is not None:
                sql += " AND status = ?"
                params.append(OperationStatus(status).value)
            sql += " ORDER BY created_at, operation_id"
            rows = conn.execute(sql, params).fetchall()
        return [
            operation for row in rows if (operation := self._operation_from_row(row)) is not None
        ]

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
                f"UPDATE operations SET {fields} WHERE operation_id = ? AND user_id = ? AND status = ?",
                args,
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
                    json.dumps(structured_data_safe, sort_keys=True)
                    if structured_data_safe
                    else None,
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
                operation_id=row[0],
                sequence=row[1],
                timestamp=row[2],
                kind=row[3],
                message_safe=row[4],
                structured_data_safe=json.loads(row[5]) if row[5] else None,
            )
            for row in rows
        ]

    def request_cancel(self, user_id: str, operation_id: str) -> bool:
        """Durably request cancellation; queued work becomes terminal before dispatch.

        A dispatcher claims work by moving it to ``running`` in the same
        transaction. Therefore a queued cancellation and a dispatch claim
        cannot both win: queued operations are cancelled with their outbox,
        while already-running operations retain a durable cancellation request
        for the external executor to confirm.
        """
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            current = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            if current is None or current.status in _TERMINAL or current.cancel_requested:
                conn.rollback()
                return False
            now = self._now()
            if current.status is OperationStatus.QUEUED:
                cursor = conn.execute(
                    """UPDATE operations
                       SET status = ?, cancel_requested = 1, completed_at = ?, updated_at = ?
                       WHERE operation_id = ? AND user_id = ? AND status = ? AND cancel_requested = 0""",
                    (OperationStatus.CANCELLED.value, now, now, operation_id, user_id, OperationStatus.QUEUED.value),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute(
                    """UPDATE operation_dispatches SET status = 'cancelled', updated_at = ?
                       WHERE operation_id = ? AND user_id = ? AND status = 'queued'""",
                    (now, operation_id, user_id),
                )
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO operation_events
                       (operation_id, sequence, timestamp, kind, message_safe)
                       VALUES (?, ?, ?, ?, ?)""",
                    (operation_id, sequence, now, "cancelled", "Cancelled before external dispatch."),
                )
            else:
                cursor = conn.execute(
                    """UPDATE operations SET cancel_requested = 1, updated_at = ?
                       WHERE operation_id = ? AND user_id = ? AND status = ? AND cancel_requested = 0""",
                    (now, operation_id, user_id, OperationStatus.RUNNING.value),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
            conn.commit()
            return True

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

    def approve_pending_and_create_external_operation(
        self, user_id: str, proposal_id: str, executor: Any
    ) -> tuple[ExternalOperationCreation, bool]:
        """Atomically accept an external operation and create its durable outbox."""
        callback_secret = self._callback_secret()
        if not callback_secret:
            raise ValueError("External async execution requires GOVERNANCE_OPERATION_CALLBACK_SECRET")
        self._validate_external_executor(executor)
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT operation_id FROM operations WHERE proposal_id=? AND user_id=?",
                (proposal_id, user_id),
            ).fetchone()
            if existing:
                op = self._operation_from_row(self._select_operation(conn, user_id, existing[0]))
                conn.commit()
                assert op is not None
                return ExternalOperationCreation(operation=op), False
            proposal = conn.execute(
                "SELECT tool, arguments, status FROM proposals WHERE proposal_id=? AND user_id=?",
                (proposal_id, user_id),
            ).fetchone()
            if proposal is None or proposal[2] not in ("pending", "approved"):
                conn.rollback()
                raise ValueError("Proposal cannot be accepted for external execution")
            approved_now = proposal[2] == "pending"
            if approved_now:
                cur = conn.execute(
                    "UPDATE proposals SET status='approved' WHERE proposal_id=? AND user_id=? AND status='pending'",
                    (proposal_id, user_id),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError("Proposal approval was consumed concurrently")
            if (
                conn.execute(
                    "UPDATE proposals SET status='consumed' WHERE proposal_id=? AND user_id=? AND status='approved'",
                    (proposal_id, user_id),
                ).rowcount
                != 1
            ):
                conn.rollback()
                raise ValueError("Proposal approval was consumed concurrently")
            now, operation_id = self._now(), uuid.uuid4().hex
            arguments = json.loads(proposal[1])
            key = uuid.uuid4().hex
            arguments_hash = self._arguments_hash(arguments)
            capability = self._callback_capability(
                callback_secret, operation_id, proposal_id, arguments_hash
            )
            conn.execute(
                """INSERT INTO operations (operation_id,proposal_id,user_id,tool_name,arguments_json,arguments_hash,status,cancel_requested,created_at,updated_at,executor_url,manifest_hash,dispatch_idempotency_key) VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?)""",
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
                    executor.dispatch_url,
                    executor.manifest_hash,
                    key,
                ),
            )
            conn.execute(
                "INSERT INTO operation_dispatches (operation_id,user_id,executor_url,manifest_hash,callback_capability_hash,idempotency_key,status,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    user_id,
                    executor.dispatch_url,
                    executor.manifest_hash,
                    hashlib.sha256(capability.encode()).hexdigest(),
                    key,
                    "queued",
                    now,
                ),
            )
            op = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            conn.commit()
        assert op is not None
        return ExternalOperationCreation(operation=op, callback_capability=capability), approved_now

    def queued_dispatch_ids(self, user_id: str, *, limit: int | None = None) -> list[str]:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            query = (
                "SELECT d.operation_id FROM operation_dispatches d "
                "JOIN operations o ON o.operation_id = d.operation_id AND o.user_id = d.user_id "
                "WHERE d.user_id=? AND d.status='queued' AND o.status='queued' "
                "AND o.cancel_requested=0 ORDER BY d.updated_at, d.operation_id"
            )
            params: tuple[Any, ...] = (user_id,)
            if limit is not None:
                query += " LIMIT ?"
                params = (user_id, limit)
            rows = conn.execute(query, params).fetchall()
        return [str(row[0]) for row in rows]

    def dispatch_envelope(self, user_id: str, operation_id: str) -> dict[str, Any]:
        """Return the immutable executor envelope for an already-approved operation."""
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            op = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            dispatch = conn.execute(
                "SELECT manifest_hash,idempotency_key FROM operation_dispatches WHERE operation_id=? AND user_id=?",
                (operation_id, user_id),
            ).fetchone()
        if op is None or dispatch is None:
            raise ValueError("External operation does not exist")
        token = self._callback_capability(
            self._callback_secret(), op.operation_id, op.proposal_id, op.arguments_hash
        )
        return {
            "operation_id": op.operation_id,
            "proposal_id": op.proposal_id,
            "tool_name": op.tool_name,
            "arguments": op.arguments,
            "arguments_hash": op.arguments_hash,
            "manifest_hash": dispatch[0],
            "idempotency_key": dispatch[1],
            "callback_token": token,
        }

    def reconcile_claimed_dispatches(self, user_id: str) -> int:
        """Compatibility alias for restart reconciliation."""
        return self.reconcile_unfinished_dispatches(user_id)

    def reconcile_unfinished_dispatches(self, user_id: str) -> int:
        """Record claimed *and acknowledged* unfinished dispatches as uncertain.

        A post/restart process cannot know whether an external executor acted,
        so it records evidence and never requeues or replays the operation.
        """
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT operation_id FROM operation_dispatches WHERE user_id=? "
                "AND status IN ('claimed', 'dispatched')",
                (user_id,),
            ).fetchall()
            reconciled = 0
            now = self._now()
            for (operation_id,) in rows:
                changed = conn.execute(
                    "UPDATE operation_dispatches SET status='uncertain', updated_at=? "
                    "WHERE operation_id=? AND user_id=? AND status IN ('claimed', 'dispatched')",
                    (now, operation_id, user_id),
                )
                if changed.rowcount != 1:
                    continue
                transitioned = conn.execute(
                    "UPDATE operations SET status=?, completed_at=?, updated_at=?, error_code=?, error_detail_safe=? "
                    "WHERE operation_id=? AND user_id=? AND status IN (?,?)",
                    (
                        OperationStatus.UNCERTAIN.value,
                        now,
                        now,
                        "dispatch_outcome_unknown",
                        "External dispatch outcome could not be confirmed after restart.",
                        operation_id,
                        user_id,
                        OperationStatus.QUEUED.value,
                        OperationStatus.RUNNING.value,
                    ),
                )
                if transitioned.rowcount == 1:
                    sequence = conn.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id=?",
                        (operation_id,),
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO operation_events (operation_id,sequence,timestamp,kind,message_safe) VALUES (?,?,?,?,?)",
                        (
                            operation_id,
                            sequence,
                            now,
                            "uncertain",
                            "External dispatch outcome could not be confirmed after restart.",
                        ),
                    )
                reconciled += 1
            conn.commit()
        return reconciled

    def get_dispatch(self, user_id: str, operation_id: str) -> DispatchClaim | None:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT operation_id,idempotency_key,executor_url,manifest_hash,status FROM operation_dispatches WHERE operation_id=? AND user_id=?",
                (operation_id, user_id),
            ).fetchone()
        return (
            DispatchClaim(
                operation_id=row[0],
                idempotency_key=row[1],
                executor_url=row[2],
                manifest_hash=row[3],
                status=row[4],
            )
            if row
            else None
        )

    def claim_dispatch(self, user_id: str, operation_id: str, worker_id: str) -> DispatchClaim:
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            now = self._now()
            started = conn.execute(
                """UPDATE operations SET status=?, started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE operation_id=? AND user_id=? AND status=? AND cancel_requested=0""",
                (OperationStatus.RUNNING.value, now, now, operation_id, user_id, OperationStatus.QUEUED.value),
            )
            if started.rowcount != 1:
                conn.rollback()
                raise ValueError("Dispatch is not queued")
            cur = conn.execute(
                """UPDATE operation_dispatches SET status='claimed',claimed_by=?,attempts=attempts+1,updated_at=?
                   WHERE operation_id=? AND user_id=? AND status='queued'""",
                (worker_id, now, operation_id, user_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise ValueError("Dispatch is not queued")
            row = conn.execute(
                "SELECT operation_id,idempotency_key,executor_url,manifest_hash,status FROM operation_dispatches WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            conn.commit()
        return DispatchClaim(
            operation_id=row[0],
            idempotency_key=row[1],
            executor_url=row[2],
            manifest_hash=row[3],
            status=row[4],
        )

    def record_dispatch_result(
        self, user_id: str, operation_id: str, worker_id: str, *, acknowledged: bool | None
    ) -> GovernedOperation:
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            now = self._now()
            # Only an explicit executor acknowledgement is safe. A non-2xx
            # response is still an ambiguous delivery outcome: the remote side
            # may have accepted and started the side effect before returning it.
            # Never requeue/replay either that case or a transport failure.
            if acknowledged is not True:
                claim = conn.execute(
                    "UPDATE operation_dispatches SET status='uncertain',updated_at=? WHERE operation_id=? AND user_id=? AND claimed_by=? AND status='claimed'",
                    (now, operation_id, user_id, worker_id),
                )
                if claim.rowcount != 1:
                    raise ValueError("Dispatch claim mismatch")
                transitioned = conn.execute(
                    "UPDATE operations SET status=?,completed_at=?,updated_at=?,error_code=?,error_detail_safe=? WHERE operation_id=? AND user_id=? AND status IN (?,?)",
                    (
                        OperationStatus.UNCERTAIN.value,
                        now,
                        now,
                        "dispatch_outcome_unknown",
                        "External dispatch outcome could not be confirmed.",
                        operation_id,
                        user_id,
                        OperationStatus.QUEUED.value,
                        OperationStatus.RUNNING.value,
                    ),
                )
                if transitioned.rowcount == 1:
                    sequence = conn.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id=?",
                        (operation_id,),
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO operation_events (operation_id,sequence,timestamp,kind,message_safe) VALUES (?,?,?,?,?)",
                        (operation_id, sequence, now, "uncertain", "External dispatch outcome could not be confirmed."),
                    )
            else:
                status = "dispatched" if acknowledged else "queued"
                cur = conn.execute(
                    "UPDATE operation_dispatches SET status=?,claimed_by=NULL,updated_at=? WHERE operation_id=? AND user_id=? AND claimed_by=? AND status='claimed'",
                    (status, now, operation_id, user_id, worker_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("Dispatch claim mismatch")
            op = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            conn.commit()
        assert op is not None
        return op

    def _validate_callback(
        self, user_id: str, operation_id: str, capability: str, **binding: str | None
    ) -> GovernedOperation:
        with self._conn(user_id) as conn:
            self._ensure_schema(conn)
            op = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            row = conn.execute(
                "SELECT callback_capability_hash FROM operation_dispatches WHERE operation_id=? AND user_id=?",
                (operation_id, user_id),
            ).fetchone()
        if (
            op is None
            or row is None
            or not hmac.compare_digest(row[0], hashlib.sha256(capability.encode()).hexdigest())
        ):
            raise PermissionError("Invalid operation callback capability")
        if (
            binding.get("proposal_id") != op.proposal_id
            or binding.get("tool_name") != op.tool_name
            or binding.get("arguments_hash") != op.arguments_hash
            or binding.get("manifest_hash") != op.manifest_hash
        ):
            raise PermissionError("Operation callback binding mismatch")
        return op

    def append_callback_event(
        self,
        user_id: str,
        operation_id: str,
        capability: str,
        sequence: int,
        kind: str,
        message_safe: str,
        *,
        proposal_id: str,
        tool_name: str,
        arguments_hash: str,
        manifest_hash: str | None = None,
    ) -> OperationEvent:
        """Atomically authenticate, order, and append an executor checkpoint."""
        with self._lock, self._conn(user_id) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            op = self._operation_from_row(self._select_operation(conn, user_id, operation_id))
            cap_row = conn.execute(
                "SELECT callback_capability_hash FROM operation_dispatches WHERE operation_id=? AND user_id=?",
                (operation_id, user_id),
            ).fetchone()
            if (
                op is None or cap_row is None
                or not hmac.compare_digest(cap_row[0], hashlib.sha256(capability.encode()).hexdigest())
                or proposal_id != op.proposal_id or tool_name != op.tool_name
                or arguments_hash != op.arguments_hash or manifest_hash != op.manifest_hash
            ):
                conn.rollback()
                raise PermissionError("Invalid operation callback binding")
            if op.status in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
                OperationStatus.UNCERTAIN,
            ):
                conn.rollback()
                raise ValueError("Operation is terminal; progress is closed")
            existing = conn.execute(
                "SELECT operation_id, sequence, timestamp, kind, message_safe, structured_data_safe_json "
                "FROM operation_events WHERE operation_id=? AND sequence=?",
                (operation_id, sequence),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return OperationEvent(
                    operation_id=existing[0], sequence=existing[1], timestamp=existing[2],
                    kind=existing[3], message_safe=existing[4],
                    structured_data_safe=json.loads(existing[5]) if existing[5] else None,
                )
            expected = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM operation_events WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
            if sequence != expected:
                conn.rollback()
                raise ValueError("Operation event sequence is not next")
            timestamp = self._now()
            if op.status is OperationStatus.QUEUED:
                conn.execute(
                    "UPDATE operations SET status=?, started_at=COALESCE(started_at, ?), updated_at=? "
                    "WHERE operation_id=? AND user_id=? AND status=?",
                    (OperationStatus.RUNNING.value, timestamp, timestamp, operation_id, user_id, OperationStatus.QUEUED.value),
                )
            conn.execute(
                "INSERT INTO operation_events (operation_id,sequence,timestamp,kind,message_safe) VALUES (?,?,?,?,?)",
                (operation_id, sequence, timestamp, kind, message_safe),
            )
            conn.execute(
                "UPDATE operations SET updated_at=? WHERE operation_id=? AND user_id=?",
                (timestamp, operation_id, user_id),
            )
            conn.commit()
        return OperationEvent(operation_id=operation_id, sequence=sequence, timestamp=timestamp, kind=kind, message_safe=message_safe)

    def finish_callback(
        self,
        user_id: str,
        operation_id: str,
        capability: str,
        status: OperationStatus,
        *,
        proposal_id: str,
        tool_name: str,
        arguments_hash: str,
        manifest_hash: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> GovernedOperation:
        self._validate_callback(
            user_id,
            operation_id,
            capability,
            proposal_id=proposal_id,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            manifest_hash=manifest_hash,
        )
        op = self.get_operation(user_id, operation_id)
        assert op is not None
        if op.status is OperationStatus.QUEUED:
            self.transition(user_id, operation_id, OperationStatus.RUNNING)
        return self.finish(user_id, operation_id, status, result=result)
