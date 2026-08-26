# mypy: disable-error-code="assignment"
"""Improvement and run-outcome API endpoints for loop 4."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from src.http.auth import enforce_user_id
from src.sdk.loops.storage import LoopEngineeringDB, get_loop_engineering_db_path

router = APIRouter(tags=["improvements"])


class SuggestionResponse(BaseModel):
    suggestion_id: str
    run_id: str
    target_type: str
    target_name: str
    current_value: str
    proposed_value: str
    rationale: str
    risk_level: str
    status: str


class OutcomeResponse(BaseModel):
    run_id: str
    trigger_type: str
    response: str
    verification_status: str | None
    verification_iterations: int
    model: str
    timestamp: str


@router.get("/improvements")
async def list_improvements(
    user_id: str = Query("default_user"),
    status: str | None = Query(None),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
    await db.init()
    suggestions = await db.list_suggestions(status=status)
    return {"suggestions": [s.__dict__ for s in suggestions]}


@router.post("/improvements/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: str,
    user_id: str = Query("default_user"),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
    await db.init()
    import time
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    success = await db.update_suggestion_status(suggestion_id, "approved", now)
    return {"status": "approved" if success else "not_found"}


@router.post("/improvements/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: str,
    user_id: str = Query("default_user"),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
    await db.init()
    success = await db.update_suggestion_status(suggestion_id, "rejected")
    return {"status": "rejected" if success else "not_found"}


@router.post("/improvements/analyze")
async def analyze_outcomes(
    user_id: str = Query("default_user"),
    request: Request = None,
) -> dict[str, Any]:
    """Trigger analysis job manually to propose improvements."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.config import get_settings
    from src.sdk.loops.improvement import AnalysisJob
    from src.sdk.providers.factory import get_cached_model_provider

    settings = get_settings()
    db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
    await db.init()

    analysis_model = settings.hill_climbing.analysis_model or settings.agent.model
    provider = get_cached_model_provider(analysis_model, user_id=user_id)

    job = AnalysisJob(
        analysis_provider=provider,
        mode=settings.hill_climbing.mode,
        auto_apply_risk_threshold=settings.hill_climbing.auto_apply_risk_threshold,
    )

    suggestions = await job.run(user_id, outcome_store=db, suggestion_store=db)
    return {"suggestions": [s.__dict__ for s in suggestions]}


@router.get("/run-outcomes")
async def list_run_outcomes(
    user_id: str = Query("default_user"),
    limit: int = Query(50),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
    await db.init()
    outcomes = await db.list_run_outcomes(user_id, limit=limit)
    return {"outcomes": [o.__dict__ for o in outcomes]}
