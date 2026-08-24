"""Audit P2: get_messages_with_summary must issue O(1)+tail queries.

Regression tests for history-read scaling: query count must be bounded
(< 8 SELECTs) regardless of session size, on both the no-summary and
summary paths, and the per-session summary cache must be invalidated
exactly when summary-relevant state changes.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager

import pytest

from src.storage.messages import MessageStore


def _store() -> MessageStore:
    temp_dir = tempfile.TemporaryDirectory()
    store = MessageStore("test_user", base_dir=temp_dir.name)
    store._temp_dir = temp_dir
    return store


def _stored_summary_metadata(
    summarized_message_ids: list[str], preserved_message_ids: list[str] | None = None
) -> dict[str, object]:
    return {
        "source": "summarization_middleware",
        "compression_reason": "threshold",
        "summarized_message_ids": summarized_message_ids,
        "preserved_message_ids": preserved_message_ids or [],
    }


def _seed_session(store: MessageStore, session_id: str, count: int) -> str:
    """Insert `count` user messages; returns the id of the first message."""
    with store._core.db._connect() as cur:
        cur.executemany(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    f"seed-{i:05d}",
                    "2026-08-03T12:00:00+00:00",
                    "user",
                    f"message-{i}",
                    None,
                    session_id,
                )
                for i in range(count)
            ],
        )
    return "seed-00000"


def _seed_summary_early(
    store: MessageStore, session_id: str, count: int
) -> str:
    """Insert a valid summary at rowid 2, then `count` trailing messages.

    Places the summary near the START of a large session so the old
    backward batch walk (from the end) must scan ~count/100 batches.
    Returns the summarized first-message id.
    """
    with store._core.db._connect() as cur:
        cur.execute(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "seed-first",
                "2026-08-03T12:00:00+00:00",
                "user",
                "first",
                None,
                session_id,
            ),
        )
        cur.execute(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "seed-summary",
                "2026-08-03T12:00:00+00:00",
                "summary",
                "cached summary",
                json.dumps(_stored_summary_metadata(["seed-first"])),
                session_id,
            ),
        )
        cur.executemany(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    f"seed-{i:05d}",
                    "2026-08-03T12:00:00+00:00",
                    "user",
                    f"message-{i}",
                    None,
                    session_id,
                )
                for i in range(count)
            ],
        )
    return "seed-first"


@pytest.fixture
def query_counter():
    """Return a context manager that counts SELECTs on the store's cursor."""

    @contextmanager
    def _counter(store: MessageStore):
        original = store._core.db._connect
        counts = {"selects": 0}

        class CountingCursor:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, sql, *args, **kwargs):
                if str(sql).lstrip().upper().startswith("SELECT"):
                    counts["selects"] += 1
                return self._cursor.execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):
                return self._cursor.executemany(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._cursor, name)

        @contextmanager
        def _wrapped_connect():
            with original() as cursor:
                yield CountingCursor(cursor)

        store._core.db._connect = _wrapped_connect  # type: ignore[method-assign]
        yield counts
        store._core.db._connect = original  # type: ignore[method-assign]

    return _counter


def test_no_summary_session_issues_constant_queries(query_counter) -> None:
    store = _store()
    _seed_session(store, "s1", 5000)

    with query_counter(store) as counts:
        messages = store.get_messages_with_summary("s1", limit=50)

    assert len(messages) == 50
    assert counts["selects"] < 8, (
        f"no-summary read issued {counts['selects']} SELECTs for a 5000-message "
        "session (must be O(1)+tail)"
    )

    # A second read must reuse the cached no-summary result: tail only.
    with query_counter(store) as counts2:
        store.get_messages_with_summary("s1", limit=50)
    assert counts2["selects"] < 8


def test_summary_session_issues_constant_queries(query_counter) -> None:
    store = _store()
    # Summary at the START of a 5000-message session: the old backward
    # batch walk would scan ~50 batches from the end to find it.
    first_id = _seed_summary_early(store, "s-b", 5000)

    # First call: cache miss -> indexed lookup + direct validation + tail.
    with query_counter(store) as counts:
        messages = store.get_messages_with_summary("s-b", limit=50)
    assert messages[0].content == "cached summary"
    assert first_id == "seed-first"
    assert counts["selects"] < 8, (
        f"summary read issued {counts['selects']} SELECTs on cache miss "
        "(must be O(1)+tail)"
    )

    # Second call: cache hit -> provenance reused, tail only.
    with query_counter(store) as counts2:
        store.get_messages_with_summary("s-b", limit=50)
    assert counts2["selects"] < 8


