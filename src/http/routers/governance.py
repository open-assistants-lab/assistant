"""Governance pendings: list / approve (deterministic execution) / cancel.

M4-1 review P0: the approval EXECUTION leg. Approve transitions the pending
and executes the approved tool exactly once via the registry; show_then_auto_send
proposals whose window has elapsed auto-approve + execute at READ time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.http.auth import enforce_user_id, resolve_user_id
from src.sdk.governance import get_governance_service
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/governance", tags=["governance"])


async def execute_approved_tool(
    user_id: str, proposal_id: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic execution of an approved pending via the native tool
    registry (indirection point so tests can stub execution)."""

    return await get_governance_service(user_id).execute_approved(
        user_id, proposal_id
    )


def _svc(user_id: str):

    return get_governance_service(user_id)


@router.get("/pendings")
async def list_pendings(request: Request, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    """List pendings; lazily resolve expired show_then_auto_send windows
    (auto-approve + execute — the window must actually elapse)."""
    resolved_user = resolve_user_id(request, user_id)
    enforce_user_id(resolved_user, getattr(getattr(request, "state", None), "identity", None))
    user_id = resolved_user
    svc = _svc(user_id)
    out: list[dict[str, Any]] = []
    for pid in svc.list_pending_ids(user_id):
        row = svc.resolve_pending(user_id, pid)  # lazy expiry at read time
        if row is None or row["status"] == "missing":
            continue
        if row["tier"] == "show_then_auto_send" and row["status"] == "approved":
            exec_row = await execute_approved_tool(
                user_id, pid, row["tool"], row["arguments"]
            )
            row = {**row, "status": "executed", "execution": exec_row}
        out.append(row)
    return out


@router.get("/tool-stats")
async def tool_stats(request: Request, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    """M4-2 anti-fatigue: per-tool proposals/overrides/approvals + override_rate."""
    resolved_user = resolve_user_id(request, user_id)
    enforce_user_id(resolved_user, getattr(getattr(request, "state", None), "identity", None))
    return _svc(resolved_user).tool_stats(resolved_user)


def requester_is_second_party_authorized(requester, target_user_id: str) -> bool:
    """T3.2: a SECOND party may approve another user's pending when it is the
    deployment admin (trusted-network identity) or an org admin+ member.
    Plain per-user identities may not act on other users' pendings."""
    if requester is None or requester.user_id in (None, target_user_id):
        return False
    from src.config import get_settings

    if not get_settings().auth.per_user_auth:
        return True
    if requester.trust_domain in ("trusted-network", "solo"):
        return True
    if "admin" in (getattr(requester, "scopes", ()) or ()):
        return True
    try:
        from src.storage.tenancy import get_tenancy_store

        return get_tenancy_store().at_least(requester.user_id, "admin")
    except Exception:
        return False


@router.post("/pendings/{proposal_id}/approve")
async def approve_pending(
    proposal_id: str, request: Request, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    requester = getattr(getattr(request, "state", None), "identity", None)
    # Second-party approve: an org admin may approve ANOTHER user's pending,
    # targeting that user's store (user_id param = pending owner). Enforce
    # self-match only for the self path.
    requester_is_other = (
        requester is not None
        and requester.user_id is not None
        and requester.user_id != user_id
    )
    if not requester_is_second_party_authorized(requester, user_id) and (
        requester is None or requester.user_id in (None, user_id)
    ):
        resolved_user = resolve_user_id(request, user_id)
        enforce_user_id(
            resolved_user, getattr(getattr(request, "state", None), "identity", None)
        )
        user_id = resolved_user
    svc = _svc(user_id)
    row = svc.get_pending(user_id, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    # T3.2 / M4 trust model (the tracked self-approval P1): a pending created
    # by user X cannot be approved by user X. A second party must approve:
    # another member with admin+ role in the same org tree, or the deployment
    # admin (trusted-network identity). Staff with no second party see their
    # pendings waiting. Idempotency and receipts unchanged.
    if not requester_is_second_party_authorized(requester, user_id):
        if requester is not None and requester.trust_domain == "untrusted":
            raise HTTPException(
                status_code=403,
                detail=(
                    "Self-approval is not permitted: a governance pending must "
                    "be approved by a second party (an org admin/owner or the "
                    "deployment admin), not the user who created it."
                ),
            )
    # Execution runs ONLY on the pending->approved transition made by THIS
    # call — replays (already approved/executed) are no-ops (M4-1 review).
    if row["status"] in ("pending", "approved"):
        if row["status"] == "pending" and not svc.approve(user_id, proposal_id):
            raise HTTPException(status_code=409, detail="Approve race lost")
        # M4-1 upgrade (session-log payoff): replay-resume when the session
        # log has the run; deterministic fallback otherwise. Exactly-once is
        # enforced inside both paths (approved->executed conditional UPDATE).
        exec_row = await svc.replay_resume(
            user_id, proposal_id,
            registry=None,
            executor=lambda uid, pid, registry=None: execute_approved_tool(
                uid, pid, (svc.get_pending(uid, pid) or {}).get("tool", ""),
                (svc.get_pending(uid, pid) or {}).get("arguments") or {},
            ),
        )
        if exec_row.get("status") == "missing":
            raise HTTPException(status_code=404, detail="No such proposal")
    else:
        exec_row = {"status": row["status"], "already": True}
    final = svc.get_pending(user_id, proposal_id)
    return {
        "proposal_id": proposal_id,
        "status": (final or {}).get("status", row["status"]),
        "execution": exec_row,
    }


@router.post("/pendings/{proposal_id}/cancel")
async def cancel_pending(
    proposal_id: str, request: Request, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    resolved_user = resolve_user_id(request, user_id)
    enforce_user_id(resolved_user, getattr(getattr(request, "state", None), "identity", None))
    user_id = resolved_user
    svc = _svc(user_id)
    row = svc.get_pending(user_id, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such pending")
    svc.cancel(user_id, proposal_id)
    return {"proposal_id": proposal_id, "status": "cancelled"}
