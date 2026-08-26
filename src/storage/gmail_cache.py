"""Gmail email cache using HybridDB.

Fetches from Gmail API (via GmailClient + ConnectKit OAuth), stores in
HybridDB for keyed-by-message-id access + keyword/semantic/hybrid search.

Store path: data/users/{user_id}/gmail_cache/
  app.db     — SQLite + FTS5 + journal
  vectors/   — ChromaDB for semantic search
"""

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from hybriddb import HybridDB, SearchMode

from src.app_logging import get_logger
from src.config import get_settings
from src.storage.gmail_client import GmailClient, GmailNotConnectedError
from src.storage.paths import get_paths

logger = get_logger()

TABLE = "emails"
_JSON_FIELDS = {"labels", "headers", "to_addr", "attachments"}
_LIST_FIELDS = {"to_addr", "labels"}


@dataclass
class EmailResult:
    """A cached email row."""

    id: int
    message_id: str
    thread_id: str
    from_addr: str
    to_addr: list[str]
    subject: str
    snippet: str
    body: str
    ts: int
    labels: list[str]
    headers: dict[str, str]
    attachments: list[dict[str, Any]] = field(default_factory=list)
    _score: float = 0.0


def _serialize(value: Any, field_name: str) -> str | None:
    """Serialize a field value for HybridDB storage."""
    if value is None:
        return None
    if field_name in _JSON_FIELDS:
        return json.dumps(value) if not isinstance(value, str) else value
    if field_name in _LIST_FIELDS:
        if isinstance(value, list):
            return ", ".join(value)
        return str(value)
    return str(value)


def _deserialize(value: Any, field_name: str) -> Any:
    """Deserialize a field value from HybridDB."""
    if value is None:
        return [] if field_name in _LIST_FIELDS else ({} if field_name == "headers" else None)
    if field_name in _JSON_FIELDS:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {} if field_name == "headers" else []
        return value
    if field_name in _LIST_FIELDS:
        if isinstance(value, str) and value.strip():
            return [v.strip() for v in value.split(",")]
        return []
    if field_name == "ts":
        return int(value) if value else 0
    return value


