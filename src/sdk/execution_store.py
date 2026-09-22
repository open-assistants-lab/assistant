"""Durable receipt storage for the shared execution kernel."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from src.sdk.execution_models import (
    EffectState,
    ExecutionEvent,
    ExecutionRequest,
    ExecutorState,
    Observation,
    Outcome,
    Receipt,
    VerificationState,
)


class ReceiptStateError(ValueError):
    """Raised when a terminal receipt is changed incompatibly."""


class ReceiptStore(Protocol):
    """Persistence boundary shared by desktop and future Jen adapters."""

    async def initialize(self) -> None: ...

    async def create_or_get(self, request: ExecutionRequest) -> Receipt: ...

    async def get(self, receipt_id: str) -> Receipt | None: ...

    async def get_by_request(self, request_id: str) -> Receipt | None: ...

    async def append_event(
        self, receipt_id: str, event_type: str, payload: dict[str, Any]
    ) -> ExecutionEvent: ...

    async def record_observation(
        self, receipt_id: str, observation: Observation
    ) -> Receipt: ...

    async def finalize(
        self,
        receipt_id: str,
        *,
        outcome: Outcome,
        executor_state: ExecutorState,
        effect_state: EffectState,
        verification_state: VerificationState,
        termination_reason: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> Receipt: ...

    async def list_events(self, receipt_id: str) -> list[ExecutionEvent]: ...

    async def rebuild(self, receipt_id: str) -> Receipt | None: ...

    async def close(self) -> None: ...


class SQLiteReceiptStore:
    """SQLite implementation of the receipt store."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open the database, configure WAL, and create the receipt schema."""

        async with self._initialize_lock:
            if self._connection is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA synchronous=NORMAL")
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    run_id TEXT,
                    tool_call_id TEXT,
                    tool_name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    request_created_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    outcome TEXT,
                    executor_state TEXT NOT NULL,
                    effect_state TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    termination_reason TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    content_json TEXT NOT NULL,
                    artifact_ids_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS execution_events (
                    event_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
                    request_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(receipt_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS execution_observations (
                    observation_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    authoritative INTEGER NOT NULL,
                    correlation_id TEXT,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS execution_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT,
                    bytes INTEGER,
                    characters INTEGER,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                """
            )
            await connection.commit()
            self._connection = connection

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def create_or_get(self, request: ExecutionRequest) -> Receipt:
        connection = await self._connection_or_initialize()
        request_data = request.model_dump(mode="json")
        await connection.execute(
            """
            INSERT INTO execution_receipts (
                receipt_id, request_id, run_id, tool_call_id, tool_name, profile,
                request_created_at, request_json, outcome, executor_state,
                effect_state, verification_state, termination_reason, started_at,
                finished_at, content_json, artifact_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            ON CONFLICT(request_id) DO NOTHING
            """,
            (
                uuid.uuid4().hex,
                request.request_id,
                request.run_id,
                request.tool_call_id,
                request.tool_name,
                request.profile,
                _datetime_to_text(request.created_at),
                _json_dumps(request_data),
                ExecutorState.NOT_STARTED.value,
                EffectState.NOT_APPLICABLE.value,
                VerificationState.NOT_REQUESTED.value,
                "{}",
                "[]",
            ),
        )
        await connection.commit()
        cursor = await connection.execute(
            "SELECT request_json FROM execution_receipts WHERE request_id = ?",
            (request.request_id,),
        )
        stored_row = await cursor.fetchone()
        await cursor.close()
        if stored_row is None:
            raise RuntimeError("receipt disappeared after idempotent creation")
        stored_data = json.loads(stored_row["request_json"])
        if _request_identity(stored_data) != _request_identity(_redact_for_storage(request_data)):
            raise ReceiptStateError(
                f"request_id is already bound to a different execution: {request.request_id}"
            )
        receipt = await self.get_by_request(request.request_id)
        if receipt is None:
            raise RuntimeError("receipt disappeared after idempotent creation")
        return receipt

    async def get(self, receipt_id: str) -> Receipt | None:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            "SELECT * FROM execution_receipts WHERE receipt_id = ?",
            (receipt_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return await self._row_to_receipt(row)

    async def get_by_request(self, request_id: str) -> Receipt | None:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            "SELECT * FROM execution_receipts WHERE request_id = ?",
            (request_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return await self._row_to_receipt(row)

    async def list_events(self, receipt_id: str) -> list[ExecutionEvent]:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            """
            SELECT event_id, receipt_id, request_id, sequence, event_type,
                   payload_json, created_at
            FROM execution_events
            WHERE receipt_id = ?
            ORDER BY sequence
            """,
            (receipt_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            ExecutionEvent(
                event_id=row["event_id"],
                receipt_id=row["receipt_id"],
                request_id=row["request_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=_datetime_from_text(row["created_at"]),
            )
            for row in rows
        ]

    async def append_event(
        self, receipt_id: str, event_type: str, payload: dict[str, Any]
    ) -> ExecutionEvent:
        connection = await self._connection_or_initialize()
        created_at = datetime.now(UTC)
        await connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await connection.execute(
                "SELECT request_id, outcome FROM execution_receipts WHERE receipt_id = ?",
                (receipt_id,),
            )
            receipt_row = await cursor.fetchone()
            await cursor.close()
            if receipt_row is None:
                raise ValueError(f"unknown receipt: {receipt_id}")
            if receipt_row["outcome"] is not None:
                raise ReceiptStateError(f"receipt is already finalized: {receipt_id}")

            cursor = await connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM execution_events
                WHERE receipt_id = ?
                """,
                (receipt_id,),
            )
            sequence_row = await cursor.fetchone()
            await cursor.close()
            if sequence_row is None:
                raise RuntimeError("failed to allocate event sequence")
            sequence = int(sequence_row["next_sequence"])
            event = ExecutionEvent(
                event_id=uuid.uuid4().hex,
                receipt_id=receipt_id,
                request_id=receipt_row["request_id"],
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=created_at,
            )
            await connection.execute(
                """
                INSERT INTO execution_events (
                    event_id, receipt_id, request_id, sequence, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.receipt_id,
                    event.request_id,
                    event.sequence,
                    event.event_type,
                    _json_dumps(event.payload),
                    _datetime_to_text(event.created_at),
                ),
            )
            if event_type == "execution.started":
                await connection.execute(
                    """
                    UPDATE execution_receipts
                    SET executor_state = ?, started_at = COALESCE(started_at, ?)
                    WHERE receipt_id = ?
                    """,
                    (
                        ExecutorState.RUNNING.value,
                        _datetime_to_text(created_at),
                        receipt_id,
                    ),
                )
            await connection.commit()
            return event
        except Exception:
            await connection.rollback()
            raise

    async def record_observation(
        self, receipt_id: str, observation: Observation
    ) -> Receipt:
        connection = await self._connection_or_initialize()
        await connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await connection.execute(
                "SELECT 1 FROM execution_receipts WHERE receipt_id = ?",
                (receipt_id,),
            )
            exists = await cursor.fetchone()
            await cursor.close()
            if exists is None:
                raise ValueError(f"unknown receipt: {receipt_id}")
            await connection.execute(
                """
                INSERT INTO execution_observations (
                    observation_id, receipt_id, kind, source, authoritative,
                    correlation_id, observed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO NOTHING
                """,
                (
                    observation.observation_id,
                    receipt_id,
                    observation.kind,
                    observation.source,
                    int(observation.authoritative),
                    observation.correlation_id,
                    _datetime_to_text(observation.observed_at),
                    _json_dumps(observation.payload),
                ),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        receipt = await self.get(receipt_id)
        if receipt is None:
            raise RuntimeError("receipt disappeared after observation")
        return receipt

    async def finalize(
        self,
        receipt_id: str,
        *,
        outcome: Outcome,
        executor_state: ExecutorState,
        effect_state: EffectState,
        verification_state: VerificationState,
        termination_reason: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> Receipt:
        connection = await self._connection_or_initialize()
        requested_content = content or {}
        requested_state = (
            outcome,
            executor_state,
            effect_state,
            verification_state,
            termination_reason,
            requested_content,
        )
        await connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await connection.execute(
                "SELECT * FROM execution_receipts WHERE receipt_id = ?",
                (receipt_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise ValueError(f"unknown receipt: {receipt_id}")
            current = await self._row_to_receipt(row)
            if current.outcome is not None:
                current_state = (
                    current.outcome,
                    current.executor_state,
                    current.effect_state,
                    current.verification_state,
                    current.termination_reason,
                    current.content,
                )
                if current_state == requested_state:
                    await connection.commit()
                    return current
                raise ReceiptStateError(f"receipt is already finalized: {receipt_id}")

            finished_at = datetime.now(UTC)
            payload = {
                "outcome": outcome.value,
                "executor_state": executor_state.value,
                "effect_state": effect_state.value,
                "verification_state": verification_state.value,
                "termination_reason": termination_reason,
                "finished_at": _datetime_to_text(finished_at),
                "content": requested_content,
            }
            cursor = await connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM execution_events WHERE receipt_id = ?",
                (receipt_id,),
            )
            sequence_row = await cursor.fetchone()
            await cursor.close()
            if sequence_row is None:
                raise RuntimeError("failed to allocate completion sequence")
            sequence = int(sequence_row["next_sequence"])
            await connection.execute(
                """
                INSERT INTO execution_events (
                    event_id, receipt_id, request_id, sequence, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    receipt_id,
                    current.request_id,
                    sequence,
                    "execution.completed",
                    _json_dumps(payload),
                    _datetime_to_text(finished_at),
                ),
            )
            await connection.execute(
                """
                UPDATE execution_receipts
                SET outcome = ?, executor_state = ?, effect_state = ?,
                    verification_state = ?, termination_reason = ?,
                    finished_at = ?, content_json = ?
                WHERE receipt_id = ? AND outcome IS NULL
                """,
                (
                    outcome.value,
                    executor_state.value,
                    effect_state.value,
                    verification_state.value,
                    termination_reason,
                    _datetime_to_text(finished_at),
                    _json_dumps(requested_content),
                    receipt_id,
                ),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        result = await self.get(receipt_id)
        if result is None:
            raise RuntimeError("receipt disappeared after finalization")
        return result

    async def rebuild(self, receipt_id: str) -> Receipt | None:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            "SELECT * FROM execution_receipts WHERE receipt_id = ?",
            (receipt_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None

        request_data = json.loads(row["request_json"])
        rebuilt = Receipt(
            receipt_id=row["receipt_id"],
            request_id=request_data["request_id"],
            run_id=request_data.get("run_id"),
            tool_call_id=request_data.get("tool_call_id"),
            tool_name=request_data["tool_name"],
            profile=request_data["profile"],
            executor_state=ExecutorState.NOT_STARTED,
            effect_state=EffectState.NOT_APPLICABLE,
            verification_state=VerificationState.NOT_REQUESTED,
        )
        for event in await self.list_events(receipt_id):
            if event.event_type == "execution.started":
                rebuilt.executor_state = ExecutorState.RUNNING
                rebuilt.started_at = event.created_at
            elif event.event_type == "execution.completed":
                payload = event.payload
                rebuilt.outcome = Outcome(payload["outcome"])
                rebuilt.executor_state = ExecutorState(payload["executor_state"])
                rebuilt.effect_state = EffectState(payload["effect_state"])
                rebuilt.verification_state = VerificationState(
                    payload["verification_state"]
                )
                rebuilt.termination_reason = payload.get("termination_reason")
                rebuilt.finished_at = _datetime_from_text(payload["finished_at"])
                rebuilt.content = payload.get("content") or {}
        observations = await self._observations_for(receipt_id)
        rebuilt.observations = observations
        return rebuilt

    async def _connection_or_initialize(self) -> aiosqlite.Connection:
        if self._connection is None:
            await self.initialize()
        if self._connection is None:
            raise RuntimeError("receipt store failed to initialize")
        return self._connection

    async def _row_to_receipt(self, row: aiosqlite.Row) -> Receipt:
        observations = await self._observations_for(row["receipt_id"])
        return Receipt(
            receipt_id=row["receipt_id"],
            request_id=row["request_id"],
            run_id=row["run_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            profile=row["profile"],
            outcome=Outcome(row["outcome"]) if row["outcome"] is not None else None,
            executor_state=ExecutorState(row["executor_state"]),
            effect_state=EffectState(row["effect_state"]),
            verification_state=VerificationState(row["verification_state"]),
            termination_reason=row["termination_reason"],
            started_at=_optional_datetime(row["started_at"]),
            finished_at=_optional_datetime(row["finished_at"]),
            content=json.loads(row["content_json"]),
            observations=observations,
            artifact_ids=json.loads(row["artifact_ids_json"]),
        )

    async def _observations_for(self, receipt_id: str) -> list[Observation]:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            """
            SELECT observation_id, kind, source, authoritative, correlation_id,
                   observed_at, payload_json
            FROM execution_observations
            WHERE receipt_id = ?
            ORDER BY observed_at, observation_id
            """,
            (receipt_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            Observation(
                observation_id=row["observation_id"],
                kind=row["kind"],
                source=row["source"],
                authoritative=bool(row["authoritative"]),
                correlation_id=row["correlation_id"],
                observed_at=_datetime_from_text(row["observed_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]


def _datetime_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: str | None) -> datetime | None:
    return _datetime_from_text(value) if value is not None else None


def _request_identity(request_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: request_data.get(key)
        for key in ("request_id", "run_id", "tool_call_id", "tool_name", "profile", "arguments")
    }


_SENSITIVE_KEY_PARTS = ("api_key", "password", "secret", "token")


def _json_dumps(value: Any) -> str:
    return json.dumps(_redact_for_storage(value), sort_keys=True, separators=(",", ":"))


def _redact_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text == "key" or any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_for_storage(item)
        return redacted
    if isinstance(value, list):
        return [_redact_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_for_storage(item) for item in value]
    return value
