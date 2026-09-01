"""Billing endpoints (Phase 2 M3-1): tenant plan/budget read + plan switch,
and the tenant monthly-budget 402 enforcement used by the message surfaces."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.http.auth import enforce_user_id, resolve_user_id
from src.storage.metering import get_metering_store
from src.storage.paths import DEFAULT_USER_ID
from src.storage.tenant import TenantError, get_tenant_store

router = APIRouter(prefix="/billing", tags=["billing"])


def month_start_iso() -> str:
    """ISO timestamp for the start of the current month (UTC)."""
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC).isoformat()


def tenant_mtd_cost(tenant: dict[str, object]) -> float:
    """Month-to-date tenant cost: sum over member stores. Each member's
    snapshot comes from their own store only — no cross-user reads (M1.3
    isolation contract)."""
    members = get_tenant_store().members(str(tenant["id"]))
    since = month_start_iso()
    return round(
        sum(get_metering_store(m).cost_since(since) for m in members), 6
    )


def tenant_budget_block(user_id: str) -> dict[str, object] | None:
    """Return a 402 billing payload when the user's tenant has exceeded its
    monthly budget, else None (no tenant / no budget / under budget)."""
    tenant = get_tenant_store().tenant_for_user(user_id)
    if not tenant:
        return None
    budget = tenant.get("monthly_budget_usd")
    if budget is None:
        return None
    budget = float(str(budget))
    mtd = float(tenant_mtd_cost(tenant))
    if mtd < float(budget):
        return None
    return {
        "code": "billing",
        "message": (
            f"Tenant '{tenant.get('name')}' exceeded its monthly budget: "
            f"${mtd:.2f} month-to-date against a ${float(budget):.2f} cap "
            f"(plan: {tenant.get('plan')})."
        ),
        "details": {
            "tenant_id": tenant["id"],
            "plan": tenant["plan"],
            "budget_usd": float(budget),
            "mtd_cost_usd": mtd,
        },
    }


class PlanSwitchRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan: str = Field(min_length=1)


def _is_admin(request: Request) -> bool:
    """Admin gate for plan changes: per-user-key deployments require an
    admin-scope key; trusted-network (flag off) deployments are admin by
    definition (the API_KEY holder is the operator)."""
    from src.config import get_settings

    if not get_settings().auth.per_user_auth:
        return True
    identity = getattr(getattr(request, "state", None), "identity", None)
    if identity is None or identity.trust_domain != "untrusted":
        return False
    scopes = getattr(identity, "scopes", ()) or ()
    return "admin" in scopes


@router.get("/tenant")
async def get_billing_tenant(
    request: Request,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, object]:
    resolved = resolve_user_id(request, user_id)
    enforce_user_id(resolved, getattr(getattr(request, "state", None), "identity", None))
    tenant = get_tenant_store().tenant_for_user(resolved)
    if tenant is None:
        return {
            "tenant_id": None,
            "plan": None,
            "seat_count": 0,
            "monthly_budget_usd": None,
            "mtd_cost_usd": 0.0,
        }
    return {
        "tenant_id": tenant["id"],
        "name": tenant["name"],
        "plan": tenant["plan"],
        "seat_count": tenant["seat_count"],
        "monthly_budget_usd": tenant["monthly_budget_usd"],
        "mtd_cost_usd": tenant_mtd_cost(tenant),
    }


@router.post("/plan")
async def switch_plan(
    req: PlanSwitchRequest, request: Request
) -> dict[str, object] | JSONResponse:
    if not _is_admin(request):
        return JSONResponse(
            status_code=403,
            content={
                "code": "forbidden",
                "message": "plan changes require an admin identity",
            },
        )
    try:
        get_tenant_store().set_plan(req.tenant_id, req.plan)
    except TenantError as e:
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    tenant = get_tenant_store().get_tenant(req.tenant_id)
    return {"tenant_id": req.tenant_id, "plan": req.plan, "persisted": tenant is not None and tenant["plan"] == req.plan}
