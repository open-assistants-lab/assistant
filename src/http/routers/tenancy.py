"""T3.1 API: org/sub-tenant CRUD under /v1/tenancy (admin-gated).

Auth patterns mirror billing.py: trusted-network (flag off) is admin by
definition; per-user-key deployments require an admin-scope key (see
`_is_admin` there — imported here as the single source).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.http.auth import enforce_user_id, resolve_user_id
from src.storage.paths import DEFAULT_USER_ID
from src.storage.tenancy import TenancyError, get_tenancy_store

router = APIRouter(prefix="/v1/tenancy", tags=["tenancy"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=1)


class SubTenantCreate(BaseModel):
    name: str = Field(min_length=1)


class MemberAdd(BaseModel):
    user_id: str = Field(min_length=1)


class MemberRole(BaseModel):
    """T3.2 review P2: role assignment request."""

    user_id: str = Field(default=DEFAULT_USER_ID)
    role: str = Field(default="staff")


class MemberMove(BaseModel):
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)


def _role_gate(request: Request, need: str) -> bool:
    """RBAC gate (T3.2): trusted-network deployments are owner-equivalent;
    per-user-key deployments check the membership role (admin+ for org
    CRUD, admin for member role changes)."""
    from src.config import get_settings

    if not get_settings().auth.per_user_auth:
        return True
    identity = getattr(getattr(request, "state", None), "identity", None)
    if identity is None:
        return False
    if identity.trust_domain == "trusted-network":
        return True
    # Deployment-level admin grant (admin-scope key) OR the org-tree role.
    if "admin" in (getattr(identity, "scopes", ()) or ()):
        return True
    role = getattr(identity, "role", "staff")
    rank = {"staff": 0, "admin": 1, "owner": 2}
    return rank.get(role, 0) >= rank.get(need, 0)


@router.post("/orgs")
async def create_org(req: OrgCreate, request: Request) -> dict[str, object]:
    """Create a top-level org. Admin-gated (trusted-network deployments are
    admin by definition; per-user-key deployments need an admin-scope key)."""
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    store = get_tenancy_store()
    # T3.2 review P2: API-created orgs must have an owner membership —
    # use the requesting admin's identity (trusted-network deployments use
    # the operator default user).
    owner_id = None
    identity = getattr(getattr(request, "state", None), "identity", None)
    if identity is not None and identity.user_id:
        owner_id = identity.user_id
    tenant_id = store.create_org(name=req.name, owner_id=owner_id)
    return {"tenant_id": tenant_id, "name": req.name, "kind": "org", "owner_id": owner_id}


@router.post("/{tenant_id}/sub-tenants")
async def create_sub_tenant(
    tenant_id: str, req: OrgCreate, request: Request
) -> dict[str, object]:
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    try:
        sub_id = get_tenancy_store().create_sub_tenant(tenant_id, name=req.name)
    except Exception as e:  # TenantError / TenancyError
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    return {"tenant_id": sub_id, "name": req.name, "kind": "sub_tenant"}


@router.post("/{tenant_id}/members")
async def add_member(
    tenant_id: str, req: MemberAdd, request: Request
) -> dict[str, object]:
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    try:
        get_tenancy_store().add_member(tenant_id, req.user_id)
    except Exception as e:
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    return {"tenant_id": tenant_id, "user_id": req.user_id, "added": True}


@router.post("/members/move")
async def move_member(req: MemberMove, request: Request) -> dict[str, object]:
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    try:
        get_tenancy_store().move_membership(req.user_id, req.tenant_id)
    except Exception as e:
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    return {"moved": True, "user_id": req.user_id, "tenant_id": req.tenant_id}


@router.post("/{tenant_id}/members/role", response_model=None)
async def set_member_role(
    tenant_id: str, req: MemberRole, request: Request
) -> dict[str, object] | JSONResponse:  # noqa: UP047
    """Set a member's role (T3.2 review P2: minimal role management). Admin+
    required; the store's owner-demotion guard is the enforcement point."""
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    store = get_tenancy_store()
    try:
        store.set_role(req.user_id, req.role)
    except TenancyError as e:
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    return {"user_id": req.user_id, "role": store.role_of(req.user_id)}


@router.get("/{tenant_id}/members")
async def list_members(tenant_id: str, request: Request) -> dict[str, object]:
    """Tenant-admin listing: tenant-scoped via membership (org tree)."""
    if not _role_gate(request, "admin"):
        return JSONResponse(
            status_code=403,
            content={"code": "forbidden", "message": "admin role required"},
        )
    store = get_tenancy_store()
    try:
        rows = store.member_rows(tenant_id)
    except Exception as e:
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": str(e)})
    return {"members": rows}


@router.get("/memberships")
async def user_membership(request: Request, user_id: str = DEFAULT_USER_ID) -> dict[str, object]:
    """The requesting (or requested) user's membership resolved to its org —
    mapping only; never a store path."""
    resolved = resolve_user_id(request, user_id)
    enforce_user_id(resolved, getattr(getattr(request, "state", None), "identity", None))
    row = get_tenancy_store().resolve_membership(resolved)
    if row is None:
        return JSONResponse(
            status_code=404,
            content={"code": "not_found", "message": "no membership for user"},
        )
    return row
