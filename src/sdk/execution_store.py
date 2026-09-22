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
                _json_dumps(request.model_dump(mode="json")),
                ExecutorState.NOT_STARTED.value,
                EffectState.NOT_APPLICABLE.value,
                VerificationState.NOT_REQUESTED.value,
                "{}",
                "[]",
            ),
        )
        await connection.commit()
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
        raise NotImplementedError

    async def record_observation(
        self, receipt_id: str, observation: Observation
    ) -> Receipt:
        raise NotImplementedError

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
        raise NotImplementedError

    async def rebuild(self, receipt_id: str) -> Receipt | None:
        raise NotImplementedError

    async def _connection_or_initialize(self) -> aiosqlite.Connection:
        if self._connection is None:
            await self.initialize()
        if self._connection is None:
            raise RuntimeError("receipt store failed to initialize")
        return self._connection

    async def _row_to_receipt(self, row: aiosqlite.Row) -> Receipt:
        connection = await self._connection_or_initialize()
        cursor = await connection.execute(
            """
            SELECT observation_id, kind, source, authoritative, correlation_id,
                   observed_at, payload_json
            FROM execution_observations
            WHERE receipt_id = ?
            ORDER BY observed_at, observation_id
            """,
            (row["receipt_id"],),
        )
        observation_rows = await cursor.fetchall()
        await cursor.close()
        observations = [
            Observation(
                observation_id=observation_row["observation_id"],
                kind=observation_row["kind"],
                source=observation_row["source"],
                authoritative=bool(observation_row["authoritative"]),
                correlation_id=observation_row["correlation_id"],
                observed_at=_datetime_from_text(observation_row["observed_at"]),
                payload=json.loads(observation_row["payload_json"]),
            )
            for observation_row in observation_rows
        ]
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


def _datetime_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: str | None) -> datetime | None:
    return _datetime_from_text(value) if value is not None else None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