class GmailCache:
    """HybridDB-backed Gmail email cache."""

    def __init__(self, user_id: str = "default_user"):
        self.user_id = user_id
        base_path = get_paths(user_id).gmail_cache_dir()
        base_path.mkdir(parents=True, exist_ok=True)
        self._base_path = base_path

        settings = get_settings()
        self.db = HybridDB(
            str(base_path),
            max_chroma_index_gb=settings.memory.messages.max_chroma_index_gb,
        )
        self.db.create_table(
            TABLE,
            {
                "message_id": "TEXT",
                "thread_id": "TEXT",
                "from_addr": "TEXT",
                "to_addr": "TEXT",
                "subject": "TEXT",
                "snippet": "LONGTEXT",
                "body": "LONGTEXT",
                "ts": "INTEGER",
                "labels": "JSON",
                "headers": "JSON",
                "attachments": "JSON",
            },
        )
        # Migration: add attachments column if not present
        try:
            self.db.add_column(TABLE, "attachments", "JSON")
        except Exception:
            pass  # already exists

        # Audit P1: UNIQUE(message_id) + journal-aware bulk ops. A unique
        # index (not CREATE TABLE IF NOT EXISTS, which never alters existing
        # user DBs) makes upsert a single ON CONFLICT statement and keeps
        # bulk sync from degrading to per-row selects.
        self._migrate_unique_message_id()

    # -- CRUD --

    def _migrate_unique_message_id(self) -> None:
        """Rebuild legacy DBs so message_id is UNIQUE (audit P1).

        CREATE TABLE IF NOT EXISTS never alters an existing user DB, so
        legacy caches lack the UNIQUE index. Rebuild them here (dedupe +
        unique index) so the ON CONFLICT upsert below can rely on it.
        """
        db_path = self._base_path / "app.db"
        if not db_path.exists():
            return
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            has_index = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='idx_emails_message_id'"
            ).fetchone()
            if has_index is None:
                # Dedupe (keep the newest row per message_id), then index.
                conn.execute(
                    "DELETE FROM emails WHERE id NOT IN "
                    "(SELECT MAX(id) FROM emails GROUP BY message_id)"
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_message_id "
                    "ON emails(message_id)"
                )
            conn.commit()
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

    def upsert(self, email: dict[str, Any]) -> int | None:
        """Insert or update an email by Gmail message_id. Returns row id."""
        msg_id = email.get("message_id")
        if not msg_id:
            logger.warning("gmail_upsert_no_id", {"reason": "missing message_id"})
            return None

        row = {
            "message_id": msg_id,
            "thread_id": _serialize(email.get("thread_id"), "thread_id"),
            "from_addr": _serialize(email.get("from_addr"), "from_addr"),
            "to_addr": _serialize(email.get("to_addr"), "to_addr"),
            "subject": _serialize(email.get("subject"), "subject"),
            "snippet": _serialize(email.get("snippet"), "snippet"),
            "body": _serialize(email.get("body"), "body"),
            "ts": _serialize(email.get("ts"), "ts"),
            "labels": _serialize(email.get("labels"), "labels"),
            "headers": _serialize(email.get("headers"), "headers"),
            "attachments": _serialize(email.get("attachments"), "attachments"),
        }

        cols = list(row.keys())
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "message_id")
        with self.db._connect() as cur:
            cur.execute(
                f"INSERT INTO emails ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(message_id) DO UPDATE SET {updates}",
                [row[c] for c in cols],
            )
            # lastrowid is 0 on the DO UPDATE branch with a fresh connection,
            # so resolve the row by the unique message_id instead.
            fetched = cur.execute(
                "SELECT * FROM emails WHERE message_id = ?", (msg_id,)
            ).fetchone()
            if fetched is None:
                raise RuntimeError(f"upserted row missing ({msg_id})")
            full_row = dict(fetched)
            internal_rowid = int(full_row["id"])  # INTEGER PK aliases rowid
            # Raw SQL bypasses HybridDB's journal; write the same rows
            # insert()/update() would so Chroma/DuckDB stay consistent.
            now = datetime.now(UTC).isoformat()
            metadata = self.db._row_to_metadata(TABLE, full_row)
            for col in self.db._get_longtext_columns(TABLE, cur=cur):
                cur.execute(
                    "INSERT INTO _journal "
                    "(app_table, row_id, column_name, op, data, metadata, created_at) "
                    "VALUES (?, ?, ?, 'add', ?, ?, ?)",
                    (TABLE, internal_rowid, col, full_row.get(col, ""),
                     json.dumps(metadata), now),
                )
            cur.execute(
                "INSERT INTO _journal (app_table, row_id, op, data, created_at) "
                "VALUES (?, ?, 'row_add', ?, ?)",
                (TABLE, internal_rowid, json.dumps(full_row, default=str), now),
            )
        self.db._process_journal()
        return cast(int, full_row.get("id") or internal_rowid)

    def upsert_batch(self, emails: list[dict[str, Any]]) -> int:
        """Insert or update multiple emails. Returns count upserted."""
        count = 0
        for email in emails:
            if self.upsert(email) is not None:
                count += 1
        return count

    def get_by_message_id(self, message_id: str) -> EmailResult | None:
        """Get a single email by Gmail message_id."""
        rows = self.db.query(TABLE, where="message_id = ?", params=(message_id,), limit=1)
        if not rows:
            return None
        return self._row_to_result(rows[0])

    def get_recent(self, limit: int = 20) -> list[EmailResult]:
        """Get most recent emails by timestamp."""
        rows = self.db.query(TABLE, order_by="ts DESC", limit=limit)
        return [self._row_to_result(r) for r in rows]

    def count(self) -> int:
        return cast(int, self.db.count(TABLE))

    # -- Search --

    def search_keyword(self, query: str, limit: int = 10) -> list[EmailResult]:
        """Keyword search across subject, snippet, body (FTS5)."""
        if not query:
            return self.get_recent(limit)
        rows = self.db.search(TABLE, "body", query, mode=SearchMode.KEYWORD, limit=limit)
        return [self._row_to_result(r) for r in rows]

    def search_semantic(self, query: str, limit: int = 10) -> list[EmailResult]:
        """Semantic search across snippet and body (ChromaDB)."""
        if not query:
            return self.get_recent(limit)
        rows = self.db.search(TABLE, "body", query, mode=SearchMode.SEMANTIC, limit=limit)
        return [self._row_to_result(r) for r in rows]

    def search_hybrid(
        self,
        query: str,
        limit: int = 10,
        fts_weight: float = 0.5,
        recency_weight: float = 0.3,
        from_addr: str | None = None,
        labels: list[str] | None = None,
    ) -> list[EmailResult]:
        """Hybrid search with optional filters."""
        if not query:
            return self.get_recent(limit)

        where: dict[str, Any] | None = None
        if from_addr or labels:
            where = {}
            if from_addr:
                where["from_addr"] = from_addr
            if labels:
                where["labels"] = {"$contains": labels} if len(labels) > 1 else labels[0]

        rows = self.db.search(
            TABLE,
            "body",
            query,
            mode=SearchMode.HYBRID,
            limit=limit,
            fts_weight=fts_weight,
            recency_weight=recency_weight,
            recency_column="ts",
            where=where,
        )
        return [self._row_to_result(r) for r in rows]

    def query_by_label(self, label: str, limit: int = 50) -> list[EmailResult]:
        """Get emails with a specific label (e.g. INBOX, SENT, UNREAD)."""
        rows = self.db.query(
            TABLE,
            where="labels LIKE ?",
            params=(f"%{label}%",),
            order_by="ts DESC",
            limit=limit,
        )
        return [self._row_to_result(r) for r in rows]

    # -- Attachments --

    def download_attachment(
        self, message_id: str, filename: str, output_dir: str | None = None, client: GmailClient | None = None
    ) -> str | None:
        """Download a specific attachment via GmailClient (ConnectKit OAuth).

        Returns the path to the downloaded file, or None on failure.
        """
        attachment_id = None
        email = self.get_by_message_id(message_id)
        if email and email.attachments:
            for a in email.attachments:
                if a.get("filename") == filename:
                    attachment_id = a.get("attachmentId")
                    break

        if not attachment_id:
            # Try fetching fresh (async fetch via the injected-or-new client).
            client = client or GmailClient(self.user_id)
            email_dict = _run_async(
                lambda: _fetch_one_email_async(client, message_id, message_id, fetch_body=True)
            )
            if email_dict:
                for a in email_dict.get("attachments", []):
                    if a.get("filename") == filename:
                        attachment_id = a.get("attachmentId")
                        break

        if not attachment_id:
            logger.warning(
                "gmail_attachment_not_found", {"message_id": message_id, "filename": filename}
            )
            return None

        out_dir = (
            Path(output_dir)
            if output_dir
            else get_paths(self.user_id).gmail_cache_dir() / "attachments" / message_id
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        client = client or GmailClient(self.user_id)
        try:
            data = _run_async(lambda: client.get_attachment(message_id, attachment_id))
        except Exception as e:
            logger.error("gmail_attachment_download_error", {"error": str(e)[:200]})
            return None
        if data:
            out_path.write_bytes(data)
            return str(out_path)
        return None

    # -- Helpers --

    def clear(self) -> None:
        """Purge all cached emails (single bulk delete, journal-aware)."""
        with self.db._connect() as cur:
            cur.execute("DELETE FROM emails")
            cur.execute("DELETE FROM _journal WHERE app_table = 'emails'")
        # FTS stays in sync via triggers; Chroma vectors and the DuckDB
        # mirror must be cleaned explicitly (raw DELETE bypasses journaling).
        if self.db._chroma is not None:
            try:
                for col in self.db._get_longtext_columns(TABLE):
                    coll = self.db._get_collection(f"{TABLE}_{col}")
                    if coll is not None:
                        # id-based delete: where={} is rejected on chromadb
                        # >=1.5 ("Expected where to have exactly one operator").
                        ids = [str(r) for r in coll.get()["ids"]]
                        if ids:
                            coll.delete(ids=ids)
            except Exception:
                pass
        try:
            self.db.sync_duckdb_table(TABLE)
        except Exception:
            pass

    def stats(self) -> dict[str, Any]:
        return {
            "total": self.db.count(TABLE),
            "health": self.db.health(TABLE),
            "journal": self.db.journal_status(TABLE),
        }

    def _row_to_result(self, row: dict[str, Any]) -> EmailResult:
        score = row.get("_score", 0.0)
        return EmailResult(
            id=row["id"],
            message_id=_deserialize(row.get("message_id"), "message_id"),
            thread_id=_deserialize(row.get("thread_id"), "thread_id"),
            from_addr=_deserialize(row.get("from_addr"), "from_addr"),
            to_addr=_deserialize(row.get("to_addr"), "to_addr"),
            subject=_deserialize(row.get("subject"), "subject"),
            snippet=_deserialize(row.get("snippet"), "snippet"),
            body=_deserialize(row.get("body"), "body"),
            ts=_deserialize(row.get("ts"), "ts"),
            labels=_deserialize(row.get("labels"), "labels"),
            headers=_deserialize(row.get("headers"), "headers"),
            attachments=_deserialize(row.get("attachments"), "attachments"),
            _score=score,
        )


# -- Singleton cache --

_stores: dict[str, GmailCache] = {}


def get_gmail_cache(user_id: str = "default_user") -> GmailCache:
    if user_id not in _stores:
        _stores[user_id] = GmailCache(user_id)
    return _stores[user_id]


# -- Sync from Gmail API via GmailClient (OAuth, replaces gws CLI) --

_LIST_PAGE_SIZE = 500
_UPSERT_FLUSH = 25


def _run_async(factory: Any) -> Any:
    """Run an async coroutine from a sync context (running-loop safe)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(factory())).result(timeout=240)
    return asyncio.run(factory())


def sync_emails(
    user_id: str = "default_user",
    max_results: int = 50,
    query: str | None = None,
    fetch_body: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    """Sync emails from Gmail API into the cache (sync facade).

    Uses GmailClient (ConnectKit OAuth token) instead of the gws CLI.

    Returns dict with counts: {listed, fetched, upserted, errors[, error]}
    """
    cache = get_gmail_cache(user_id)
    client = GmailClient(user_id)
    return cast(
        dict[str, Any],
        _run_async(
            lambda: _sync_emails_async(
                user_id,
                cache,
                client,
                max_results=max_results,
                query=query,
                fetch_body=fetch_body,
                progress=progress,
            )
        ),
    )


async def _sync_emails_async(
    user_id: str,
    cache: GmailCache,
    client: GmailClient,
    max_results: int = 50,
    query: str | None = None,
    fetch_body: bool = True,
    progress: bool = False,
) -> dict[str, Any]:
    """Async core: paginate message ids, fetch details, upsert into HybridDB."""
    try:
        return await _sync_emails_core(cache, client, max_results, query, fetch_body, progress)
    except GmailNotConnectedError as e:
        logger.warning("gmail_sync_not_connected", {"error": str(e)}, user_id=user_id)
        return {"listed": 0, "fetched": 0, "upserted": 0, "errors": 1, "error": str(e)}


async def _sync_emails_core(
    cache: GmailCache,
    client: GmailClient,
    max_results: int,
    query: str | None,
    fetch_body: bool,
    progress: bool,
) -> dict[str, Any]:
    """Paginate message ids, fetch details, upsert in batches."""
    all_messages: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = 0
    page_size = min(max_results, _LIST_PAGE_SIZE) if max_results > 0 else _LIST_PAGE_SIZE

    while True:
        list_json = await client.list_messages(max_results=page_size, query=query, page_token=page_token)
        pages += 1
        if list_json is None:
            if pages == 1:
                return {"listed": 0, "fetched": 0, "upserted": 0, "errors": 1}
            break

        messages = list_json.get("messages", [])
        all_messages.extend(messages)
        page_token = list_json.get("nextPageToken")
        if not page_token or len(all_messages) >= max_results:
            break

    if not all_messages:
        return {"listed": 0, "fetched": 0, "upserted": 0, "errors": 0}

    total = len(all_messages)
    logger.info("gmail_sync_listed", {"total": total, "pages": pages})

    fetched = 0
    upserted = 0
    errors = 0
    batch: list[dict[str, Any]] = []

    for i, msg in enumerate(all_messages):
        msg_id = msg["id"]
        thread_id = msg.get("threadId", msg_id)
        email_data = await _fetch_one_email_async(client, msg_id, thread_id, fetch_body)

        if email_data:
            batch.append(email_data)
            fetched += 1
        else:
            errors += 1

        if len(batch) >= _UPSERT_FLUSH:
            upserted += cache.upsert_batch(batch)
            batch.clear()

        if progress and (i + 1) % 10 == 0:
            print(f"  {i + 1}/{total} ...", end="\r", flush=True)

    if batch:
        upserted += cache.upsert_batch(batch)
    if progress:
        print(f"  {total}/{total} done.             ")

    cache.db.process_journal(limit=10000)
    return {"listed": total, "fetched": fetched, "upserted": upserted, "errors": errors}


async def _fetch_one_email_async(
    client: GmailClient,
    message_id: str,
    thread_id: str,
    fetch_body: bool = True,
) -> dict[str, Any] | None:
    """Fetch a single email's metadata (and optionally body) via GmailClient."""
    try:
        data = await client.get_message(message_id, fmt="full" if fetch_body else "metadata")
    except Exception as e:
        logger.error("gmail_fetch_error", {"message_id": message_id, "error": str(e)[:200]})
        return None
    if data is None:
        return None

    payload = data.get("payload", {})
    headers_dict = {}
    for h in payload.get("headers", []):
        headers_dict[h["name"]] = h["value"]

    # Parse timestamp
    date_str = headers_dict.get("Date", "")
    ts = _parse_date_to_ts(date_str)

    # Extract body
    body = ""
    if fetch_body:
        body = _extract_body(payload)

    # Parse recipients
    to_raw = headers_dict.get("To", "")
    to_list = _parse_address_list(to_raw) if to_raw else []

    # Labels
    labels = data.get("labelIds", [])

    # Attachments
    attachments = _extract_attachments(payload) if fetch_body else []

    # Headers we care about
    important_headers = {}
    for key in ["List-Unsubscribe", "List-Unsubscribe-Post", "Message-ID", "In-Reply-To", "References"]:
        val = headers_dict.get(key, "")
        if val:
            important_headers[key] = val

    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "from_addr": headers_dict.get("From", ""),
        "to_addr": to_list,
        "subject": headers_dict.get("Subject", "(no subject)"),
        "snippet": data.get("snippet", ""),
        "body": body,
        "ts": ts,
        "labels": labels,
        "headers": important_headers,
        "attachments": attachments,
    }



