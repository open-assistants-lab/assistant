"""Tests for GET /sessions endpoint."""
import tempfile

from src.storage.messages import MessageStore


class TestListSessionsStore:
    """Tests for MessageStore.list_sessions()."""

    def test_list_sessions_empty(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        sessions = store.list_sessions()
        assert sessions == []

    def test_list_sessions_with_messages(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        store.add_message("user", "hello", session_id="session-1")
        store.add_message("assistant", "hi", session_id="session-1")
        store.add_message("user", "second", session_id="session-2")
        store.add_message("user", "third", session_id="session-3")

        sessions = store.list_sessions()
        assert len(sessions) == 3

        ids = {s["session_id"] for s in sessions}
        assert ids == {"session-1", "session-2", "session-3"}

    def test_list_sessions_orders_by_last_updated_desc(self):
        import time

        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        store.add_message("user", "old", session_id="old-session")
        time.sleep(0.01)
        store.add_message("user", "new", session_id="new-session")

        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == "new-session"
        assert sessions[1]["session_id"] == "old-session"

    def test_list_sessions_includes_message_count(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        store.add_message("user", "a", session_id="s1")
        store.add_message("assistant", "b", session_id="s1")
        store.add_message("user", "c", session_id="s1")

        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["message_count"] == 3

    def test_list_sessions_includes_last_message_preview(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        store.add_message("user", "hello world", session_id="s1")
        store.add_message("assistant", "hi there", session_id="s1")
        store.add_message("user", "how are you", session_id="s2")

        sessions = store.list_sessions()
        s1 = next(s for s in sessions if s["session_id"] == "s1")
        assert s1["last_message"] == "hello world"
        assert s1["last_updated"] is not None
        assert s1["created_at"] is not None

    def test_list_sessions_respects_limit(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        for i in range(10):
            store.add_message("user", f"msg-{i}", session_id=f"session-{i}")

        sessions = store.list_sessions(limit=3)
        assert len(sessions) == 3

    def test_list_sessions_no_session_id_ignored(self):
        store = MessageStore("test_user", base_dir=tempfile.mkdtemp())
        store.add_message("user", "no session")
        store.add_message("user", "has session", session_id="real-session")

        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "real-session"


class TestSessionsEndpoint:
    """Tests for GET /sessions."""

    def test_get_sessions_default_user(self, client):
        r = client.get("/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_get_sessions_with_user_id(self, client, test_user_id):
        r = client.get("/sessions", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data

    def test_get_sessions_with_messages(self, client, test_user_id, conversation_messages):
        from src.storage.messages import get_message_store

        store = get_message_store(test_user_id)
        for msg in conversation_messages:
            store.add_message(msg["role"], msg["content"], session_id="test-session")

        r = client.get("/sessions", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert len(data["sessions"]) > 0

    def test_get_sessions_response_schema(self, client, test_user_id):
        r = client.get("/sessions", params={"user_id": test_user_id})
        data = r.json()
        for s in data["sessions"]:
            assert "session_id" in s
            assert "message_count" in s
            assert "last_updated" in s
            assert "created_at" in s
            assert "last_message" in s
