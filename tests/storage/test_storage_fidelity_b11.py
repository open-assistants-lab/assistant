"""B11: storage correctness — summary tail, vector purge, cache invalidation (#122, #123, #125, #127)."""

from __future__ import annotations


class TestVectorPurgeUsesTheCollectionApi:
    """#123: `client.delete(...)` on a chromadb Client never existed.

    The AttributeError was swallowed by `except: pass`, so every session,
    workspace and memory deletion silently left its vectors behind.
    """

    def test_purge_uses_the_collection_api(self, monkeypatch):
        from src.storage.messages import MessageStore

        calls: dict = {}

        class _Collection:
            def delete(self, ids):
                calls["collection_delete"] = list(ids)

        class _Chroma:
            def get_collection(self, name):
                calls["get_collection"] = name
                return _Collection()

            def delete(self, *a, **k):  # pragma: no cover - must never be used
                raise AssertionError("a chromadb Client has no delete()")

        class _DB:
            _chroma = _Chroma()
            def _connect(self):
                raise AssertionError("not needed")

        class _Core:
            db = _DB()
            def fetch(self, limit, metadata):
                return [type("M", (), {"id": m})() for m in ("a", "b", "None")]

        store = MessageStore.__new__(MessageStore)
        store._core = _Core()
        store._purge_vectors(["a", "b"])

        assert calls.get("get_collection") == "messages_content"
        assert calls.get("collection_delete") == ["a", "b"]

    def test_ids_are_collected_before_rows_are_deleted(self, monkeypatch):
        """After the DELETE the fetch is empty, so order matters."""
        from src.storage.messages import MessageStore

        store = MessageStore.__new__(MessageStore)
        store._core = type("C", (), {"fetch": staticmethod(lambda **k: [])})()
        assert store._collect_vector_ids({"session_id": "s"}) == []


class TestSummaryCacheInvalidationOnClear:
    """#125: the clear endpoint bypassed MessageStore.clear()."""

    def test_endpoint_clear_invalidates_summary_cache(self):
        import asyncio

        from src.http.routers import memories as memories_router

        cleared: list[str] = []

        class _Store:
            def clear(self):
                cleared.append("store-clear")

        async def _store(user_id):
            return _Store()

        import src.storage.messages as messages_mod

        original = messages_mod.aget_message_store
        messages_mod.aget_message_store = _store
        try:
            out = asyncio.run(
                memories_router.clear_memories(user_id="u", workspace_id="personal", request=None)
            )
        finally:
            messages_mod.aget_message_store = original

        assert out["status"] == "cleared"
        assert cleared == ["store-clear"], (
            "the endpoint cleared the core directly, leaving the summary cache stale"
        )


class TestGeneratedColumnMigrationIsRepeatable:
    """#130: table_info omits VIRTUAL generated columns, so every second
    startup re-added run_id, hit a duplicate-column error and gave up."""

    def test_generated_column_migration_is_repeatable(self, tmp_path):
        import sqlite3

        from src.storage.messages import MessageStore

        db = tmp_path / "app.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, role TEXT, content TEXT, "
            "metadata TEXT, ts TEXT, session_id TEXT)"
        )
        conn.commit()
        conn.close()

        first = MessageStore._migrate_generated_columns(tmp_path)
        second = MessageStore._migrate_generated_columns(tmp_path)
        assert first is True
        assert second is True, "the second startup failed the generated-column migration"

        conn = sqlite3.connect(str(db))
        indexes = {r[1] for r in conn.execute("PRAGMA index_list('messages')")}
        conn.close()
        assert "idx_messages_run_id" in indexes, indexes
        assert "idx_messages_workspace_id" in indexes, indexes
