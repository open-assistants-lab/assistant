"""Tests for MessageStore (CoreMem adapter)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from unittest import mock

import pytest

import src.storage.messages as messages_storage
from src.storage.messages import Message, MessageStore, get_message_store
from src.storage.paths import DataPaths


def _store() -> MessageStore:
    temp_dir = tempfile.TemporaryDirectory()
    store = MessageStore("test_user", base_dir=temp_dir.name)
    store._temp_dir = temp_dir
    return store


def _summary_metadata(
    summarized_message_ids: list[str],
    preserved_message_ids: list[str] | None = None,
    compression_reason: str = "threshold",
) -> dict[str, object]:
    return {
        "compression_reason": compression_reason,
        "summarized_message_ids": summarized_message_ids,
        "preserved_message_ids": preserved_message_ids or [],
    }


def _insert_raw_messages(
    store: MessageStore,
    rows: list[tuple[str, str, str, str, dict[str, object] | None]],
) -> None:
    with store._core.db._connect() as cur:
        cur.executemany(
            "INSERT INTO messages (id, ts, role, content, metadata, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    message_id,
                    "2026-08-03T12:00:00+00:00",
                    role,
                    content,
                    json.dumps(metadata),
                    session_id,
                )
                for message_id, role, content, session_id, metadata in rows
            ],
        )


def _stored_summary_metadata(
    summarized_message_ids: list[str], preserved_message_ids: list[str] | None = None
) -> dict[str, object]:
    return {
        "source": "summarization_middleware",
        **_summary_metadata(summarized_message_ids, preserved_message_ids),
    }


def test_message_session_id_is_preserved() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="session-a")

    messages = store.get_messages_by_session_id("session-a")

    assert messages[0].session_id == "session-a"


def test_get_messages_by_session_queries_sql_and_ignores_coremem_backend() -> None:
    store = _store()
    store.add_message("user", "allowed", session_id="session-a")
    store.add_message("user", "leaked", session_id="session-b")
    store._core.fetch = mock.Mock(side_effect=AssertionError("fetch must not run"))
    store._core.fetch_all = mock.Mock(side_effect=AssertionError("fetch_all must not run"))

    messages = store.get_messages_by_session_id(" session-a ")

    assert [message.content for message in messages] == ["allowed"]
    store._core.fetch.assert_not_called()
    store._core.fetch_all.assert_not_called()


@pytest.mark.parametrize("session_id", ["", " ", "\t\n"])
def test_get_messages_by_session_rejects_blank_before_fetch(session_id: str) -> None:
    store = _store()
    store._core.fetch = mock.Mock(side_effect=AssertionError("fetch must not run"))

    with pytest.raises(ValueError, match="session_id"):
        store.get_messages_by_session_id(session_id)

    store._core.fetch.assert_not_called()


@pytest.mark.parametrize("method", ["get_messages_with_summary", "has_summary"])
def test_summary_reads_reject_blank_session_before_fetch(method: str) -> None:
    store = _store()
    store._core.fetch = mock.Mock(side_effect=AssertionError("fetch must not run"))

    with pytest.raises(ValueError, match="session_id"):
        getattr(store, method)("  ")

    store._core.fetch.assert_not_called()


def test_add_summary_rejects_blank_session_before_write() -> None:
    store = _store()
    store._core.ingest = mock.Mock(side_effect=AssertionError("ingest must not run"))

    with pytest.raises(ValueError, match="session_id"):
        store.add_summary_message(
            "summary",
            session_id=" ",
            metadata=_summary_metadata(["message-1"]),
        )

    store._core.ingest.assert_not_called()


def test_scoped_zero_limit_validates_session_then_returns_empty() -> None:
    store = _store()
    store._core.fetch = mock.Mock(side_effect=AssertionError("fetch must not run"))

    assert store.get_messages_by_session_id(" session-a ", limit=0) == []
    assert store.get_messages_with_summary(" session-a ", limit=0) == []
    store._core.fetch.assert_not_called()


def test_add_summary_writes_session_source_and_provenance() -> None:
    store = _store()
    source_id = store.add_message("user", "before", session_id="session-a")

    summary_id = store.add_summary_message(
        "condensed",
        session_id="session-a",
        metadata=_summary_metadata([source_id], compression_reason="provider_overflow"),
    )

    summary = next(message for message in store.get_messages() if message.id == summary_id)
    assert summary.role == "summary"
    assert summary.session_id == "session-a"
    assert summary.metadata == {
        "source": "summarization_middleware",
        "compression_reason": "provider_overflow",
        "summarized_message_ids": [source_id],
        "preserved_message_ids": [],
    }


@pytest.mark.parametrize("content", ["", " ", "\n\t"])
def test_add_summary_rejects_blank_content_without_write(content: str) -> None:
    store = _store()
    store._core.ingest = mock.Mock(side_effect=AssertionError("ingest must not run"))

    with pytest.raises(ValueError, match="content"):
        store.add_summary_message(
            content,
            session_id="session-a",
            metadata=_summary_metadata(["message-1"]),
        )

    store._core.ingest.assert_not_called()


@pytest.mark.parametrize("reason", ["", "automatic", None])
def test_add_summary_rejects_unknown_reason_without_write(reason: object) -> None:
    store = _store()
    store._core.ingest = mock.Mock(side_effect=AssertionError("ingest must not run"))
    metadata = _summary_metadata(["message-1"])
    metadata["compression_reason"] = reason

    with pytest.raises(ValueError, match="compression_reason"):
        store.add_summary_message("summary", session_id="session-a", metadata=metadata)

    store._core.ingest.assert_not_called()


def test_add_summary_rejects_non_object_metadata_without_write() -> None:
    store = _store()
    store._core.ingest = mock.Mock(side_effect=AssertionError("ingest must not run"))

    with pytest.raises(ValueError, match="metadata"):
        store.add_summary_message(
            "summary",
            session_id="session-a",
            metadata=None,  # type: ignore[arg-type]
        )

    store._core.ingest.assert_not_called()


@pytest.mark.parametrize(
    ("summarized", "preserved"),
    [
        ([], []),
        ([""], []),
        (["duplicate", "duplicate"], []),
        ([1], []),
        (["message-1"], [""]),
        (["message-1"], ["duplicate", "duplicate"]),
        (["message-1"], [1]),
        (["overlap"], ["overlap"]),
    ],
)
def test_add_summary_rejects_invalid_provenance_without_write(
    summarized: list[object], preserved: list[object]
) -> None:
    store = _store()
    store._core.ingest = mock.Mock(side_effect=AssertionError("ingest must not run"))

    with pytest.raises(ValueError):
        store.add_summary_message(
            "summary",
            session_id="session-a",
            metadata=_summary_metadata(summarized, preserved),  # type: ignore[arg-type]
        )

    store._core.ingest.assert_not_called()


def test_add_summary_forces_source_and_does_not_duplicate_session_metadata() -> None:
    store = _store()
    source_id = store.add_message("user", "source", session_id="session-a")
    metadata = _summary_metadata([source_id])
    metadata.update({"source": "caller", "session_id": "session-b"})

    summary_id = store.add_summary_message(
        "summary", session_id="session-a", metadata=metadata
    )

    summary = next(message for message in store.get_messages() if message.id == summary_id)
    assert summary.metadata is not None
    assert summary.metadata["source"] == "summarization_middleware"
    assert "session_id" not in summary.metadata


def test_summaries_and_messages_are_isolated_between_sessions() -> None:
    store = _store()
    a_old = store.add_message("user", "a-old", session_id="a")
    b_old = store.add_message("user", "b-old", session_id="b")
    store.add_summary_message("a-summary", session_id="a", metadata=_summary_metadata([a_old]))
    store.add_summary_message("b-summary", session_id="b", metadata=_summary_metadata([b_old]))
    store.add_message("assistant", "a-new", session_id="a")
    store.add_message("assistant", "b-new", session_id="b")

    assert [m.content for m in store.get_messages_with_summary("a")] == ["a-summary", "a-new"]
    assert [m.content for m in store.get_messages_with_summary("b")] == ["b-summary", "b-new"]


def test_latest_valid_summary_is_selected() -> None:
    store = _store()
    first = store.add_message("user", "first", session_id="a")
    store.add_summary_message("older-summary", session_id="a", metadata=_summary_metadata([first]))
    second = store.add_message("user", "second", session_id="a")
    store.add_summary_message(
        "newer-summary", session_id="a", metadata=_summary_metadata([first, second])
    )

    assert [m.content for m in store.get_messages_with_summary("a")] == ["newer-summary"]


def test_later_malformed_summary_is_ignored_in_favor_of_latest_valid() -> None:
    store = _store()
    old = store.add_message("user", "old", session_id="a")
    store.add_summary_message("valid", session_id="a", metadata=_summary_metadata([old]))
    store.add_message("summary", "malformed", metadata={}, session_id="a")
    store.add_message("assistant", "new", session_id="a")

    assert [m.content for m in store.get_messages_with_summary("a")] == ["valid", "new"]


def test_summary_provenance_selects_preserved_and_post_summary_messages() -> None:
    store = _store()
    summarized = store.add_message("user", "summarized", session_id="a")
    preserved = store.add_message("assistant", "preserved", session_id="a")
    store.add_message("user", "old-unrelated", session_id="a")
    store.add_summary_message(
        "summary",
        session_id="a",
        metadata=_summary_metadata([summarized], [preserved]),
    )
    store.add_message("assistant", "post-summary", session_id="a")

    messages = store.get_messages_with_summary("a")

    assert [message.content for message in messages] == ["summary", "preserved", "post-summary"]


def test_summary_context_is_chronological_and_uses_latest_limit_minus_summary() -> None:
    store = _store()
    old = store.add_message("user", "old", session_id="a")
    store.add_summary_message("summary", session_id="a", metadata=_summary_metadata([old]))
    for content in ["new-1", "new-2", "new-3"]:
        store.add_message("assistant", content, session_id="a")

    assert [m.content for m in store.get_messages_with_summary("a", limit=3)] == [
        "summary",
        "new-2",
        "new-3",
    ]


def test_summary_context_limit_one_returns_only_summary() -> None:
    store = _store()
    old = store.add_message("user", "old", session_id="a")
    store.add_summary_message("summary", session_id="a", metadata=_summary_metadata([old]))
    store.add_message("assistant", "new", session_id="a")

    assert [m.content for m in store.get_messages_with_summary("a", limit=1)] == ["summary"]


def test_equal_timestamps_use_insertion_sequence_not_reversed_ids() -> None:
    store = _store()
    _insert_raw_messages(
        store,
        [
            ("source", "user", "source", "a", None),
            ("z-before", "assistant", "before", "a", None),
            ("middle", "summary", "summary", "a", _stored_summary_metadata(["source"])),
            ("a-after", "assistant", "after", "a", None),
        ],
    )

    assert [message.content for message in store.get_messages_with_summary("a")] == [
        "summary",
        "after",
    ]


def test_equal_timestamp_prior_record_is_included_only_when_preserved() -> None:
    store = _store()
    _insert_raw_messages(
        store,
        [
            ("source", "user", "source", "a", None),
            ("z-before", "assistant", "before", "a", None),
            (
                "middle",
                "summary",
                "summary",
                "a",
                _stored_summary_metadata(["source"], ["z-before"]),
            ),
        ],
    )

    assert [message.content for message in store.get_messages_with_summary("a")] == [
        "summary",
        "before",
    ]


def test_scoped_summary_reads_more_than_ten_thousand_rows_without_coremem() -> None:
    store = _store()
    rows = [("preserved", "user", "preserved", "a", None)]
    rows.extend((f"bulk-{index}", "user", f"bulk-{index}", "a", None) for index in range(10001))
    rows.append(
        (
            "latest-summary",
            "summary",
            "summary",
            "a",
            _stored_summary_metadata(["bulk-0"], ["preserved"]),
        )
    )
    _insert_raw_messages(store, rows)
    store._core.fetch = mock.Mock(side_effect=AssertionError("fetch must not run"))
    store._core.fetch_all = mock.Mock(side_effect=AssertionError("fetch_all must not run"))

    messages = store.get_messages_with_summary("a", limit=10)

    assert [message.id for message in messages] == ["latest-summary", "preserved"]


def test_many_newer_malformed_summaries_do_not_hide_older_valid_summary() -> None:
    store = _store()
    source_id = store.add_message("user", "source", session_id="a")
    store.add_summary_message(
        "valid", session_id="a", metadata=_summary_metadata([source_id])
    )
    _insert_raw_messages(
        store,
        [
            (f"malformed-{index}", "summary", "malformed", "a", {})
            for index in range(200)
        ],
    )

    assert [message.content for message in store.get_messages_with_summary("a")] == ["valid"]


def test_add_summary_rejects_missing_and_cross_session_ids() -> None:
    store = _store()
    cross_session_id = store.add_message("user", "cross", session_id="b")

    with pytest.raises(ValueError, match="provenance"):
        store.add_summary_message(
            "missing", session_id="a", metadata=_summary_metadata(["missing"])
        )
    with pytest.raises(ValueError, match="provenance"):
        store.add_summary_message(
            "cross", session_id="a", metadata=_summary_metadata([cross_session_id])
        )


def test_add_summary_rejects_summary_as_preserved_or_invalid_prior_summary() -> None:
    store = _store()
    source_id = store.add_message("user", "source", session_id="a")
    valid_summary_id = store.add_summary_message(
        "valid", session_id="a", metadata=_summary_metadata([source_id])
    )
    malformed_summary_id = store.add_message("summary", "malformed", session_id="a", metadata={})

    with pytest.raises(ValueError, match="provenance"):
        store.add_summary_message(
            "bad preserved",
            session_id="a",
            metadata=_summary_metadata([source_id], [valid_summary_id]),
        )
    with pytest.raises(ValueError, match="provenance"):
        store.add_summary_message(
            "bad summarized",
            session_id="a",
            metadata=_summary_metadata([malformed_summary_id]),
        )


def test_raw_summary_with_missing_cross_session_future_or_summary_preserved_is_invalid() -> None:
    store = _store()
    _insert_raw_messages(
        store,
        [
            ("source", "user", "source", "a", None),
            ("cross", "user", "cross", "b", None),
            ("valid", "summary", "valid", "a", _stored_summary_metadata(["source"])),
            ("missing", "summary", "missing", "a", _stored_summary_metadata(["absent"])),
            ("cross-ref", "summary", "cross", "a", _stored_summary_metadata(["cross"])),
            (
                "wrong-preserved",
                "summary",
                "wrong preserved",
                "a",
                _stored_summary_metadata(["source"], ["valid"]),
            ),
            ("future-ref", "summary", "future", "a", _stored_summary_metadata(["future"])),
            ("future", "assistant", "future record", "a", None),
        ],
    )

    assert store.has_summary("a")
    assert [message.content for message in store.get_messages_with_summary("a")] == [
        "valid",
        "future record",
    ]


def test_repeated_summary_can_summarize_prior_valid_summary() -> None:
    store = _store()
    source_id = store.add_message("user", "source", session_id="a")
    first_summary_id = store.add_summary_message(
        "first", session_id="a", metadata=_summary_metadata([source_id])
    )

    second_summary_id = store.add_summary_message(
        "second", session_id="a", metadata=_summary_metadata([first_summary_id])
    )

    assert second_summary_id
    assert [message.content for message in store.get_messages_with_summary("a")] == ["second"]


def test_no_valid_summary_falls_back_to_latest_non_summary_messages() -> None:
    store = _store()
    for content in ["one", "two"]:
        store.add_message("user", content, session_id="a")
    store.add_message("summary", "legacy", session_id="a")
    store.add_message("assistant", "three", session_id="a")

    assert [m.content for m in store.get_messages_with_summary("a", limit=2)] == ["two", "three"]


def test_unscoped_legacy_summary_never_enters_session_context() -> None:
    store = _store()
    store.add_message("summary", "unscoped legacy")
    store.add_message("user", "scoped", session_id="a")

    assert [m.content for m in store.get_messages_with_summary("a")] == ["scoped"]
    assert not store.has_summary("a")


def test_add_message_with_embedding_uses_supplied_embedding() -> None:
    store = _store()

    rid = store.add_message_with_embedding("user", "precomputed", [1.0] * 384)

    assert rid != ""
    messages = store.get_recent_messages(count=1)
    assert messages[0].content == "precomputed"


def test_add_message_with_embedding_bypasses_default_embedding() -> None:
    store = _store()
    calls: list[str] = []

    original_ingest = store._core.ingest

    def tracking_ingest(role, content, embedding=None, **kw):
        if embedding is not None:
            calls.append("custom_embedding_provided")
        return original_ingest(role, content, embedding=embedding, **kw)

    store._core.ingest = tracking_ingest

    store.add_message_with_embedding("user", "custom-vec", [0.5] * 384)

    assert "custom_embedding_provided" in calls


def test_search_hybrid_basic() -> None:
    store = _store()
    store.add_message("user", "I love building model kits")

    results = store.search_hybrid("model kits")

    assert len(results) > 0
    assert results[0].content == "I love building model kits"


def test_clear_removes_more_than_default_query_limit() -> None:
    store = _store()
    for i in range(100):
        store.add_message("user", f"msg-{i}")

    store.clear()

    assert store.count_messages() == 0


def test_clear_works_when_empty() -> None:
    store = _store()
    store.clear()
    assert store.count_messages() == 0


def test_message_table_has_chronological_indexes() -> None:
    store = _store()

    indexes = store._core._db.raw_query("PRAGMA index_list(messages)")
    names = {idx["name"] for idx in indexes}

    assert "idx_messages_ts" in names
    assert "idx_messages_role" in names
    assert "idx_messages_session_ts" in names
    assert "idx_messages_session_role_ts" in names
    assert "idx_messages_session_rowid" in names


def test_scoped_rowid_limit_query_uses_session_index_without_temp_sort() -> None:
    store = _store()

    with store._core.db._connect() as cur:
        plan = cur.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT rowid, id FROM messages "
            "WHERE session_id = ? ORDER BY rowid DESC LIMIT ?",
            ["session-a", 50],
        ).fetchall()
    details = [str(row[3]) for row in plan]

    assert any("USING INDEX idx_messages_session_rowid" in detail for detail in details)
    assert all("TEMP B-TREE" not in detail for detail in details)


def test_message_store_initializes_when_duckdb_unavailable() -> None:
    from hybriddb import HybridDB

    def disable_duckdb(db):
        db._duckdb_path = ""
        db._duckdb_synced_tables = {}
        db._duckdb_conn = None

    with mock.patch.object(HybridDB, "_init_duckdb", disable_duckdb):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore("test_user", base_dir=temp_dir)
            row_id = store.add_message("user", "hello")

    assert row_id != ""


def test_get_message_store_ignores_workspace_id_for_storage(monkeypatch, tmp_path) -> None:
    messages_storage._stores.clear()
    paths = DataPaths(ea_root=str(tmp_path / "assistant"))
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: paths)

    first = get_message_store("test_user", workspace_id="personal")
    first.add_message("user", "shared storage")
    second = get_message_store("test_user", workspace_id="project-x")

    assert second is first
    assert second.count_messages() == 1
    assert (tmp_path / "assistant" / "Conversation" / "app.db").exists()
    assert not (tmp_path / "assistant" / "Workspaces" / "project-x" / "conversation.app.db").exists()


def test_message_store_uses_stable_user_level_context(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *args, **kwargs):
            return self

    class FakeDB:
        def _connect(self):
            return FakeCursor()

        def register_duckdb_table(self, table_name):
            pass

    class FakeMemoryCore:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.db = FakeDB()

    monkeypatch.setattr(messages_storage, "MemoryCore", FakeMemoryCore)

    store = MessageStore("test_user", base_dir=tmp_path, workspace_id="project-x")

    assert store.workspace_id == "user"
    assert captured["observation_kwargs"] == {"session_id": "user"}


def _create_legacy_workspace_db(
    root,
    workspace_id: str,
    msg_id: str,
    content: str,
    session_id: str = "default",
    user_id: str | None = "test_user",
) -> None:
    legacy_dir = root / "Workspaces" / workspace_id
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "conversation.app.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        "CREATE TABLE messages ("
        "id TEXT PRIMARY KEY, ts TEXT NOT NULL, role TEXT NOT NULL, content TEXT, "
        "metadata TEXT, session_id TEXT, user_id TEXT, agent_id TEXT)"
    )
    conn.execute(
        "INSERT INTO messages (id, ts, role, content, metadata, session_id, user_id, agent_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            msg_id,
            "2026-01-01T00:00:00",
            "user",
            content,
            "{}",
            session_id,
            user_id,
            "",
        ),
    )
    conn.commit()
    conn.close()


def test_message_store_imports_legacy_workspace_conversation_db(monkeypatch, tmp_path) -> None:
    messages_storage._stores.clear()
    root = tmp_path / "assistant"
    _create_legacy_workspace_db(
        root, "project-x", "legacy-1", "legacy workspace message", "legacy-session"
    )

    paths = DataPaths(ea_root=str(root), user_id="test_user")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: paths)

    store = MessageStore("test_user")
    imported = store.get_messages_by_session_id("legacy-project-x-legacy-session", limit=10)
    MessageStore("test_user")

    assert [m.content for m in imported] == ["legacy workspace message"]
    assert store.count_messages() == 1


def test_message_store_imports_only_matching_legacy_user_rows(monkeypatch, tmp_path) -> None:
    messages_storage._stores.clear()
    root = tmp_path / "assistant"
    _create_legacy_workspace_db(root, "project-x", "alice-1", "alice message", user_id="alice")
    _create_legacy_workspace_db(root, "project-y", "bob-1", "bob message", user_id="bob")

    paths = DataPaths(ea_root=str(root), user_id="alice")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: paths)

    store = MessageStore("alice")

    assert [m.content for m in store.get_recent_messages(count=10)] == ["alice message"]


def test_message_store_imports_anonymous_legacy_rows_only_for_default_user(
    monkeypatch, tmp_path
) -> None:
    messages_storage._stores.clear()
    root = tmp_path / "assistant"
    _create_legacy_workspace_db(root, "project-x", "anon-1", "anonymous message", user_id=None)

    alice_paths = DataPaths(ea_root=str(root), user_id="alice")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: alice_paths)
    alice_store = MessageStore("alice")
    assert alice_store.count_messages() == 0

    default_paths = DataPaths(ea_root=str(root), user_id="default_user")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: default_paths)
    default_store = MessageStore("default_user")
    assert [m.content for m in default_store.get_recent_messages(count=10)] == ["anonymous message"]


def test_message_store_imports_overlapping_legacy_ids_without_collapsing_sessions(
    monkeypatch, tmp_path
) -> None:
    messages_storage._stores.clear()
    root = tmp_path / "assistant"
    _create_legacy_workspace_db(root, "alpha", "1", "alpha message")
    _create_legacy_workspace_db(root, "beta", "1", "beta message")

    paths = DataPaths(ea_root=str(root), user_id="test_user")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: paths)

    store = MessageStore("test_user")
    MessageStore("test_user")
    messages = store.get_messages(limit=10)

    assert {m.id for m in messages} == {"legacyws:alpha:1", "legacyws:beta:1"}
    assert {m.content for m in messages} == {"alpha message", "beta message"}
    assert {m.metadata["legacy_id"] for m in messages if m.metadata} == {"1"}
    assert {m.metadata["legacy_workspace_id"] for m in messages if m.metadata} == {"alpha", "beta"}
    assert [m.content for m in store.get_messages_by_session_id("legacy-alpha-default", 10)] == [
        "alpha message"
    ]
    assert [m.content for m in store.get_messages_by_session_id("legacy-beta-default", 10)] == [
        "beta message"
    ]


def test_has_summary_is_scoped_and_requires_valid_provenance() -> None:
    store = _store()
    a_message = store.add_message("user", "a", session_id="a")
    store.add_message("user", "b", session_id="b")
    store.add_summary_message("summary text", session_id="a", metadata=_summary_metadata([a_message]))
    store.add_message("summary", "malformed", metadata={}, session_id="b")

    assert store.has_summary("a")
    assert not store.has_summary("b")


def test_has_summary_false_when_no_summary() -> None:
    store = _store()
    store.add_message("user", "no summary here", session_id="a")

    assert not store.has_summary("a")


def test_search_hybrid_returns_empty_for_empty_query() -> None:
    store = _store()
    store.add_message("user", "something")

    assert store.search_hybrid("") == []


def test_workspace_recent_messages_is_user_level_compatibility() -> None:
    store = _store()
    store.add_message("user", "personal", metadata={"workspace_id": "personal"})
    store.add_message("assistant", "project", metadata={"workspace_id": "project"})

    messages = store.get_recent_messages_for_workspace("personal", count=10)

    assert {m.content for m in messages} == {"personal", "project"}


def test_date_filters_work() -> None:
    store = _store()
    store.add_message("user", "message-1")
    messages = store.get_messages()
    assert len(messages) >= 1
    ts = messages[0].ts

    filtered = store.get_messages(start_date=ts.date())
    assert len(filtered) >= 1

    filtered = store.get_messages(end_date=ts.date())
    assert len(filtered) >= 1

    filtered = store.get_messages(start_date=ts.date() + timedelta(days=1))
    assert len(filtered) == 0


def test_persist_run_stores_final_answer_with_run_id() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    answer = Message(id="", ts=datetime.now(UTC), role="assistant", content="world", session_id="a")
    mid = store.persist_run(
        run_id="run-1",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=[],
        metadata={"model": "test:model"},
    )
    assert mid
    messages = store.get_messages_by_session_id("a", limit=100)
    persisted = [m for m in messages if m.id == mid]
    assert len(persisted) == 1
    assert persisted[0].content == "world"
    assert persisted[0].metadata.get("run_id") == "run-1"


def test_persist_run_stores_audit_records_with_include_in_model_context_false() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    answer = Message(id="", ts=datetime.now(UTC), role="assistant", content="answer", session_id="a")
    audit = [
        Message(id="", ts=datetime.now(UTC), role="reasoning", content="thinking...", session_id="a"),
        Message(id="", ts=datetime.now(UTC), role="tool", content='{"result": "ok"}', session_id="a"),
    ]
    store.persist_run(
        run_id="run-2",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=audit,
        metadata={"model": "test:model"},
    )
    messages = store.get_messages_by_session_id("a", limit=100)
    audit_persisted = [m for m in messages if m.metadata.get("run_id") == "run-2" and m.role != "assistant"]
    assert len(audit_persisted) == 2
    for m in audit_persisted:
        assert m.metadata.get("include_in_model_context") is False


def test_persist_run_is_idempotent_on_run_id() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    answer = Message(id="", ts=datetime.now(UTC), role="assistant", content="answer", session_id="a")
    mid1 = store.persist_run(
        run_id="run-3",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=[],
        metadata={"model": "test:model"},
    )
    mid2 = store.persist_run(
        run_id="run-3",
        session_id="a",
        user_message_id="msg-1",
        final_answer=answer,
        audit_records=[],
        metadata={"model": "test:model"},
    )
    assert mid1 == mid2
    messages = store.get_messages_by_session_id("a", limit=100)
    run_messages = [m for m in messages if m.metadata.get("run_id") == "run-3"]
    assert len(run_messages) == 1


def test_persist_run_rejects_blank_session() -> None:
    store = _store()
    answer = Message(id="", ts=datetime.now(UTC), role="assistant", content="x", session_id="")
    with pytest.raises(ValueError, match="session_id must be nonempty"):
        store.persist_run(
            run_id="run-4",
            session_id="",
            user_message_id="msg-1",
            final_answer=answer,
            audit_records=[],
            metadata={},
        )


def test_get_turns_empty_session() -> None:
    store = _store()
    turns, cursor = store.get_turns("a")
    assert turns == []
    assert cursor is None


def test_get_turns_groups_by_run_id() -> None:
    store = _store()
    store.add_message("user", "hello", metadata={"run_id": "run-1"}, session_id="a")
    store.persist_run(
        run_id="run-1", session_id="a", user_message_id="msg-1",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="hi", session_id="a"),
        audit_records=[], metadata={"model": "test:model"},
    )
    store.add_message("user", "again", metadata={"run_id": "run-2"}, session_id="a")
    store.persist_run(
        run_id="run-2", session_id="a", user_message_id="msg-2",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="ok", session_id="a"),
        audit_records=[], metadata={"model": "test:model"},
    )
    turns, cursor = store.get_turns("a", limit=10)
    assert len(turns) == 2
    assert turns[0]["run_id"] == "run-1"
    assert turns[1]["run_id"] == "run-2"
    assert cursor is None


def test_get_turns_legacy_messages_without_run_id() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    store.add_message("assistant", "hi", session_id="a")
    store.add_message("user", "again", session_id="a")
    store.add_message("assistant", "ok", session_id="a")
    turns, cursor = store.get_turns("a", limit=10)
    assert len(turns) == 2
    assert turns[0]["run_id"] is None
    assert len(turns[0]["messages"]) == 2
    assert turns[1]["run_id"] is None
    assert len(turns[1]["messages"]) == 2
    assert cursor is None


def test_get_turns_cursor_pagination_does_not_split_turns() -> None:
    store = _store()
    store.add_message("user", "q1", metadata={"run_id": "run-1"}, session_id="a")
    store.persist_run(
        run_id="run-1", session_id="a", user_message_id="m1",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="a1", session_id="a"),
        audit_records=[], metadata={},
    )
    store.add_message("user", "q2", metadata={"run_id": "run-2"}, session_id="a")
    store.persist_run(
        run_id="run-2", session_id="a", user_message_id="m2",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="a2", session_id="a"),
        audit_records=[], metadata={},
    )
    store.add_message("user", "q3", metadata={"run_id": "run-3"}, session_id="a")
    store.persist_run(
        run_id="run-3", session_id="a", user_message_id="m3",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="a3", session_id="a"),
        audit_records=[], metadata={},
    )
    turns, cursor = store.get_turns("a", limit=2)
    assert len(turns) == 2
    assert turns[0]["run_id"] == "run-1"
    assert turns[1]["run_id"] == "run-2"
    assert cursor is not None
    turns2, cursor2 = store.get_turns("a", limit=2, cursor=cursor)
    assert len(turns2) == 1
    assert turns2[0]["run_id"] == "run-3"
    assert cursor2 is None


def test_get_turns_metadata_from_final_assistant() -> None:
    store = _store()
    store.add_message("user", "hello", metadata={"run_id": "run-1"}, session_id="a")
    store.persist_run(
        run_id="run-1", session_id="a", user_message_id="m1",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="hi", session_id="a"),
        audit_records=[], metadata={"model": "test:model", "custom": "value"},
    )
    turns, _ = store.get_turns("a", limit=10)
    assert len(turns) == 1
    assert turns[0]["metadata"].get("model") == "test:model"
    assert turns[0]["metadata"].get("custom") == "value"


def test_get_turns_limit_zero() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    turns, cursor = store.get_turns("a", limit=0)
    assert turns == []
    assert cursor is None


def test_get_turns_malformed_cursor_falls_back_to_start() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    store.add_message("assistant", "hi", session_id="a")
    turns, cursor = store.get_turns("a", limit=10, cursor="!!!invalid-base64!!!")
    assert len(turns) == 1
    assert cursor is None


def test_get_turns_empty_cursor_falls_back_to_start() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    store.add_message("assistant", "hi", session_id="a")
    turns, cursor = store.get_turns("a", limit=10, cursor="")
    assert len(turns) == 1
    assert cursor is None


def test_get_turns_mixed_run_id_and_legacy() -> None:
    store = _store()
    # Legacy turn
    store.add_message("user", "q1", session_id="a")
    store.add_message("assistant", "a1", session_id="a")
    # Run-id turn
    store.add_message("user", "q2", metadata={"run_id": "run-1"}, session_id="a")
    store.persist_run(
        run_id="run-1", session_id="a", user_message_id="m1",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="a2", session_id="a"),
        audit_records=[], metadata={},
    )
    turns, cursor = store.get_turns("a", limit=10)
    assert len(turns) == 2
    assert turns[0]["run_id"] is None
    assert turns[1]["run_id"] == "run-1"


def test_get_turns_turn_with_only_user_message() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="a")
    turns, cursor = store.get_turns("a", limit=10)
    assert len(turns) == 1
    assert turns[0]["run_id"] is None
    assert len(turns[0]["messages"]) == 1


def test_get_turns_turn_with_tool_messages() -> None:
    store = _store()
    store.add_message("user", "search", metadata={"run_id": "run-1"}, session_id="a")
    store.add_message("tool", '{"result": "ok"}', metadata={"run_id": "run-1", "tool_name": "web_search"}, session_id="a")
    store.persist_run(
        run_id="run-1", session_id="a", user_message_id="m1",
        final_answer=Message(id="", ts=datetime.now(UTC), role="assistant", content="found it", session_id="a"),
        audit_records=[], metadata={},
    )
    turns, cursor = store.get_turns("a", limit=10)
    assert len(turns) == 1
    assert turns[0]["run_id"] == "run-1"
    assert len(turns[0]["messages"]) == 3
    assert turns[0]["messages"][1].role == "tool"