def _extract_body(payload: dict[str, Any]) -> str:
    """Extract plain text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            import base64

            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return ""
        return ""

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    # Fallback: try HTML if no plain text
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                import base64

                try:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return _strip_html(html)
                except Exception:
                    pass

    return ""


def _strip_html(html: str) -> str:
    """Basic HTML tag stripping to get readable text."""
    import re

    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def _extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract attachment metadata from a Gmail message payload."""
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename", "").strip()
        if filename:
            body_info = part.get("body", {})
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", ""),
                "size": body_info.get("size", 0),
                "attachmentId": body_info.get("attachmentId", ""),
            })
        for p in part.get("parts", []):
            walk(p)

    walk(payload)
    return attachments


def _parse_date_to_ts(date_str: str) -> int:
    """Parse an email Date header to Unix timestamp."""
    if not date_str:
        return 0
    from email.utils import parsedate_to_datetime

    try:
        return int(parsedate_to_datetime(date_str).timestamp())
    except Exception:
        return 0


def _parse_address_list(raw: str) -> list[str]:
    """Parse a comma-separated address list like '\"Name\" <a@b.com>, <c@d.com>'."""
    if not raw:
        return []
    from email.utils import getaddresses

    try:
        return [addr for name, addr in getaddresses([raw]) if addr]
    except Exception:
        return [a.strip() for a in raw.split(",") if a.strip()]
