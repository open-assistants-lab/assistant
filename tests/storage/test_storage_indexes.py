"""run_id / workspace_id generated-column index (audit P1).

The messages table should carry VIRTUAL generated columns for the common
json_extract probes (run_id, workspace_id) plus indexes, so persist_run's
idempotency probe and workspace deletes avoid per-row JSON extraction.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime

from src.storage.messages import Message, MessageStore


def _store() -> MessageStore:
    temp_dir = tempfile.TemporaryDirectory()
    store = MessageStore("test_user", base_dir=temp_dir.name)
    store._temp_dir = temp_dir
    return store


def _table_ddl(store: MessageStore) -> str:
    with store._core.db._connect() as cur:
        row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
    return str(row[0] if row else "")


def _indexes(store: MessageStore) -> list[str]:
    with store._core.db._connect() as cur:
        rows = cur.execute("PRAGMA index_list('messages')").fetchall()
    return [str(r[1]) for r in rows]


def test_generated_columns_exist_after_init() -> None:
    """SQLite 3.50 omits generated columns from PRAGMA table_info; assert via DDL."""
    store = _store()
    ddl = _table_ddl(store)
    assert "run_id TEXT GENERATED ALWAYS AS (json_extract(metadata, '$.run_id')) VIRTUAL" in ddl
    assert (
        "workspace_id TEXT GENERATED ALWAYS AS (json_extract(metadata, '$.workspace_id')) VIRTUAL"
        in ddl
    )


def test_indexes_exist_after_init() -> None:
    store = _store()
    names = _indexes(store)
    assert "idx_messages_run_id" in names
    assert "idx_messages_workspace_id" in names


def test_generated_column_tracks_metadata_run_id() -> None:
    store = _store()
    store.add_message(
        "user", "hello", metadata={"run_id": "r-1", "workspace_id": "w-9"}, session_id="a"
    )
    with store._core.db._connect() as cur:
        row = cur.execute("SELECT run_id, workspace_id FROM messages WHERE session_id = 'a'").fetchone()
    assert row is not None
    assert row[0] == "r-1"
    assert row[1] == "w-9"


def test_persist_run_probe_uses_generated_column() -> None:
    """persist_run must resolve its idempotency probe via the run_id column."""
    store = _store()
    store.add_message("user", "hello", metadata={"run_id": "run-1"}, session_id="a")
    answer = Message(
        id="", ts=datetime.now(UTC), role="assistant", content="hi", session_id="a"
    )
    mid = store.persist_run(
        run_id="run-1",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=[],
        metadata={"model": "test:model"},
    )
    # Second persist for the same run must be idempotent (probe hits).
    mid2 = store.persist_run(
        run_id="run-1",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=[],
        metadata={"model": "test:model"},
    )
    assert mid == mid2
