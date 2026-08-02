"""Tests for MessageStore (CoreMem adapter)."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from unittest import mock

import pytest
from coremem.types import Memory as CoreMemory

import src.storage.messages as messages_storage
from src.storage.messages import MessageStore, get_message_store
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


def test_message_session_id_is_preserved() -> None:
    store = _store()
    store.add_message("user", "hello", session_id="session-a")

    messages = store.get_messages_by_session_id("session-a")

    assert messages[0].session_id == "session-a"


def test_get_messages_by_session_filters_backend_leakage_after_normalizing_session() -> None:
    store = _store()
    store.add_message("user", "allowed", session_id="session-a")
    store.add_message("user", "leaked", session_id="session-b")
    memories = store._core.fetch_all()
    store._core.fetch = mock.Mock(return_value=memories)

    messages = store.get_messages_by_session_id(" session-a ")

    assert [message.content for message in messages] == ["allowed"]
    store._core.fetch.assert_called_once_with(limit=50, session_id="session-a")


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
    metadata = _summary_metadata(["message-1"])
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


def test_equal_timestamp_id_after_summary_is_included() -> None:
    store = _store()
    instant = datetime(2026, 8, 3, 12, tzinfo=UTC)
    summary = CoreMemory(
        id="middle",
        content="summary",
        role="summary",
        ts=instant,
        session_id="a",
        metadata={
            "source": "summarization_middleware",
            **_summary_metadata(["summarized"]),
        },
    )
    after = CoreMemory(
        id="z-after",
        content="after",
        role="assistant",
        ts=instant.astimezone(timezone(timedelta(hours=1))),
        session_id="a",
    )
    store._scoped_memories = mock.Mock(return_value=[after, summary])

    assert [message.content for message in store.get_messages_with_summary("a")] == [
        "summary",
        "after",
    ]


def test_equal_timestamp_id_before_summary_is_excluded_unless_preserved() -> None:
    store = _store()
    instant = datetime(2026, 8, 3, 12, tzinfo=UTC)
    before = CoreMemory(
        id="a-before",
        content="before",
        role="assistant",
        ts=instant,
        session_id="a",
    )
    summary = CoreMemory(
        id="middle",
        content="summary",
        role="summary",
        ts=instant,
        session_id="a",
        metadata={
            "source": "summarization_middleware",
            **_summary_metadata(["summarized"]),
        },
    )
    store._scoped_memories = mock.Mock(return_value=[summary, before])

    assert [message.content for message in store.get_messages_with_summary("a")] == ["summary"]

    summary.metadata["preserved_message_ids"] = ["a-before"]
    assert [message.content for message in store.get_messages_with_summary("a")] == [
        "summary",
        "before",
    ]


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
