"""Owner dashboard summary (Phase 2 D1-1/D1-2 data layer).

GET /v1/dashboard/summary — read-only owner cards over the telemetry
sidecar: drafts produced, hours saved, cost/seat, top models by spend.
Opt-out is the shipped stance (telemetry disabled -> opt-out state, no data).
"""

from typing import Any

from fastapi import APIRouter, Query, Request

from src.app_logging import get_logger
from src.http.auth import resolve_user_id
from src.sdk.telemetry import telemetry_enabled
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(tags=["dashboard"])

logger = get_logger()

# Documented derivation (D1-1): one automated LLM call replaces a 5-minute
# manual task on average. Real turn durations override when flushed.
MANUAL_MINUTES_PER_CALL = 5.0


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window_days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Owner dashboard summary: drafts, hours_saved, cost/seat, top models."""
    user_id = resolve_user_id(request, user_id)
    if not telemetry_enabled():
        return {"enabled": False, "opted_out": True}

    from src.storage.analytics import get_analytics_store
    from src.storage.metering import get_metering_store

    summary = get_analytics_store(user_id).summary(user_id, window_days)
    snapshot = get_metering_store(user_id).snapshot(window_days=window_days)
    llm_calls = int(str(summary.get("llm_calls", 0)))
    # hours_saved: real turn durations when flushed; documented 5-min/call
    # baseline otherwise (both are derivations, stated as such).
    avg_turn_s = float(str(summary.get("avg_turn_seconds", 0.0)))
    hours_saved = (
        (llm_calls * avg_turn_s) / 3600.0
        if avg_turn_s > 0
        else (llm_calls * MANUAL_MINUTES_PER_CALL) / 60.0
    )
    by_model = snapshot.by_model or {}
    top_models = sorted(
        by_model.items(),
        key=lambda kv: float(kv[1].get("cost_usd", 0) or 0),
        reverse=True,
    )[:5]
    return {
        "user_id": user_id,
        "window_days": window_days,
        "enabled": True,
        "drafts": summary["drafts"],
        "hours_saved": round(hours_saved, 4),
        "cost_per_seat": snapshot.cost_usd,
        "top_models": [
            {"model": name, "cost_usd": stats.get("cost_usd", 0.0)}
            for name, stats in top_models
        ],
    }
