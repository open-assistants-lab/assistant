"""Email sync - backfill and interval sync with rate limit handling."""

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import text

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.tools import tool
from src.sdk.tools_core.email_db import get_engine as _get_engine
from src.sdk.tools_core.email_db import parse_email_flags

logger = get_logger()

RATE_LIMIT_COOLDOWN: dict[str, float] = {}




def _resolve_watermark(account: dict[str, Any]) -> int:
    """Quick-sync watermark: SQL NULL / missing means "never synced" → 0.

    ``account.get("last_timestamp", 0)`` returns None — not the default —
    when the nullable accounts.last_timestamp column is NULL, and
    ``None > 0`` raises TypeError, breaking every quick sync for such
    accounts.
    """
    return account.get("last_timestamp") or 0


def _clamp_watermark(newest: int, now_ts: int) -> int:
    """Never advance the watermark past now: a future Date header would
    otherwise poison every subsequent sync (date__gt would skip all mail
    older than the bogus future timestamp)."""
    return min(newest, now_ts)

def _load_accounts(user_id: str) -> dict[str, Any]:
    """Load accounts from database."""
    engine = _get_engine(user_id)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM accounts"))
        accounts = {}
        for row in result:
            row_dict = dict(row._mapping)
            accounts[row_dict["id"]] = {
                "id": row_dict["id"],
                "name": row_dict["name"],
                "email": row_dict.get("email", ""),
                "password": row_dict.get("password", ""),
                "imap_host": row_dict.get("imap_host", ""),
                "imap_port": row_dict.get("imap_port", 993),
                "smtp_host": row_dict.get("smtp_host", ""),
                "smtp_port": row_dict.get("smtp_port", 587),
                "provider": row_dict.get("provider", ""),
                "folders": row_dict.get("folders", "").split(",")
                if row_dict.get("folders")
                else [],
                "last_sync": row_dict.get("last_sync"),
                "last_timestamp": row_dict.get("last_timestamp"),
                "status": row_dict.get("status", "connected"),
                "created_at": row_dict.get("created_at"),
            }
        return accounts


def _save_account(user_id: str, account_id: str, account: dict[str, Any]) -> None:
    """Save account to database."""
    engine = _get_engine(user_id)

    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT OR REPLACE INTO accounts
                (id, name, email, password, imap_host, imap_port, smtp_host, smtp_port, provider, folders, last_sync, last_timestamp, status, created_at)
                VALUES (:id, :name, :email, :password, :imap_host, :imap_port, :smtp_host, :smtp_port, :provider, :folders, :last_sync, :last_timestamp, :status, :created_at)
            """),
            {
                "id": account_id,
                "name": account.get("name", ""),
                "email": account.get("email", ""),
                "password": account.get("password", ""),
                "imap_host": account.get("imap_host", ""),
                "imap_port": account.get("imap_port", 993),
                "smtp_host": account.get("smtp_host", ""),
                "smtp_port": account.get("smtp_port", 587),
                "provider": account.get("provider", ""),
                "folders": ",".join(account.get("folders", [])),
                "last_sync": account.get("last_sync"),
                "last_timestamp": account.get("last_timestamp"),
                "status": account.get("status", "connected"),
                "created_at": account.get("created_at", int(datetime.now(UTC).timestamp())),
            },
        )
        conn.commit()


def _parse_email_date(date_str: str) -> int:
    """Parse email date string to Unix timestamp."""
    if not date_str:
        return int(datetime.now(UTC).timestamp())
    try:
        dt = parsedate_to_datetime(date_str)
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(UTC).timestamp())


def _email_to_dict(msg: Any) -> dict[str, Any]:
    """Convert imap_tools message to dict."""
    attachments = []
    if msg.attachments:
        for att in msg.attachments:
            attachments.append(
                {
                    "filename": att.filename,
                    "size": len(att.payload) if att.payload else 0,
                    "content_type": att.content_type,
                }
            )

    date_ts = (
        _parse_email_date(msg.date_str) if msg.date_str else int(datetime.now(UTC).timestamp())
    )

    return {
        "message_id": str(msg.uid),
        "from_addr": msg.from_,
        "from_name": msg.from_values.name if msg.from_values else None,
        "to_addrs": [str(a) for a in msg.to],
        "cc_addrs": [str(a) for a in msg.cc],
        "subject": msg.subject or "",
        "body_text": msg.text or "",
        "timestamp": date_ts,
        "in_reply_to": msg.headers.get("In-Reply-To", [None])[0] if msg.headers else None,
        "thread_references": msg.headers.get("References", [None])[0] if msg.headers else None,
        "is_forwarded": bool(
            msg.headers.get("X-FWD", [None])[0] or msg.headers.get("Forwarded", [None])[0]
        )
        if msg.headers
        else False,
        **parse_email_flags(msg),
        "has_attachments": bool(attachments),
        "attachments": attachments,
    }


def _get_imap_connection(account_id: str, user_id: str) -> Any:
    """Get IMAP connection for account."""
    accounts = _load_accounts(user_id)
    account = accounts.get(account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    email = account.get("email")
    password = account.get("password")
    imap_host = account.get("imap_host")
    imap_port = account.get("imap_port", 993)

    if not email or not password or not imap_host:
        raise ValueError(f"Account {account_id} is missing credentials")

    from imap_tools import MailBox

    return MailBox(imap_host, imap_port).login(email, password)


def _email_row_params(email_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten an _email_to_dict payload into bound INSERT parameters."""
    return {
        **email_data,
        "to_addrs": ",".join(email_data["to_addrs"]),
        "cc_addrs": ",".join(email_data["cc_addrs"]),
        "attachments": str(email_data["attachments"]),
        "read": 1 if email_data["read"] else 0,
        "flagged": 1 if email_data["flagged"] else 0,
        "has_attachments": 1 if email_data["has_attachments"] else 0,
        "is_forwarded": 1 if email_data.get("is_forwarded") else 0,
        "tags": "",
    }


