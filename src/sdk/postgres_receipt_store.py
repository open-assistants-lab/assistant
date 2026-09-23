"""PostgreSQL receipt-store adapter for hosted execution runtimes."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

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
from src.sdk.execution_store import (
    ReceiptStateError,
    ReceiptStore,
    _json_dumps,
    _redact_for_storage,
    _request_identity,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    run_id TEXT,
    tool_call_id TEXT,
    tool_name TEXT NOT NULL,
    request_created_at TIMESTAMPTZ NOT NULL,
    request_json JSONB NOT NULL,
    outcome TEXT,
    executor_state TEXT NOT NULL,
    effect_state TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    termination_reason TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    content_json JSONB NOT NULL,
    artifact_ids_json JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_events (
    event_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(receipt_id, sequence)
);
CREATE TABLE IF NOT EXISTS execution_observations (
    observation_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    authoritative BOOLEAN NOT NULL,
    correlation_id TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_artifacts (
    artifact_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES execution_receipts(receipt_id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT,
    bytes BIGINT,
    characters BIGINT,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_execution_events_receipt
    ON execution_events(receipt_id, sequence);
CREATE INDEX IF NOT EXISTS idx_execution_observations_receipt
    ON execution_observations(receipt_id, observed_at, observation_id);
"""


class PostgresReceiptStore:
    """PostgreSQL implementation of the shared :class:`ReceiptStore` contract.

    The pool may be injected by Jen's application lifecycle. Supplying a DSN
    creates an owned ``asyncpg`` pool, which is closed by :meth:`close`.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        pool: asyncpg.Pool[Any] | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if pool is None and not dsn:
            raise ValueError("PostgresReceiptStore requires a DSN or an asyncpg pool")
        self._dsn = dsn
        self._pool = pool
        self._min_size = min_size
        self._max_size = max_size
        self._owns_pool = pool is None
        self._initialize_lock = asyncio.Lock()

    @property
    def pool(self) -> asyncpg.Pool[Any] | None:
        return self._pool

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._pool is None:
                assert self._dsn is not None
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
            async with self._pool.acquire() as connection:
                await connection.execute(_SCHEMA)

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None

    async def create_or_get(self, request: ExecutionRequest) -> Receipt:
        pool = await self._pool_or_initialize()
        request_data = request.model_dump(mode="json")
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO execution_receipts (
                    receipt_id, request_id, run_id, tool_call_id, tool_name,
                    request_created_at, request_json, outcome, executor_state,
                    effect_state, verification_state, content_json, artifact_ids_json
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NULL, $8, $9, $10, $11::jsonb, $12::jsonb)
                ON CONFLICT(request_id) DO NOTHING
                """,
                uuid.uuid4().hex,
                request.request_id,
                request.run_id,
                request.tool_call_id,
                request.tool_name,
                request.created_at,
                _json_dumps(request_data),
                ExecutorState.NOT_STARTED.value,
                EffectState.NOT_APPLICABLE.value,
                VerificationState.NOT_REQUESTED.value,
                "{}",
                "[]",
            )
            stored = await connection.fetchrow(
                "SELECT request_json FROM execution_receipts WHERE request_id=$1",
                request.request_id,
            )
        if stored is None:
            raise RuntimeError("receipt disappeared after idempotent creation")
        stored_request = _json_value(stored["request_json"])
        if _request_identity(dict(stored_request)) != _request_identity(
            _redact_for_storage(request_data)
        ):
            raise ReceiptStateError(
                f"request_id is already bound to a different execution: {request.request_id}"
            )
        receipt = await self.get_by_request(request.request_id)
        if receipt is None:
            raise RuntimeError("receipt disappeared after idempotent creation")
        return receipt

    async def get(self, receipt_id: str) -> Receipt | None:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM execution_receipts WHERE receipt_id=$1", receipt_id
            )
            return await self._row_to_receipt(connection, row) if row else None

    async def get_by_request(self, request_id: str) -> Receipt | None:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM execution_receipts WHERE request_id=$1", request_id
            )
            return await self._row_to_receipt(connection, row) if row else None

    async def list_events(self, receipt_id: str) -> list[ExecutionEvent]:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT event_id, receipt_id, request_id, sequence, event_type,
                       payload_json, created_at
                FROM execution_events WHERE receipt_id=$1 ORDER BY sequence
                """,
                receipt_id,
            )
        return [self._event_from_row(row) for row in rows]

    async def append_event(
        self, receipt_id: str, event_type: str, payload: dict[str, Any]
    ) -> ExecutionEvent:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT request_id, outcome, executor_state
                    FROM execution_receipts WHERE receipt_id=$1 FOR UPDATE
                    """,
                    receipt_id,
                )
                if row is None:
                    raise ValueError(f"unknown receipt: {receipt_id}")
                if row["outcome"] is not None:
                    raise ReceiptStateError(f"receipt is already finalized: {receipt_id}")
                if (
                    event_type == "execution.started"
                    and row["executor_state"] == ExecutorState.RUNNING.value
                ):
                    raise ReceiptStateError(f"receipt is already running: {receipt_id}")
                sequence = await self._next_sequence(connection, receipt_id)
                event = ExecutionEvent(
                    event_id=uuid.uuid4().hex,
                    receipt_id=receipt_id,
                    request_id=row["request_id"],
                    sequence=sequence,
                    event_type=event_type,
                    payload=payload,
                    created_at=datetime.now(UTC),
                )
                await connection.execute(
                    """
                    INSERT INTO execution_events
                        (event_id, receipt_id, request_id, sequence, event_type, payload_json, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    """,
                    event.event_id,
                    event.receipt_id,
                    event.request_id,
                    event.sequence,
                    event.event_type,
                    _json_dumps(event.payload),
                    event.created_at,
                )
                if event_type == "execution.started":
                    await connection.execute(
                        """
                        UPDATE execution_receipts
                        SET executor_state=$1, started_at=COALESCE(started_at, $2)
                        WHERE receipt_id=$3
                        """,
                        ExecutorState.RUNNING.value,
                        event.created_at,
                        receipt_id,
                    )
                return event

    async def record_observation(
        self, receipt_id: str, observation: Observation
    ) -> Receipt:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    "SELECT 1 FROM execution_receipts WHERE receipt_id=$1", receipt_id
                )
                if exists is None:
                    raise ValueError(f"unknown receipt: {receipt_id}")
                await connection.execute(
                    """
                    INSERT INTO execution_observations
                        (observation_id, receipt_id, kind, source, authoritative,
                         correlation_id, observed_at, payload_json)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    ON CONFLICT(observation_id) DO NOTHING
                    """,
                    observation.observation_id,
                    receipt_id,
                    observation.kind,
                    observation.source,
                    observation.authoritative,
                    observation.correlation_id,
                    observation.observed_at,
                    _json_dumps(observation.payload),
                )
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
        requested_content = content or {}
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM execution_receipts WHERE receipt_id=$1 FOR UPDATE",
                    receipt_id,
                )
                if row is None:
                    raise ValueError(f"unknown receipt: {receipt_id}")
                if row["outcome"] is not None:
                    current_state = (
                        Outcome(row["outcome"]),
                        ExecutorState(row["executor_state"]),
                        EffectState(row["effect_state"]),
                        VerificationState(row["verification_state"]),
                        row["termination_reason"],
                        dict(_json_value(row["content_json"])),
                    )
                    requested_state = (
                        outcome,
                        executor_state,
                        effect_state,
                        verification_state,
                        termination_reason,
                        requested_content,
                    )
                    if current_state == requested_state:
                        return await self._row_to_receipt(connection, row)
                    raise ReceiptStateError(f"receipt is already finalized: {receipt_id}")
                finished_at = datetime.now(UTC)
                sequence = await self._next_sequence(connection, receipt_id)
                payload = {
                    "outcome": outcome.value,
                    "executor_state": executor_state.value,
                    "effect_state": effect_state.value,
                    "verification_state": verification_state.value,
                    "termination_reason": termination_reason,
                    "finished_at": finished_at.isoformat(),
                    "content": requested_content,
                }
                await connection.execute(
                    """
                    INSERT INTO execution_events
                        (event_id, receipt_id, request_id, sequence, event_type, payload_json, created_at)
                    VALUES ($1, $2, $3, $4, 'execution.completed', $5::jsonb, $6)
                    """,
                    uuid.uuid4().hex,
                    receipt_id,
                    row["request_id"],
                    sequence,
                    _json_dumps(payload),
                    finished_at,
                )
                await connection.execute(
                    """
                    UPDATE execution_receipts
                    SET outcome=$1, executor_state=$2, effect_state=$3,
                        verification_state=$4, termination_reason=$5,
                        finished_at=$6, content_json=$7::jsonb
                    WHERE receipt_id=$8 AND outcome IS NULL
                    """,
                    outcome.value,
                    executor_state.value,
                    effect_state.value,
                    verification_state.value,
                    termination_reason,
                    finished_at,
                    _json_dumps(requested_content),
                    receipt_id,
                )
                updated = await connection.fetchrow(
                    "SELECT * FROM execution_receipts WHERE receipt_id=$1", receipt_id
                )
                if updated is None:
                    raise RuntimeError("receipt disappeared after finalization")
                return await self._row_to_receipt(connection, updated)

    async def rebuild(self, receipt_id: str) -> Receipt | None:
        pool = await self._pool_or_initialize()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM execution_receipts WHERE receipt_id=$1", receipt_id
            )
            if row is None:
                return None
            request_data = dict(_json_value(row["request_json"]))
            rebuilt = Receipt(
                receipt_id=row["receipt_id"],
                request_id=request_data["request_id"],
                run_id=request_data.get("run_id"),
                tool_call_id=request_data.get("tool_call_id"),
                tool_name=request_data["tool_name"],
                executor_state=ExecutorState.NOT_STARTED,
                effect_state=EffectState.NOT_APPLICABLE,
                verification_state=VerificationState.NOT_REQUESTED,
            )
            events = await connection.fetch(
                "SELECT * FROM execution_events WHERE receipt_id=$1 ORDER BY sequence",
                receipt_id,
            )
            for event in events:
                payload = dict(_json_value(event["payload_json"]))
                if event["event_type"] == "execution.started":
                    rebuilt.executor_state = ExecutorState.RUNNING
                    rebuilt.started_at = event["created_at"]
                elif event["event_type"] == "execution.completed":
                    rebuilt.outcome = Outcome(payload["outcome"])
                    rebuilt.executor_state = ExecutorState(payload["executor_state"])
                    rebuilt.effect_state = EffectState(payload["effect_state"])
                    rebuilt.verification_state = VerificationState(payload["verification_state"])
                    rebuilt.termination_reason = payload.get("termination_reason")
                    rebuilt.finished_at = datetime.fromisoformat(payload["finished_at"])
                    rebuilt.content = payload.get("content") or {}
            rebuilt.observations = await self._observations_for(connection, receipt_id)
            return rebuilt

    async def _pool_or_initialize(self) -> asyncpg.Pool[Any]:
        if self._pool is None:
            await self.initialize()
        if self._pool is None:
            raise RuntimeError("receipt store failed to initialize")
        return self._pool

    @staticmethod
    async def _next_sequence(connection: Any, receipt_id: str) -> int:
        value = await connection.fetchval(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM execution_events WHERE receipt_id=$1",
            receipt_id,
        )
        return int(value)

    async def _row_to_receipt(self, connection: Any, row: Any) -> Receipt:
        return Receipt(
            receipt_id=row["receipt_id"],
            request_id=row["request_id"],
            run_id=row["run_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            outcome=Outcome(row["outcome"]) if row["outcome"] is not None else None,
            executor_state=ExecutorState(row["executor_state"]),
            effect_state=EffectState(row["effect_state"]),
            verification_state=VerificationState(row["verification_state"]),
            termination_reason=row["termination_reason"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            content=dict(_json_value(row["content_json"])),
            observations=await self._observations_for(connection, row["receipt_id"]),
            artifact_ids=list(_json_value(row["artifact_ids_json"])),
        )

    @staticmethod
    def _event_from_row(row: Any) -> ExecutionEvent:
        return ExecutionEvent(
            event_id=row["event_id"],
            receipt_id=row["receipt_id"],
            request_id=row["request_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            payload=dict(_json_value(row["payload_json"])),
            created_at=row["created_at"],
        )

    @staticmethod
    async def _observations_for(connection: Any, receipt_id: str) -> list[Observation]:
        rows = await connection.fetch(
            """
            SELECT observation_id, kind, source, authoritative, correlation_id,
                   observed_at, payload_json
            FROM execution_observations
            WHERE receipt_id=$1 ORDER BY observed_at, observation_id
            """,
            receipt_id,
        )
        return [
            Observation(
                observation_id=row["observation_id"],
                kind=row["kind"],
                source=row["source"],
                authoritative=bool(row["authoritative"]),
                correlation_id=row["correlation_id"],
                observed_at=row["observed_at"],
                payload=dict(_json_value(row["payload_json"])),
            )
            for row in rows
        ]



def _json_value(value: Any) -> Any:
    """Decode asyncpg JSONB values across default and codec-configured pools."""
    return json.loads(value) if isinstance(value, str) else value


# Keep the protocol relationship visible to type checkers and adapter readers.
_RECEIPT_STORE_TYPE: type[ReceiptStore] = PostgresReceiptStore
