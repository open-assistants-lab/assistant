"""Message tool workspace compatibility tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.sdk.tools_core import message as message_tools
from src.storage.messages import MessageStore, SearchResult


class RecordingCore:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def search_enhanced(self, query: str, limit: int, **kwargs):
        self.calls.append({"query": query, "limit": limit, **kwargs})
        return []


class RecordingConversation:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def search_hybrid(self, query: str, limit: int, **kwargs):
        self.calls.append({"query": query, "limit": limit, **kwargs})
        return [
            SearchResult(
                id="msg-1",
                content="Alpha project",
                ts=datetime.now(UTC),
                role="user",
                score=1.0,
            )
        ]

    def _connect(self):
        class Conn:
            def execute(self, *args, **kwargs):
                return self

            def fetchall(self):
                return []

            def close(self):
                pass

        return Conn()


class EmptyConversation:
    def get_messages(self, **kwargs):
        return []

    def count_messages(self):
        return 0


def test_message_search_ignores_workspace_id_for_search_metadata(monkeypatch):
    core = RecordingCore()
    monkeypatch.setattr(message_tools, "_get_message_core", lambda *args, **kwargs: core)
    monkeypatch.setattr(
        message_tools, "get_message_store", lambda *args, **kwargs: RecordingConversation()
    )

    result = message_tools.message_search.invoke(
        {"query": "alpha", "user_id": "test_user", "workspace_id": "project"}
    )

    assert "No messages found" in result
    assert core.calls == [{"query": "alpha", "limit": 10}]


def test_message_history_empty_store_does_not_mention_workspace(monkeypatch):
    monkeypatch.setattr(message_tools, "get_message_store", lambda *args, **kwargs: EmptyConversation())

    result = message_tools.message_history.invoke(
        {"user_id": "test_user", "workspace_id": "project"}
    )

    assert "No persisted messages found" in result
    assert "conversation store" in result
    assert "workspace" not in result.lower()


def test_message_history_empty_date_does_not_mention_workspace(monkeypatch):
    monkeypatch.setattr(message_tools, "get_message_store", lambda *args, **kwargs: EmptyConversation())

    result = message_tools.message_history.invoke(
        {"date_str": "2026-01-01", "user_id": "test_user", "workspace_id": "project"}
    )

    assert "No persisted messages found" in result
    assert "session context" in result
    assert "workspace" not in result.lower()


def test_message_count_ignores_workspace_id_for_search_metadata(monkeypatch):
    conversation = RecordingConversation()
    monkeypatch.setattr(message_tools, "expand_queries", lambda query, llm_provider=None: [query])
    monkeypatch.setattr(message_tools, "_try_create_llm_provider", lambda: None)
    monkeypatch.setattr(message_tools, "get_message_store", lambda *args, **kwargs: conversation)

    message_tools.message_count.invoke(
        {"query": "alpha", "user_id": "test_user", "workspace_id": "project"}
    )

    assert conversation.calls == [{"query": "alpha", "limit": 100}]


def test_message_count_groups_by_message_session_id_column(monkeypatch):
    class SessionColumnConversation:
        def search_hybrid(self, query: str, limit: int, **kwargs):
            return [
                SearchResult(
                    id="msg-1",
                    content="Alpha Project",
                    ts=datetime.now(UTC),
                    role="user",
                    score=1.0,
                ),
                SearchResult(
                    id="msg-2",
                    content="Beta Project",
                    ts=datetime.now(UTC),
                    role="assistant",
                    score=1.0,
                ),
            ]

        def _connect(self):
            class Conn:
                def execute(self, *args, **kwargs):
                    return self

                def fetchall(self):
                    return [("msg-1", "{}", "chat-alpha"), ("msg-2", "{}", "chat-beta")]

                def close(self):
                    pass

            return Conn()

    monkeypatch.setattr(message_tools, "expand_queries", lambda query, llm_provider=None: [query])
    monkeypatch.setattr(message_tools, "_try_create_llm_provider", lambda: None)
    monkeypatch.setattr(
        message_tools, "get_message_store", lambda *args, **kwargs: SessionColumnConversation()
    )

    result = message_tools.message_count.invoke({"query": "projects", "user_id": "test_user"})

    assert "Analyzed 2 sessions" in result
    assert "Alpha Project" in result
    assert "Beta Project" in result


def test_fetch_session_ids_uses_real_message_store_core_db(tmp_path):
    store = MessageStore("message_tool_session_test", base_dir=tmp_path / "conversation")
    store.clear()
    msg_id = store.add_message("user", "Alpha Project", session_id="chat-alpha")

    result = message_tools._fetch_session_ids(store, [msg_id])

    assert result == {msg_id: "chat-alpha"}


def test_message_timeline_ignores_workspace_id_for_search_metadata(monkeypatch):
    core = RecordingCore()
    core.search_enhanced = lambda query, limit, **kwargs: core.calls.append(
        {"query": query, "limit": limit, **kwargs}
    ) or [
        SimpleNamespace(
            memory=SimpleNamespace(
                session_id="session-1",
                ts=datetime(2026, 1, 1, tzinfo=UTC),
                content="Alpha project started",
            )
        )
    ]
    monkeypatch.setattr(message_tools, "_get_message_core", lambda *args, **kwargs: core)

    result = message_tools.message_timeline.invoke(
        {"query": "alpha", "user_id": "test_user", "workspace_id": "project"}
    )

    assert "Alpha project started" in result
    assert core.calls == [{"query": "alpha", "limit": 20}]
