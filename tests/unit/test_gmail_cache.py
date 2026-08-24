"""Gmail cache bulk ops + unique message_id (audit P1).

clear() must be a single DELETE that also purges journal/Chroma/DuckDB
(mirroring delete_messages_for_workspace), and upsert must dedupe by
message_id via a UNIQUE index rather than a per-row select+insert/update.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.storage.gmail_cache as gc_module
from src.storage.gmail_cache import GmailCache
from src.storage.paths import DataPaths


@pytest.fixture()
def cache(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    """Fresh GmailCache rooted at tmp_path so tests never touch user data."""
    monkeypatch.setattr(gc_module, "_stores", {})

    def fake_get_paths(user_id: str = "default_user"):
        return DataPaths(ea_root=tmp_path, user_id=user_id)

    monkeypatch.setattr(gc_module, "get_paths", fake_get_paths)
    return GmailCache("cache_user")


def _email(message_id: str, subject: str = "s", body: str = "b") -> dict[str, Any]:
    return {
        "message_id": message_id,
        "thread_id": message_id,
        "from_addr": "a@b.c",
        "to_addr": ["me@x.y"],
        "subject": subject,
        "snippet": subject,
        "body": body,
        "ts": 1234567890,
        "labels": [],
        "headers": {},
        "attachments": [],
    }


def test_upsert_dedupes_by_message_id(cache) -> None:
    cache.upsert(_email("m1", subject="first"))
    cache.upsert(_email("m1", subject="second"))
    assert cache.db.count("emails") == 1
    rows = cache.db.query("emails", limit=10)
    assert rows[0]["subject"] == "second"


def test_unique_index_exists(cache) -> None:
    with cache.db._connect() as cur:
        idx = [r[1] for r in cur.execute("PRAGMA index_list('emails')")]
    assert "idx_emails_message_id" in idx
    # Enforce at the DB level too.
    cache.upsert(_email("dup"))
    with pytest.raises(Exception):
        with cache.db._connect() as cur:
            cur.execute("INSERT INTO emails (message_id, subject) VALUES ('dup', 'x')")


def test_clear_is_single_bulk_delete(cache) -> None:
    for i in range(5):
        cache.upsert(_email(f"m{i}"))
    cache.clear()
    assert cache.db.count("emails") == 0
    assert cache.stats()["total"] == 0


def test_clear_purges_journal(cache) -> None:
    for i in range(3):
        cache.upsert(_email(f"j{i}"))
    cache.clear()
    with cache.db._connect() as cur:
        pending = cur.execute(
            "SELECT count(*) FROM _journal WHERE app_table = 'emails'"
        ).fetchone()[0]
    assert pending == 0
