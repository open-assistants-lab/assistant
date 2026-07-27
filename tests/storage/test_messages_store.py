"""Tests for MessageStore (CoreMem adapter)."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import timedelta
from unittest import mock

import src.storage.messages as messages_storage
from src.storage.messages import MessageStore, get_message_store
from src.storage.paths import DataPaths


def _store() -> MessageStore:
    temp_dir = tempfile.TemporaryDirectory()
    store = MessageStore("test_user", base_dir=temp_dir.name)
    store._temp_dir = temp_dir
    return store


def test_get_messages_with_summary_respects_limit() -> None:
    store = _store()
    store.add_message("user", "before")
    store.add_summary_message("summary")
    for i in range(5):
        store.add_message("user", f"after-{i}")

    messages = store.get_messages_with_summary(limit=2)

    assert [m.content for m in messages] == ["summary", "after-4"]


def test_get_messages_with_summary_zero_limit_returns_empty() -> None:
    store = _store()
    store.add_summary_message("summary")

    assert store.get_messages_with_summary(limit=0) == []


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


def test_has_summary_true() -> None:
    store = _store()
    store.add_summary_message("summary text")

    assert store.has_summary()


def test_has_summary_false_when_no_summary() -> None:
    store = _store()
    store.add_message("user", "no summary here")

    assert not store.has_summary()


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


def test_messages_with_summary_ignores_workspace_id_compatibility() -> None:
    store = _store()
    store.add_summary_message("summary")
    store.add_message("user", "personal", metadata={"workspace_id": "personal"})
    store.add_message("assistant", "project", metadata={"workspace_id": "project"})

    messages = store.get_messages_with_summary(limit=10, workspace_id="personal")

    assert {m.content for m in messages} == {"summary", "personal", "project"}


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
