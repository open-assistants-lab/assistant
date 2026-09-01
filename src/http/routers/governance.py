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


@router.post("/pendings/{proposal_id}/approve")
async def approve_pending(
    proposal_id: str, request: Request, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    resolved_user = resolve_user_id(request, user_id)
    enforce_user_id(resolved_user, getattr(getattr(request, "state", None), "identity", None))
    user_id = resolved_user
    svc = _svc(user_id)
    row = svc.get_pending(user_id, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    # Execution runs ONLY on the pending->approved transition made by THIS
    # call — replays (already approved/executed) are no-ops (M4-1 review).
    if row["status"] == "pending":
        if not svc.approve(user_id, proposal_id):
            raise HTTPException(status_code=409, detail="Approve race lost")
        exec_row = await execute_approved_tool(
            user_id, proposal_id, row["tool"], row["arguments"]
        )
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
