"""Email REST endpoints for Flutter browse mode.

GET  /emails              — list emails
GET  /emails/:id           — single email
GET  /emails/search?q=...  — hybrid search
POST /emails/sync          — trigger sync from Gmail/Outlook
"""

import asyncio
from typing import Any

from fastapi import APIRouter

from src.storage.email_db import (
    count_emails,
    get_email,
    list_emails,
    mark_read,
    search_emails,
)
from src.storage.gmail_cache import sync_emails
from src.storage.gmail_client import GmailClient, GmailNotConnectedError

router = APIRouter(prefix="/emails", tags=["emails"])

_SYNC_TASKS: dict[str, asyncio.Task] = {}


@router.get("")
async def handle_list(
    user_id: str = "default_user",
    limit: int = 50,
    offset: int = 0,
    is_read: bool | None = None,
) -> dict[str, Any]:
    emails = list_emails(user_id, limit=limit, offset=offset, is_read=is_read)
    total = count_emails(user_id)
    unread = count_emails(user_id, is_read=False)
    return {
        "emails": emails,
        "total": total,
        "unread": unread,
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
async def handle_search(q: str, user_id: str = "default_user", limit: int = 20) -> dict[str, Any]:
    emails = search_emails(user_id, q, limit=limit)
    return {"emails": emails, "query": q}


@router.get("/{email_id}")
async def handle_get(email_id: str, user_id: str = "default_user") -> dict[str, Any]:
    email = get_email(user_id, email_id)
    if not email:
        return {"error": "not_found", "email_id": email_id}
    # Mark as read on open
    mark_read(user_id, email_id)
    return email


@router.post("/sync")
async def handle_sync(user_id: str = "default_user", provider: str = "gmail") -> dict[str, Any]:
    """Trigger a manual email sync. Returns immediately, sync runs in background.

    Gmail sync now runs through GmailClient (ConnectKit OAuth token) instead of
    the gws CLI subprocess (roadmap G3/G4). When the connector has no stored
    token the response is a frontend-friendly not-connected error (HTTP 200,
    body `{"error": "not_connected", ...}`) so the UI can prompt Sign-in.
    """

    if provider in ("gmail", "google"):
        try:
            client = GmailClient(user_id)
            if not client.is_connected():
                return {
                    "error": "not_connected",
                    "detail": "Gmail is not connected — Sign in with Google first.",
                    "provider": "gmail",
                }
        except GmailNotConnectedError as exc:
            return {"error": "not_connected", "detail": str(exc), "provider": "gmail"}

        # Background sync via the G3 sync facade (GmailClient + HybridDB).
        # Hold a module-level reference so the task is never GC'd mid-flight
        # (asyncio "Task was destroyed" warning); set is bounded to 1 per user.
        task = asyncio.create_task(asyncio.to_thread(sync_emails, user_id))
        _SYNC_TASKS[user_id] = task
    elif provider in ("outlook", "m365"):
        from src.config.settings import get_settings

        settings = get_settings()
        if not settings.email.m365_client_id:
            return {"error": "m365_client_id not configured"}
        asyncio.create_task(_sync_outlook(user_id, settings))

    return {"status": "sync_started", "provider": provider}


async def _sync_outlook(user_id: str, settings: Any) -> None:
    """Background Outlook sync via m365 CLI."""
    # Deferred: requires m365 CLI setup. Same pattern as gmail's GmailClient.
    pass