_INSERT_EMAIL_SQL = text("""
    INSERT OR REPLACE INTO emails
    (account_id, folder, message_id, from_addr, from_name,
     to_addrs, cc_addrs, subject, body_text, timestamp,
     in_reply_to, thread_references, is_forwarded,
     read, flagged, has_attachments, attachments, tags, created_at)
    VALUES
    (:account_id, :folder, :message_id, :from_addr, :from_name,
     :to_addrs, :cc_addrs, :subject, :body_text, :timestamp,
     :in_reply_to, :thread_references, :is_forwarded,
     :read, :flagged, :has_attachments, :attachments, :tags, :created_at)
""")


def _insert_email_rows(engine: Any, rows: list[dict[str, Any]]) -> None:
    """Insert email rows in ONE transaction (audit P1): the old code opened a
    connection + fsync commit PER EMAIL, so a 500-message backfill paid 500
    connects + 500 commits."""
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(_INSERT_EMAIL_SQL, rows)


def _sync_folder(
    account_id: str,
    folder: str,
    mode: str = "new",
    limit: int = 100,
    user_id: str = "",
) -> int:
    """Sync emails from a folder.

    Modes:
        - "new": Fetch emails newer than last_timestamp (quick sync)
        - "full": Fetch all emails (backfill, newest -> earliest)

    Args:
        account_id: Account ID
        folder: Folder name
        mode: "new" or "full"
        limit: Max emails to fetch
        user_id: User ID

    Returns:
        Number of emails synced
    """
    engine = _get_engine(user_id)
    accounts = _load_accounts(user_id)
    account = accounts.get(account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    with _get_imap_connection(account_id, user_id) as mailbox:
        mailbox.folder.set(folder)

        if mode == "full":
            # Full backfill - fetch all in batches (newest first), paginating
            # by UID range so each batch fetches older emails than the last
            from imap_tools import AND, UidRange

            synced = 0

            existing_ids = set()
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT message_id FROM emails WHERE account_id = :account_id AND folder = :folder"
                    ),
                    {"account_id": account_id, "folder": folder},
                )
                for row in result:
                    existing_ids.add(row[0])

            batch_count = 0
            max_batches = 50  # Safety limit
            min_uid_seen = None  # Track lowest UID for pagination

            while batch_count < max_batches:
                # First batch: fetch newest emails (no UID filter)
                # Subsequent batches: fetch emails older than min_uid_seen
                if min_uid_seen is not None:
                    criteria = AND(uid_range=UidRange("1", str(min_uid_seen - 1)))  # type: ignore[call-arg]
                    messages = list(mailbox.fetch(criteria, limit=limit, reverse=True))
                else:
                    messages = list(mailbox.fetch(limit=limit, reverse=True))

                if not messages:
                    break

                batch_rows: list[dict[str, Any]] = []
                for msg in messages:
                    msg_uid = str(msg.uid)
                    if msg_uid in existing_ids:
                        continue

                    email_data = _email_to_dict(msg)
                    email_data["account_id"] = account_id
                    email_data["folder"] = folder
                    email_data["created_at"] = int(datetime.now(UTC).timestamp())

                    batch_rows.append(_email_row_params(email_data))

                    existing_ids.add(msg_uid)
                    synced += 1

                # One transaction per IMAP batch instead of one commit per row.
                _insert_email_rows(engine, batch_rows)

                # Track lowest UID for pagination
                batch_uids = [msg.uid for msg in messages if msg.uid is not None]
                if batch_uids:
                    batch_min = min(batch_uids)
                    if min_uid_seen is not None and batch_min >= min_uid_seen:
                        # No progress — all returned UIDs are >= our threshold
                        break
                    min_uid_seen = batch_min

                batch_count += 1

                # If we got fewer than limit, we've reached the end
                if len(messages) < limit:
                    break

            if synced > 0:
                account["last_sync"] = int(datetime.now(UTC).timestamp())
                # Use the oldest synced email's timestamp for incremental sync
                # so future "new" syncs pick up from where we left off
                with engine.connect() as conn:
                    result = conn.execute(
                        text(
                            "SELECT MIN(timestamp) FROM emails "
                            "WHERE account_id = :account_id AND folder = :folder"
                        ),
                        {"account_id": account_id, "folder": folder},
                    )
                    row = result.fetchone()
                    if row and row[0]:
                        account["last_timestamp"] = int(row[0])
                    else:
                        account["last_timestamp"] = int(datetime.now(UTC).timestamp())
                _save_account(user_id, account_id, account)

            logger.info(
                "email.backfill_complete",
                {"account_id": account_id, "folder": folder, "count": synced},
            )
            return synced

        else:
            # Quick sync - fetch recent emails
            last_timestamp = _resolve_watermark(account)
            synced = 0

            existing_ids = set()
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT message_id FROM emails WHERE account_id = :account_id AND folder = :folder"
                    ),
                    {"account_id": account_id, "folder": folder},
                )
                for row in result:
                    existing_ids.add(row[0])

            # Incremental watermark (audit B7): the old code ignored
            # last_timestamp entirely, so >limit new mails between syncs were
            # permanently missed. IMAP SINCE is day-granular; the UID dedup
            # above/below absorbs any overlap from the coarse cutoff.
            fetch_kwargs: dict[str, Any] = {"limit": limit, "reverse": True}
            if last_timestamp > 0:
                fetch_kwargs["date__gt"] = datetime.fromtimestamp(
                    last_timestamp, tz=UTC
                ).date()
            messages = list(mailbox.fetch(**fetch_kwargs))

            newest_timestamp = last_timestamp
            new_rows: list[dict[str, Any]] = []

            for msg in messages:
                msg_uid = str(msg.uid)
                if msg_uid in existing_ids:
                    continue

                email_data = _email_to_dict(msg)
                email_data["account_id"] = account_id
                email_data["folder"] = folder
                email_data["created_at"] = int(datetime.now(UTC).timestamp())

                new_rows.append(_email_row_params(email_data))

                if email_data["timestamp"] > newest_timestamp:
                    newest_timestamp = email_data["timestamp"]
                synced += 1

            # One transaction for the whole quick sync (audit P1).
            _insert_email_rows(engine, new_rows)

            if synced > 0:
                account["last_sync"] = int(datetime.now(UTC).timestamp())
                account["last_timestamp"] = _clamp_watermark(
                    newest_timestamp, int(datetime.now(UTC).timestamp())
                )
                _save_account(user_id, account_id, account)

            logger.info(
                "email.quick_sync_complete",
                {"account_id": account_id, "folder": folder, "count": synced},
            )
            return synced


