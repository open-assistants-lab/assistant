"""C1-1: email draft tool — first delivery surface (no send).

Builds a valid RFC822 EmailMessage and delivers it to the user's drafts
folder via IMAP APPEND. Sending requires human approval (C1-2 is Phase 3);
this tool can never send.
"""

from email.message import EmailMessage
from typing import Any

from src.app_logging import get_logger

logger = get_logger()


def build_draft_message(
    from_addr: str, to: str, subject: str, body: str
) -> EmailMessage:
    """Build a valid RFC822 draft with From/To/Subject set."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _draft_account(user_id: str, account_name: str) -> dict[str, Any]:
    """Resolve the account to draft from; raise ValueError with actionable
    text when nothing is configured (never crash — the tool returns the
    message as an error string)."""
    from src.sdk.tools_core.email_sync import _load_accounts

    accounts = _load_accounts(user_id)
    if not accounts:
        raise ValueError(
            "No email accounts connected. Connect an account first "
            "(Settings -> Email), then retry."
        )
    if account_name:
        from src.sdk.tools_core.email_db import get_account_id_by_name

        account_id = get_account_id_by_name(account_name, user_id)
        if not account_id:
            raise ValueError(f"Account '{account_name}' not found.")
        account = accounts.get(account_id)
        if account is None:
            raise ValueError(f"Account '{account_name}' not found.")
        return dict(account)
    # Default: the first connected account.
    return dict(next(iter(accounts.values())))


def append_draft(
    account: dict[str, Any], message: EmailMessage
) -> dict[str, Any]:
    """IMAP APPEND the message to the account's Drafts folder.

    Uses raw imaplib (not imap_tools) for an explicit APPEND payload that is
    trivially mockable; reuses email_sync's stored account credentials.
    """
    import imaplib

    host = account.get("imap_host")
    port = int(account.get("imap_port", 993))
    email = account.get("email")
    password = account.get("password")
    if not host or not email or not password:
        raise ValueError(
            "Account is missing IMAP credentials (host/email/password) — "
            "reconnect the account to draft mail."
        )

    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.login(email, password)
        # imaplib type stubs say str; the runtime returns (status, data).
        result: Any = conn.append(
            "Drafts",
            "\\Draft",
            imaplib.Time2Internaldate(0),
            message.as_string().encode("utf-8"),
        )
        typ, data = (result if isinstance(result, tuple) else (result, []))
        if typ != "OK":
            raise ValueError(f"IMAP APPEND failed: {typ} {data!r}")
        # IMAP APPEND returns [b'[APPENDUID uidvalidity uid] ...'] style data
        uid = ""
        for chunk in data or []:
            text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
            if "APPENDUID" in text:
                parts = text.split()
                if len(parts) >= 3:
                    uid = parts[-2].strip("[]")
        return {
            "status": "drafted",
            "folder": "Drafts",
            "uid": uid,
            "message_id": message.get("Message-ID", ""),
        }
    finally:
        try:
            conn.logout()
        except Exception:  # pragma: no cover - best effort teardown
            pass

from src.sdk.tools import tool  # noqa: E402  (tool import after helpers keeps module import-light)


@tool
def email_draft(
    account_name: str,
    to: str,
    subject: str,
    body: str,
    user_id: str = "",
) -> str:
    """Draft an email into the user's drafts folder (NEVER sends).

    Args:
        account_name: Account name to draft from ("" = first connected account)
        to: Recipient address
        subject: Subject line
        body: Plain-text body
        user_id: User ID (REQUIRED)

    Returns:
        JSON handle: {"status": "drafted", "folder": "Drafts", "uid": ...}
        or an actionable error string.
    """
    if not user_id:
        return "Error: user_id is required."
    if not to or not subject:
        return "Error: to and subject are required."

    try:
        account = _draft_account(user_id, account_name)
    except ValueError as e:
        return f"Error: {e}"

    from_addr = str(account.get("email", ""))
    message = build_draft_message(from_addr, to, subject, body)

    try:
        import json

        handle = append_draft(account, message)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:  # connection/transport errors -> actionable string
        logger.error(
            "email_draft.append_failed",
            {"error_type": type(e).__name__},
            user_id=user_id,
        )
        return f"Error: could not reach the drafts folder — {type(e).__name__}"

    return json.dumps(handle)