def test_cache_skips_provenance_validation_on_hit(query_counter) -> None:
    """The cached provenance must skip the second full-prefix load.

    A session with a summary at the start and only a handful of trailing
    messages: the cache-hit read must not re-load the whole prefix.
    """
    store = _store()
    _seed_summary_early(store, "s-c", 5000)

    store.get_messages_with_summary("s-c", limit=50)  # warm the cache
    with query_counter(store) as counts:
        store.get_messages_with_summary("s-c", limit=50)

    assert counts["selects"] <= 3, (
        f"cache-hit read issued {counts['selects']} SELECTs (expected tail-only, "
        "<= 3)"
    )


def test_cache_invalidated_on_new_summary() -> None:
    store = _store()
    first = store.add_message("user", "first", session_id="s-d")
    store.add_message("user", "second", session_id="s-d")
    store.add_summary_message(
        "summary-1", session_id="s-d", metadata=_stored_summary_metadata([first])
    )

    # Warm cache.
    assert [m.content for m in store.get_messages_with_summary("s-d")] == ["summary-1"]

    # Adding a newer summary must invalidate the cached rowid/provenance.
    store.add_summary_message(
        "summary-2",
        session_id="s-d",
        metadata=_stored_summary_metadata([first], preserved_message_ids=[]),
    )
    messages = store.get_messages_with_summary("s-d")
    assert messages[0].content == "summary-2"


def test_cache_invalidated_on_delete_session() -> None:
    store = _store()
    first_id = store.add_message("user", "first", session_id="s-e")
    store.add_summary_message(
        "summary", session_id="s-e", metadata=_stored_summary_metadata([first_id])
    )
    store.get_messages_with_summary("s-e")  # warm cache

    store.delete_session("s-e")
    assert store.get_messages_with_summary("s-e") == []


def test_cache_invalidated_on_clear() -> None:
    store = _store()
    first_id = store.add_message("user", "first", session_id="s-f")
    store.add_summary_message(
        "summary", session_id="s-f", metadata=_stored_summary_metadata([first_id])
    )
    store.get_messages_with_summary("s-f")  # warm cache

    store.clear()
    assert store.get_messages_with_summary("s-f") == []


def test_cache_survives_plain_appends() -> None:
    """Plain add_message must NOT invalidate the summary cache (append-safe)."""
    store = _store()
    first_id = store.add_message("user", "first", session_id="s-g")
    store.add_summary_message(
        "summary", session_id="s-g", metadata=_stored_summary_metadata([first_id])
    )
    store.get_messages_with_summary("s-g")  # warm cache
    store.add_message("user", "appended", session_id="s-g")

    messages = store.get_messages_with_summary("s-g")
    assert [m.content for m in messages] == ["summary", "appended"]

def test_cache_not_poisoned_by_malformed_newest_summary(query_counter) -> None:
    """The newest (malformed) summary must not shadow an older valid one,
    even when the cache is warm."""
    store = _store()
    first_id = store.add_message("user", "first", session_id="s-h")
    store.add_summary_message(
        "valid", session_id="s-h", metadata=_stored_summary_metadata([first_id])
    )
    # A later malformed summary row (no source / provenance).
    with store._core.db._connect() as cur:
        cur.execute(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bad", "2026-08-03T13:00:00+00:00", "summary", "malformed", None, "s-h"),
        )
    store.add_message("assistant", "new", session_id="s-h")

    store.get_messages_with_summary("s-h")  # warm cache
    assert [m.content for m in store.get_messages_with_summary("s-h")] == [
        "valid",
        "new",
    ]


def test_invalidate_summary_cache_helper() -> None:
    """P2-5: _invalidate_summary_cache() is the single invalidation entry point."""
    store = _store()
    sid = "s-inv"
    store.add_message(sid, "user", "q1")
    store.get_messages_with_summary(sid, limit=50)  # warms cache (possibly None)
    assert sid in store._summary_cache

    # Targeted invalidation drops only that session.
    store._summary_cache["other"] = None
    store._invalidate_summary_cache(sid)
    assert sid not in store._summary_cache
    assert "other" in store._summary_cache

    # Full invalidation clears everything.
    store._invalidate_summary_cache()
    assert not store._summary_cache