async def _sync_emails(
    user_id: str,
    account_id: str,
    folder: str = "INBOX",
    mode: str = "new",
    limit: int = 100,
) -> int:
    """Async wrapper for sync_folder with rate limit handling."""
    cooldown_key = f"{user_id}:{account_id}"

    if cooldown_key in RATE_LIMIT_COOLDOWN:
        cooldown_until = RATE_LIMIT_COOLDOWN[cooldown_key]
        if time.time() < cooldown_until:
            logger.info(
                "email_sync.rate_limited",
                {"account_id": account_id, "cooldown_seconds": int(cooldown_until - time.time())},
            )
            return 0

    try:
        return await asyncio.to_thread(_sync_folder, account_id, folder, mode, limit, user_id)
    except Exception as e:
        error_str = str(e).lower()
        if "too many simultaneous connections" in error_str or "rate limit" in error_str:
            cooldown_minutes = getattr(get_settings().email_sync, "cooldown_minutes", 15)
            RATE_LIMIT_COOLDOWN[cooldown_key] = time.time() + (cooldown_minutes * 60)
            logger.warning(
                "email_sync.rate_limited",
                {"account_id": account_id, "cooldown_minutes": cooldown_minutes},
            )
            return 0
        raise


def start_background_sync(user_id: str, account_id: str) -> None:
    """Start background backfill sync (newest -> earliest)."""

    async def _backfill() -> None:
        limit = get_settings().email_sync.backfill_limit or 500
        count = await _sync_emails(user_id, account_id, "INBOX", "full", limit)
        logger.info(
            "email.backfill_complete",
            {"user_id": user_id, "account_id": account_id, "count": count},
        )

    try:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_backfill())
        except RuntimeError:
            asyncio.run(_backfill())
    except Exception as e:
        logger.error(
            "email.backfill_error",
            {
                "user_id": user_id,
                "account_id": account_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )


# Interval sync scheduler
_scheduler_task: asyncio.Task[None] | None = None
_running = False


async def start_interval_sync() -> None:
    """Start background interval sync for all accounts."""
    global _scheduler_task, _running

    if _scheduler_task is not None:
        return

    if not get_settings().email_sync.enabled:
        logger.info("email_sync.disabled", {}, user_id="system")
        return

    _running = True
    _scheduler_task = asyncio.create_task(_run_interval_sync())
    logger.info(
        "email_sync.started",
        {"interval_minutes": get_settings().email_sync.interval_minutes},
        user_id="system",
    )


async def stop_interval_sync() -> None:
    """Stop background interval sync."""
    global _scheduler_task, _running
    _running = False

    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None

    logger.info("email_sync.stopped", {}, user_id="system")


async def _run_interval_sync() -> None:
    """Main interval sync loop."""
    interval_seconds = get_settings().email_sync.interval_minutes * 60

    while _running:
        try:
            await _sync_all_accounts()
        except Exception as e:
            logger.error("email_sync.error", {"error": str(e)})

        for _ in range(interval_seconds):
            if not _running:
                break
            await asyncio.sleep(1)


async def _sync_all_accounts() -> None:
    """Sync all connected accounts."""
    from src.storage.user import get_all_user_ids

    user_ids = get_all_user_ids()
    # Filter out "default_user" user - it's not valid for email operations
    user_ids = [uid for uid in user_ids if uid and uid != "default_user"]
    batch_size = get_settings().email_sync.batch_size

    for user_id in user_ids:
        accounts = _load_accounts(user_id)
        for account_id, account in accounts.items():
            cooldown_key = f"{user_id}:{account_id}"

            if cooldown_key in RATE_LIMIT_COOLDOWN:
                cooldown_until = RATE_LIMIT_COOLDOWN[cooldown_key]
                if time.time() < cooldown_until:
                    logger.info(
                        "email_sync.skipping_rate_limited",
                        {
                            "account": account["name"],
                            "cooldown_remaining": int(cooldown_until - time.time()),
                        },
                    )
                    continue

            try:
                await _sync_emails(
                    user_id=user_id,
                    account_id=account_id,
                    folder="INBOX",
                    mode="new",
                    limit=batch_size,
                )
            except Exception as e:
                error_str = str(e).lower()
                if "too many simultaneous connections" in error_str or "rate limit" in error_str:
                    cooldown_minutes = getattr(get_settings().email_sync, "cooldown_minutes", 15)
                    RATE_LIMIT_COOLDOWN[cooldown_key] = time.time() + (cooldown_minutes * 60)
                    logger.warning(
                        "email_sync.rate_limited",
                        {"account": account["name"], "cooldown_minutes": cooldown_minutes},
                    )
                else:
                    logger.error(
                        "email_sync.account_error",
                        {"account": account["name"], "error": str(e)},
                    )


@tool
def email_sync(
    account_name: str,
    mode: str = "new",
    folder: str = "INBOX",
    user_id: str = "",
) -> str:
    """Manually sync emails for an account.

    Args:
        account_name: Account name to sync
        mode: Sync mode - "new" (quick) or "full" (backfill all)
        folder: Folder to sync (default: INBOX)
        user_id: User ID (REQUIRED)

    Returns:
        Sync status
    """
    if not user_id:
        return "Error: user_id is required."

    from src.sdk.tools_core.email_db import get_account_id_by_name

    account_id = get_account_id_by_name(account_name, user_id)
    if not account_id:
        return f"Error: Account '{account_name}' not found."

    async def _sync() -> int:
        limit = (
            get_settings().email_sync.backfill_limit if mode == "full" else get_settings().email_sync.batch_size
        )
        count = await _sync_emails(user_id, account_id, folder, mode, limit)
        return count

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    try:
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _sync())
                count = future.result(timeout=120)
        else:
            count = asyncio.run(_sync())
        return f"Synced {count} emails ({mode} mode) for {account_name}."
    except Exception as e:
        return f"Error syncing: {e}"
