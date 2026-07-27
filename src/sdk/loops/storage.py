"""SQLite storage for RunOutcome and ImprovementSuggestion (loop 4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from src.app_logging import get_logger

logger = get_logger()


@dataclass
class RunOutcome:
    run_id: str
    user_id: str
    session_id: str
    trigger_type: str
    response: str
    verification_status: str | None = None
    verification_iterations: int = 0
    verification_evaluations: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    timestamp: str = ""
    traces: dict | None = None


@dataclass
class ImprovementSuggestion:
    suggestion_id: str
    run_id: str
    target_type: str
    target_name: str
    current_value: str
    proposed_value: str
    rationale: str
    risk_level: str
    status: str = "proposed"
    eval_result: dict | None = None
    created_at: str = ""
    applied_at: str | None = None


class LoopEngineeringDB:
    """SQLite storage for run outcomes and improvement suggestions."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS run_outcomes (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    trigger_type TEXT,
                    response TEXT,
                    verification_status TEXT,
                    verification_iterations INTEGER DEFAULT 0,
                    verification_evaluations TEXT,
                    cost_usd REAL DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    model TEXT,
                    timestamp TEXT NOT NULL,
                    traces TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS improvement_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    target_type TEXT,
                    target_name TEXT,
                    current_value TEXT,
                    proposed_value TEXT,
                    rationale TEXT,
                    risk_level TEXT,
                    status TEXT DEFAULT 'proposed',
                    eval_result TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
            """)
            await db.commit()

    async def save_run_outcome(self, outcome: RunOutcome) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO run_outcomes
                   (run_id, user_id, session_id, trigger_type, response,
                    verification_status, verification_iterations, verification_evaluations,
                    cost_usd, input_tokens, output_tokens, model, timestamp, traces)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    outcome.run_id, outcome.user_id, outcome.session_id,
                    outcome.trigger_type, outcome.response,
                    outcome.verification_status, outcome.verification_iterations,
                    json.dumps(outcome.verification_evaluations),
                    outcome.cost_usd, outcome.input_tokens, outcome.output_tokens,
                    outcome.model, outcome.timestamp,
                    json.dumps(outcome.traces) if outcome.traces else None,
                ),
            )
            await db.commit()

    async def list_run_outcomes(
        self, user_id: str, limit: int = 50, since: str | None = None
    ) -> list[RunOutcome]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if since:
                cursor = await db.execute(
                    "SELECT * FROM run_outcomes WHERE user_id = ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                    (user_id, since, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM run_outcomes WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (user_id, limit),
                )
            rows = await cursor.fetchall()
            return [self._row_to_outcome(row) for row in rows]

    def _row_to_outcome(self, row: aiosqlite.Row) -> RunOutcome:
        return RunOutcome(
            run_id=row["run_id"],
            user_id=row["user_id"],
            session_id=row["session_id"] or "",
            trigger_type=row["trigger_type"] or "manual",
            response=row["response"] or "",
            verification_status=row["verification_status"],
            verification_iterations=row["verification_iterations"] or 0,
            verification_evaluations=json.loads(row["verification_evaluations"] or "[]"),
            cost_usd=row["cost_usd"] or 0.0,
            input_tokens=row["input_tokens"] or 0,
            output_tokens=row["output_tokens"] or 0,
            model=row["model"] or "",
            timestamp=row["timestamp"],
            traces=json.loads(row["traces"]) if row["traces"] else None,
        )

    async def save_suggestion(self, suggestion: ImprovementSuggestion) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO improvement_suggestions
                   (suggestion_id, run_id, target_type, target_name,
                    current_value, proposed_value, rationale, risk_level,
                    status, eval_result, created_at, applied_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    suggestion.suggestion_id, suggestion.run_id,
                    suggestion.target_type, suggestion.target_name,
                    suggestion.current_value, suggestion.proposed_value,
                    suggestion.rationale, suggestion.risk_level,
                    suggestion.status,
                    json.dumps(suggestion.eval_result) if suggestion.eval_result else None,
                    suggestion.created_at, suggestion.applied_at,
                ),
            )
            await db.commit()

    async def list_suggestions(
        self, user_id: str | None = None, status: str | None = None
    ) -> list[ImprovementSuggestion]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT s.* FROM improvement_suggestions s"
            params: list[Any] = []
            conditions: list[str] = []
            if status:
                conditions.append("s.status = ?")
                params.append(status)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY s.created_at DESC"
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_suggestion(row) for row in rows]

    async def update_suggestion_status(
        self, suggestion_id: str, status: str, applied_at: str | None = None
    ) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE improvement_suggestions SET status = ?, applied_at = ? WHERE suggestion_id = ?",
                (status, applied_at, suggestion_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    def _row_to_suggestion(self, row: aiosqlite.Row) -> ImprovementSuggestion:
        return ImprovementSuggestion(
            suggestion_id=row["suggestion_id"],
            run_id=row["run_id"] or "",
            target_type=row["target_type"] or "",
            target_name=row["target_name"] or "",
            current_value=row["current_value"] or "",
            proposed_value=row["proposed_value"] or "",
            rationale=row["rationale"] or "",
            risk_level=row["risk_level"] or "low",
            status=row["status"] or "proposed",
            eval_result=json.loads(row["eval_result"]) if row["eval_result"] else None,
            created_at=row["created_at"] or "",
            applied_at=row["applied_at"],
        )


def get_loop_engineering_db_path(user_id: str) -> Path:
    from src.config import get_settings
    settings = get_settings()
    return Path(settings.data_path) / "users" / user_id / "loop_engineering.db"
