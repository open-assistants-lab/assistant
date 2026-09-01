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
    avg_turn_s = float(str(summary.get("avg_turn_seconds", 0.0) or 0.0))
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


# ---------------------------------------------------------------------------
# D1-2: owner dashboard UI + CSV export + H7 compounding trend
# ---------------------------------------------------------------------------


@router.get("/dashboard/summary.csv")
async def get_dashboard_summary_csv(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window_days: int = Query(default=30, ge=1, le=365),
) -> Any:
    """CSV export of the daily dashboard rows (drafts, cost, durations)."""
    from fastapi.responses import Response

    user_id = resolve_user_id(request, user_id)
    if not telemetry_enabled():
        return Response(
            content="opted_out\n",
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=dashboard.csv"},
        )
    from src.storage.analytics import get_analytics_store

    rows = get_analytics_store(user_id).daily_rows(user_id, window_days)
    lines = ["day,drafts,cost_usd,avg_turn_seconds"]
    lines += [
        f"{r['day']},{r['drafts_count']},{r['cost_usd']:.4f},{r['avg_turn_seconds']:.2f}"
        for r in rows
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dashboard.csv"},
    )


@router.get("/dashboard/trend")
async def get_dashboard_trend(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window_days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """H7 compounding metric (simple v1): duration trend per recurring task.

    v1 derives ONE series over all tasks (label "all tasks") — per-task
    labels need task-prefix grouping that lands with the H7 refinement
    (review-gated, deferred per plan). first/latest are the window's first
    and latest day average turn durations; delta_pct is the change.
    """
    user_id = resolve_user_id(request, user_id)
    if not telemetry_enabled():
        return {"enabled": False, "opted_out": True, "trends": []}
    from src.storage.analytics import get_analytics_store

    rows = get_analytics_store(user_id).daily_rows(user_id, window_days)
    trends: list[dict[str, Any]] = []
    if rows:
        first = float(str(rows[0]["avg_turn_seconds"]))
        latest = float(str(rows[-1]["avg_turn_seconds"]))
        delta_pct = (
            round((latest - first) / first * 100.0, 1) if first > 0 else 0.0
        )
        trends.append(
            {
                "task_label": "all tasks",
                "first_duration_s": first,
                "latest_duration_s": latest,
                "delta_pct": delta_pct,
            }
        )
    return {"enabled": True, "window_days": window_days, "trends": trends}


def _render_dashboard_html(summary: dict[str, Any], trends: list[dict[str, Any]]) -> str:
    """Server-side light template render — no JS framework, no build step."""
    top_rows = "".join(
        f"<tr><td>{m['model']}</td><td>${m['cost_usd']:.4f}</td></tr>"
        for m in summary.get("top_models", [])
    ) or "<tr><td colspan='2'>no spend yet</td></tr>"
    trend_rows = "".join(
        f"<tr><td>{t['task_label']}</td>"
        f"<td>{t['first_duration_s']:.0f}s</td>"
        f"<td>{t['latest_duration_s']:.0f}s</td>"
        f"<td>{t['delta_pct']:+.1f}%</td></tr>"
        for t in trends
    ) or "<tr><td colspan='4'>no trend data yet</td></tr>"
    return f"""<!doctype html>
<html><head><title>Owner dashboard</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
.cards {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.card {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem 1.5rem; }}
.card .value {{ font-size: 2rem; font-weight: 600; }}
table {{ border-collapse: collapse; margin-top: 1rem; }}
td, th {{ border: 1px solid #ccc; padding: 0.35rem 0.75rem; }}
</style></head>
<body>
<h1>Owner dashboard <small style="font-size:0.9rem">({summary['window_days']}d)</small></h1>
<div class="cards">
  <div class="card"><div class="value">{summary['drafts']}</div>drafts produced</div>
  <div class="card"><div class="value">{summary['hours_saved']:.1f}h</div>hours saved</div>
  <div class="card"><div class="value">${summary['cost_per_seat']:.4f}</div>cost / seat</div>
  <div class="card"><div class="value">{len(summary.get('top_models', []))}</div>models in use</div>
</div>
<h2>Top models by spend</h2>
<table id="top-models">{top_rows}</table>
<h2>Duration trend (compounding)</h2>
<table id="trends">{trend_rows}</table>
<p><a id="csv-export" href="/v1/dashboard/summary.csv?window_days={summary['window_days']}">Export CSV</a></p>
</body></html>"""


@router.get("/dashboard")
async def get_dashboard_page(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
    window_days: int = Query(default=30, ge=1, le=365),
) -> Any:
    """Owner dashboard page — server-rendered cards, same identity as the
    data endpoints (PER_USER_AUTH respected; localhost-solo renders directly).
    """
    from fastapi.responses import HTMLResponse

    user_id = resolve_user_id(request, user_id)
    if not telemetry_enabled():
        return HTMLResponse(
            "<html><body><h1>Owner dashboard</h1>"
            "<p>Telemetry is opted out for this deployment.</p></body></html>"
        )
    from src.storage.analytics import get_analytics_store
    from src.storage.metering import get_metering_store

    summary = get_analytics_store(user_id).summary(user_id, window_days)
    snapshot = get_metering_store(user_id).snapshot(window_days=window_days)
    llm_calls = int(str(summary.get("llm_calls", 0) or 0))
    avg_turn_s = float(str(summary.get("avg_turn_seconds", 0.0) or 0.0))
    hours_saved = (
        (llm_calls * avg_turn_s) / 3600.0
        if avg_turn_s > 0
        else (llm_calls * MANUAL_MINUTES_PER_CALL) / 60.0
    )
    by_model = snapshot.by_model or {}
    top_models = sorted(
        by_model.items(),
        key=lambda kv: float(str(kv[1].get("cost_usd", 0) or 0)),
        reverse=True,
    )[:5]
    card_summary: dict[str, Any] = {
        "window_days": window_days,
        "drafts": summary["drafts"],
        "hours_saved": round(hours_saved, 4),
        "cost_per_seat": snapshot.cost_usd,
        "top_models": [
            {"model": name, "cost_usd": float(str(stats.get("cost_usd", 0) or 0))}
            for name, stats in top_models
        ],
    }
    trend = await get_dashboard_trend(request, user_id=user_id, window_days=window_days)
    return HTMLResponse(
        _render_dashboard_html(
            card_summary, list(trend.get("trends", []) if trend.get("enabled") else [])
        )
    )
