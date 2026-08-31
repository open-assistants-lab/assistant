"""Usage / billing query API (Phase 2 M1.2).

Read-only endpoints over the per-user MeteringStore that the CaptureBus
metering sink writes to (M1.1). Metering is OFF by default (OSS) — endpoints
return zeros/empty when no data exists, never errors.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from src.app_logging import get_logger
from src.http.auth import enforce_user_id
from src.storage.metering import get_metering_store
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(tags=["usage"])

logger = get_logger()


def _window_days(window: str | None) -> int:
    """Parse a window like '24h', '7d', '30d' (default 30d)."""
    if not window:
        return 30
    w = window.strip().lower()
    try:
        if w.endswith("h"):
            return max(1, int(round(int(w[:-1]) / 24)))
        if w.endswith("d"):
            return max(1, int(w[:-1]))
        return max(1, int(w))
    except ValueError:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"invalid window: {window!r}") from None


def _store_for(user_id: str, window: str | None) -> tuple[Any, int]:
    store = get_metering_store(user_id)
    return store, _window_days(window)


@router.get("/usage/summary")
async def get_usage_summary(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window: str | None = Query(default=None, description="e.g. '24h', '7d', '30d'"),
) -> dict[str, Any]:
    """Tokens + cost per (day, model) for the window."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    store, days = _store_for(user_id, window)
    rows = await _to_thread_summary(store, days)
    logger.info(
        "usage.summary",
        {"window": window or "30d", "rows": len(rows)},
        user_id=user_id,
    )
    return {
        "user_id": user_id,
        "window_days": days,
        "summary": [r.model_dump(mode="json") for r in rows],
    }


async def _to_thread_summary(store: Any, days: int) -> Any:
    import asyncio

    return await asyncio.to_thread(store.summary, days)


@router.get("/usage/events")
async def get_usage_events(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated usage events, newest first."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    store = get_metering_store(user_id)
    import asyncio

    rows = await asyncio.to_thread(store.events, limit, offset)
    return {
        "user_id": user_id,
        "limit": limit,
        "offset": offset,
        "events": [r.model_dump(mode="json") for r in rows],
        "count": len(rows),
    }


@router.get("/billing/cost")
async def get_billing_cost(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window: str | None = Query(default=None, description="e.g. '24h', '7d', '30d'"),
) -> dict[str, Any]:
    """Total metered cost (USD) for the window."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    store, days = _store_for(user_id, window)
    import asyncio

    total = await asyncio.to_thread(store.total_cost, days)
    return {
        "user_id": user_id,
        "window_days": days,
        "cost_usd": round(total, 6),
        "currency": "USD",
        "as_of": datetime.now(UTC).isoformat(),
    }
