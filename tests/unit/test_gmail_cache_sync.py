"""G3 — gmail_cache sync via GmailClient (replaces gws CLI subprocess).

Hermetic: the async core is tested with an injected fake client; HybridDB
caches land under tmp via a monkeypatched get_paths (DataPaths ea_root).
"""

import pytest

from src.storage.gmail_cache import (
    _fetch_one_email_async,
    _sync_emails_async,
)
from src.storage.gmail_client import GmailNotConnectedError


class FakeGmailClient:
    """Minimal async stand-in for GmailClient."""

    def __init__(self, pages=None, messages=None, error=None):
        self.pages = list(pages or [])
        self.messages = messages or {}
        self.error = error
        self.list_calls = 0

    async def list_messages(self, max_results=50, query=None, page_token=None):
        self.list_calls += 1
        if self.error:
            raise self.error
        if not self.pages:
            return {"messages": []}
        return self.pages.pop(0)

    async def get_message(self, message_id, fmt="metadata"):
        if self.error:
            raise self.error
        return self.messages.get(message_id, {})

    async def get_attachment(self, message_id, attachment_id):
        return b"attachment-bytes"


def _make_cache(tmp_path, monkeypatch, user_id="default_user"):
    """Real GmailCache with HybridDB under tmp (DataPaths ea_root isolation)."""
    from src.storage.paths import DataPaths

    fake = DataPaths(data_root=str(tmp_path), data_path=str(tmp_path))
    monkeypatch.setattr("src.storage.gmail_cache.get_paths", lambda uid, **kw: fake)

    import src.storage.gmail_cache as gc

    gc._stores.pop(user_id, None)
    return gc.get_gmail_cache(user_id)


def _msg_payload(subject="Hello", message_id="m1", body="hello body"):
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "snippet",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Bob <bob@example.com>"},
                {"name": "Date", "value": "Mon, 3 Jun 2026 10:00:00 +0000"},
                {"name": "Subject", "value": subject},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "aGVsbG8gYm9keQ=="}},
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"size": 42, "attachmentId": "att-1"},
                },
            ],
        },
    }


@pytest.mark.asyncio
async def test_sync_paginates_and_upserts(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path, monkeypatch)
    client = FakeGmailClient(
        pages=[
            {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "tok2"},
            {"messages": [{"id": "m3"}]},
        ],
        messages={
            "m1": _msg_payload("first", "m1"),
            "m2": _msg_payload("second", "m2"),
            "m3": _msg_payload("third", "m3"),
        },
    )

    result = await _sync_emails_async("default_user", cache, client, max_results=50)

    assert client.list_calls == 2  # paginated through nextPageToken
    assert result["listed"] == 3
    assert result["fetched"] == 3
    assert result["upserted"] == 3
    assert result["errors"] == 0

    # All three bodies contain "hello body" (FTS searches the body column).
    rows = cache.search_keyword("hello")
    assert {r.subject for r in rows} == {"first", "second", "third"}
    # Attachment metadata captured from the payload walk.
    row_m1 = next(r for r in rows if r.subject == "first")
    assert row_m1.attachments[0]["filename"] == "report.pdf"


@pytest.mark.asyncio
async def test_fetch_one_email_parses_payload():
    client = FakeGmailClient(messages={"m1": _msg_payload()})
    email = await _fetch_one_email_async(client, "m1", "thread-m1", fetch_body=True)
    assert email is not None
    assert email["from_addr"] == "Alice <alice@example.com>"
    assert email["to_addr"] == ["bob@example.com"]
    assert email["subject"] == "Hello"
    assert "hello body" in email["body"]
    assert email["labels"] == ["INBOX", "UNREAD"]
    assert email["attachments"][0]["attachmentId"] == "att-1"


@pytest.mark.asyncio
async def test_fetch_failure_counts_as_error(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path, monkeypatch)
    client = FakeGmailClient(pages=[{"messages": [{"id": "m1"}]}])

    async def fail_get(message_id, fmt="metadata"):
        raise RuntimeError("api down")

    client.get_message = fail_get

    result = await _sync_emails_async("default_user", cache, client)
    assert result["listed"] == 1
    assert result["errors"] == 1
    assert result["fetched"] == 0


@pytest.mark.asyncio
async def test_not_connected_surfaces_clear_error(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path, monkeypatch)
    client = FakeGmailClient(
        error=GmailNotConnectedError("Gmail is not connected — Sign in with Google first.")
    )
    result = await _sync_emails_async("default_user", cache, client)
    assert result["errors"] >= 1
    assert "Sign in with Google" in result.get("error", "")


@pytest.mark.asyncio
async def test_download_attachment_writes_file(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path, monkeypatch)
    client = FakeGmailClient(
        pages=[{"messages": [{"id": "m1"}]}],
        messages={"m1": _msg_payload()},
    )
    # Seed the store so the attachment id is discoverable.
    await _sync_emails_async("default_user", cache, client, max_results=50)

    out_dir = tmp_path / "attachments"
    path = cache.download_attachment("m1", "report.pdf", output_dir=str(out_dir), client=client)
    assert path is not None
    assert path.endswith("report.pdf")
    assert out_dir.joinpath("report.pdf").read_bytes() == b"attachment-bytes"
