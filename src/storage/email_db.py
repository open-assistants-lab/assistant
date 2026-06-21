"""Email store backed by HybridDB (SQLite + FTS5 + ChromaDB).

Replaces the old SQLite-only email_db.py. Uses per-user storage
at data/users/{user_id}/email/app.db.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger

logger = get_logger()


def _get_db(user_id: str) -> Any:
    """Get or create a HybridDB instance for the user's email store."""
    from hybriddb import HybridDB

    from src.storage.paths import get_paths

    paths = get_paths(user_id)
    path = paths.email_dir()
    db = HybridDB(path=str(path))

    try:
        db.create_table(
            "emails",
            {
                "id": "TEXT PRIMARY KEY",
                "account": "TEXT",
                "provider": "TEXT",
                "from_addr": "TEXT",
                "to_addr": "TEXT",
                "subject": "TEXT",
                "body": "LONGTEXT",
                "snippet": "TEXT",
                "received_at": "TEXT",
                "is_read": "INTEGER DEFAULT 0",
                "labels": "TEXT",
                "thread_id": "TEXT",
            },
        )
    except Exception:
        logger.warning("email_db.create_table_error", {"user_id": user_id})

    return db


def store_emails(user_id: str, emails: list[dict[str, Any]], account: str = "default") -> int:
    """Store a batch of emails. Returns count stored."""
    db = _get_db(user_id)
    count = 0
    for email in emails:
        eid = email.get("id", "")
        if not eid:
            continue
        row = {
            "id": eid,
            "account": account,
            "provider": email.get("provider", "gmail"),
            "from_addr": email.get("from", ""),
            "to_addr": email.get("to", ""),
            "subject": email.get("subject", ""),
            "body": email.get("body", ""),
            "snippet": email.get("snippet", ""),
            "received_at": email.get("received_at", datetime.now(UTC).isoformat()),
            "is_read": 1 if email.get("is_read") else 0,
            "labels": ",".join(email.get("labels", [])),
            "thread_id": email.get("thread_id", ""),
        }
        try:
            db.insert("emails", row)
            count += 1
        except Exception:
            logger.warning("email_db.insert_error", {"user_id": user_id, "email_id": eid})
    return count


def list_emails(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
    account: str | None = None,
    is_read: bool | None = None,
) -> list[dict[str, Any]]:
    """List emails with optional filters."""
    db = _get_db(user_id)
    try:
        rows = db.search(
            table="emails",
            column="subject",
            query="",
            limit=limit,
            order_by="received_at DESC",
        )
    except Exception:
        return []

    results = []
    for i, row in enumerate(rows):
        if i < offset:
            continue
        if account and row.get("account", "") != account:
            continue
        if is_read is not None and bool(row.get("is_read")) != is_read:
            continue
        results.append(_row_to_email(row))
        if len(results) >= limit:
            break
    return results


def search_emails(user_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search emails by FTS5 keyword + ChromaDB semantic."""
    from hybriddb import SearchMode

    db = _get_db(user_id)
    try:
        rows = db.search(
            table="emails",
            column="body",
            query=query,
            mode=SearchMode.HYBRID,
            limit=limit * 3,
            fts_weight=0.7,
        )
    except Exception:
        return []

    seen_ids: set[str] = set()
    results = []
    for row in rows:
        eid = row.get("id", "")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        results.append(_row_to_email(row))
        if len(results) >= limit:
            break
    return results


def get_email(user_id: str, email_id: str) -> dict[str, Any] | None:
    """Get a single email by ID."""
    db = _get_db(user_id)
    row = db.get("emails", email_id)
    return _row_to_email(row) if row else None


def mark_read(user_id: str, email_id: str) -> None:
    """Mark an email as read."""
    db = _get_db(user_id)
    try:
        db.update("emails", email_id, {"is_read": 1})
    except Exception:
        logger.warning("email_db.mark_read_error", {"user_id": user_id, "email_id": email_id})


def count_emails(user_id: str, is_read: bool | None = None) -> int:
    """Count emails, optionally filtered by read status."""
    db = _get_db(user_id)
    try:
        if is_read is not None:
            rows = db.raw_query(
                "SELECT COUNT(*) as cnt FROM emails WHERE is_read = ?",
                (int(is_read),),
            )
        else:
            rows = db.raw_query("SELECT COUNT(*) as cnt FROM emails")
        return rows[0].get("cnt", 0) if rows else 0
    except Exception:
        return 0


def clear_emails(user_id: str) -> None:
    """Delete all emails for a user."""
    db = _get_db(user_id)
    try:
        db.raw_query("DELETE FROM emails")
    except Exception:
        logger.warning("email_db.clear_error", {"user_id": user_id})


def _row_to_email(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels", "")
    return {
        "id": row.get("id", ""),
        "account": row.get("account", ""),
        "provider": row.get("provider", ""),
        "from": row.get("from_addr", ""),
        "to": row.get("to_addr", ""),
        "subject": row.get("subject", ""),
        "body": row.get("body", ""),
        "snippet": row.get("snippet", ""),
        "received_at": row.get("received_at", ""),
        "is_read": bool(row.get("is_read")),
        "labels": [label.strip() for label in labels.split(",") if label.strip()],
        "thread_id": row.get("thread_id", ""),
    }
