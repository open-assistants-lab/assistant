"""Audit export endpoint (roadmap P0-T4).

GET /audit returns a user's audit events as NDJSON (one event per line),
read from the per-user AuditStore that the CaptureBus writes to (P0-T3).
Read-only: this router never records, only exports.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.http.auth import enforce_user_id
from src.sdk.audit import ensure_audit_store_subscribed
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def get_audit_export(
    request: Request,
    user_id: str =  DEFAULT_USER_ID,
    since: str | None = None,
) -> StreamingResponse:
    """Export one user's audit trail as NDJSON.

    - `since` (ISO-8601, optional) filters events at-or-after that timestamp.
    - Read-only against the store the loop writes to; write-only from the
      loop, read-only via this API (P0-T3 contract).
    - P0-T2 enforcement: a resolved per-user identity (Phase 2) must match
      the requested user_id; solo/shared-secret (user_id=None) is exempt.
    """
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))

    since_ts: datetime | None = None
    if since is not None:
        try:
            since_ts = datetime.fromisoformat(since)
        except ValueError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"invalid since: {exc}") from exc

    store = ensure_audit_store_subscribed(user_id)
    events = await asyncio.to_thread(store.export, user_id=user_id, since=since_ts)

    def _lines() -> Any:
        for event in events:
            yield json.dumps(event.model_dump(), default=str) + "\n"

    return StreamingResponse(
        _lines(),
        media_type="application/x-ndjson",
        headers={"X-Audit-Count": str(len(events))},
    )
