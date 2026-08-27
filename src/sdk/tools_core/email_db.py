"""Email database operations - single source of truth for all email/contacts DB operations."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import create_engine, text

from src.app_logging import get_logger
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

# Cached SQLAlchemy engines per user (audit P1): get_engine previously built
# a fresh engine + re-ran the full DDL on every invocation and never disposed
# it — pooled SQLite connections piled up until GC.
_engines: dict[str, Any] = {}


def get_db_path(user_id: str) -> str:
    """Get SQLite database path for user."""
    from src.storage.paths import get_paths

    uid = user_id or DEFAULT_USER_ID
    return str(get_paths(uid).email_db())


def get_engine(user_id: str) -> Any:
    """Get SQLAlchemy engine with schema initialized (cached per user)."""
    cached = _engines.get(user_id)
    if cached is not None:
        return cached
    db_path = get_db_path(user_id)
    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)
    _engines[user_id] = engine
    return engine


def init_db(engine: Any) -> None:
    """Initialize database schema."""
    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                imap_host TEXT NOT NULL,
                imap_port INTEGER DEFAULT 993,
                smtp_host TEXT NOT NULL,
                smtp_port INTEGER DEFAULT 587,
                provider TEXT NOT NULL,
                folders TEXT NOT NULL,
                last_sync INTEGER,
                last_timestamp INTEGER,
                status TEXT DEFAULT 'connected',
                created_at INTEGER NOT NULL
            )
        """)
        )

        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS emails (
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL,
                message_id TEXT NOT NULL,
                from_addr TEXT,
                from_name TEXT,
                to_addrs TEXT,
                cc_addrs TEXT,
                subject TEXT,
                body_text TEXT,
                timestamp INTEGER NOT NULL,
                in_reply_to TEXT,
                thread_references TEXT,
                is_forwarded INTEGER DEFAULT 0,
                read INTEGER DEFAULT 0,
                flagged INTEGER DEFAULT 0,
                has_attachments INTEGER DEFAULT 0,
                attachments TEXT,
                tags TEXT,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (account_id, folder, message_id)
            )
        """)
        )

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_emails_timestamp ON emails(timestamp)"))

        conn.commit()


def load_accounts(user_id: str) -> dict[str, Any]:
    """Load all accounts for a user."""
    engine = get_engine(user_id)

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


def save_account(user_id: str, account_id: str, account: dict[str, Any]) -> None:
    """Save account to database."""
    engine = get_engine(user_id)

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


def delete_account(user_id: str, account_id: str) -> None:
    """Delete account and all associated emails from database."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM emails WHERE account_id = :id"), {"id": account_id})
        conn.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": account_id})
        conn.commit()


def get_account_id_by_name(account_name: str, user_id: str) -> str | None:
    """Get account ID by name or email."""
    accounts = load_accounts(user_id)
    for acc_id, acc in accounts.items():
        if acc.get("name") == account_name:
            return acc_id
        if acc.get("email") == account_name:
            return acc_id
    return None


def get_imap_connection(account_id: str, user_id: str) -> Any:
    """Get IMAP connection for account."""
    accounts = load_accounts(user_id)
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


def parse_email_date(date_str: str) -> int:
    """Parse email date string to Unix timestamp."""
    if not date_str:
        return int(datetime.now(UTC).timestamp())
    try:
        dt = parsedate_to_datetime(date_str)
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(UTC).timestamp())


def email_to_dict(msg: Any) -> dict[str, Any]:
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

    date_ts = parse_email_date(msg.date_str) if msg.date_str else int(datetime.now(UTC).timestamp())

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


def parse_email_flags(msg: Any) -> dict[str, bool]:
    """Extract read/flagged state from an imap_tools message.

    imap_tools >=1.x returns ``msg.flags`` as a tuple of flag strings
    (``('SEEN', '\\Flagged')``). The previous ``hasattr(msg.flags, "Seen")``
    check was always False on a tuple, hardcoding every email to
    ``read=True / flagged=False`` (audit B7) — which also zeroed the
    scheduler's unread/urgent counts.
    """
    flags = msg.flags or ()
    return {
        "read": "SEEN" in {str(f).upper() for f in flags},
        "flagged": any(
            str(f).upper().lstrip("\\") == "FLAGGED" for f in flags
        ),
    }


def detect_provider(email: str) -> tuple[str, str, int, str, int]:
    """Detect email provider."""
    email_lower = email.lower()

    if "gmail.com" in email_lower or "googlemail.com" in email_lower:
        return ("gmail", "imap.gmail.com", 993, "smtp.gmail.com", 587)
    elif "outlook.com" in email_lower or "hotmail.com" in email_lower or "live.com" in email_lower:
        return ("outlook", "outlook.office365.com", 993, "smtp.office365.com", 587)
    elif "icloud.com" in email_lower or "me.com" in email_lower:
        return ("icloud", "imap.mail.me.com", 993, "smtp.mail.me.com", 587)
    elif "yahoo.com" in email_lower:
        return ("yahoo", "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 587)

    return ("other", "", 993, "", 587)
