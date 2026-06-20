"""Tests for the in-memory suggestion store."""

from src.http.suggestion_store import clear, get_suggestion, remove_suggestion, store_suggestion


class TestSuggestionStore:
    """Tests for store/get/remove/clear operations."""

    def teardown_method(self):
        clear()

    def test_store_and_get(self):
        store_suggestion("sug_1", "new content", "/test.py", from_=1, to=5)
        sug = get_suggestion("sug_1")
        assert sug is not None
        assert sug["content"] == "new content"
        assert sug["file_path"] == "/test.py"
        assert sug["from"] == 1
        assert sug["to"] == 5
        assert "created_at" in sug

    def test_get_nonexistent(self):
        sug = get_suggestion("nonexistent")
        assert sug is None

    def test_remove(self):
        store_suggestion("sug_1", "content", "/test.py")
        remove_suggestion("sug_1")
        sug = get_suggestion("sug_1")
        assert sug is None

    def test_remove_nonexistent(self):
        remove_suggestion("nonexistent")

    def test_clear(self):
        store_suggestion("sug_1", "content a", "/a.py")
        store_suggestion("sug_2", "content b", "/b.py")
        clear()
        assert get_suggestion("sug_1") is None
        assert get_suggestion("sug_2") is None

    def test_multiple_suggestions(self):
        store_suggestion("sug_1", "content a", "/a.py")
        store_suggestion("sug_2", "content b", "/b.py")
        sug1 = get_suggestion("sug_1")
        sug2 = get_suggestion("sug_2")
        assert sug1 is not None
        assert sug2 is not None
        assert sug1["content"] == "content a"
        assert sug2["content"] == "content b"

    def test_default_from_to(self):
        store_suggestion("sug_1", "content", "/test.py")
        sug = get_suggestion("sug_1")
        assert sug["from"] == -1
        assert sug["to"] == -1

    def test_overwrite_existing(self):
        store_suggestion("sug_1", "old content", "/old.py")
        store_suggestion("sug_1", "new content", "/new.py")
        sug = get_suggestion("sug_1")
        assert sug["content"] == "new content"
        assert sug["file_path"] == "/new.py"

    def test_eviction_when_full(self, monkeypatch):
        monkeypatch.setattr("src.http.suggestion_store._MAX_SUGGESTIONS", 5)
        for i in range(5):
            store_suggestion(f"sug_{i}", f"content_{i}", "/test.py")
        store_suggestion("new_sug", "new content", "/new.py")
        assert get_suggestion("sug_0") is None
        assert get_suggestion("sug_1") is not None
        assert get_suggestion("new_sug") is not None
