"""Tests for HybridDB: SQLite + FTS5 + ChromaDB with self-healing journal."""

import hashlib
import os
import shutil
import struct
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from hybriddb import HybridDB, SearchMode
from hybriddb.embedding import EMBEDDING_DIM
from hybriddb.utils import (
    _CHROMA_INDEX_MAX_ELEMENTS,
    _CHROMA_INDEX_MAX_M0,
    _sanitize_fts_query,
)


def _mock_embedding(text: str) -> list[float]:
    if not text:
        return [0.0] * EMBEDDING_DIM
    words = str(text).lower().split()
    dim = EMBEDDING_DIM
    embedding = [0.0] * dim
    for word in words:
        h = int(hashlib.md5(word.encode()).hexdigest(), 16) % dim
        embedding[h] += 1.0
    mag = sum(x**2 for x in embedding) ** 0.5
    if mag > 0:
        embedding = [x / mag for x in embedding]
    return embedding


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db(tmp_dir):
    return HybridDB(tmp_dir, embedding_fn=_mock_embedding)


@pytest.fixture
def db_with_contacts(db):
    db.create_table(
        "contacts",
        {
            "first_name": "TEXT",
            "last_name": "TEXT",
            "company": "LONGTEXT",
            "notes": "LONGTEXT",
            "clv": "REAL",
            "is_active": "BOOLEAN",
        },
    )
    return db


class TestSanitizeFtsQuery:
    def test_basic(self):
        assert _sanitize_fts_query("hello world") == "hello OR world"

    def test_special_chars(self):
        result = _sanitize_fts_query("what's the <best>?")
        assert "<" not in result
        assert ">" not in result

    def test_empty(self):
        assert _sanitize_fts_query("") == ""
        assert _sanitize_fts_query("   ") == ""

    def test_punctuation_only(self):
        assert _sanitize_fts_query("!!! ???") == ""


class TestCreateTable:
    def test_basic(self, db):
        db.create_table(
            "items",
            {
                "name": "TEXT",
                "description": "LONGTEXT",
                "count": "INTEGER",
            },
        )
        schema = db.get_schema("items")
        assert "name" in schema
        assert schema["name"] == "TEXT"
        assert schema["description"] == "LONGTEXT"
        assert schema["count"] == "INTEGER"

    def test_list_tables(self, db):
        db.create_table("t1", {"name": "TEXT"})
        db.create_table("t2", {"val": "INTEGER"})
        tables = db.list_tables()
        assert "t1" in tables
        assert "t2" in tables
        assert "_journal" not in tables
        assert "_schema" not in tables

    def test_list_tables_excludes_fts_shadow_tables(self, db):
        db.create_table("contacts", {"name": "TEXT", "notes": "LONGTEXT"})

        assert db.list_tables() == ["contacts"]

    def test_all_types(self, db):
        db.create_table(
            "all_types",
            {
                "a_text": "TEXT",
                "a_longtext": "LONGTEXT",
                "an_int": "INTEGER",
                "a_real": "REAL",
                "a_bool": "BOOLEAN",
                "a_json": "JSON",
            },
        )
        schema = db.get_schema("all_types")
        assert schema["a_text"] == "TEXT"
        assert schema["a_longtext"] == "LONGTEXT"
        assert schema["an_int"] == "INTEGER"
        assert schema["a_real"] == "REAL"
        assert schema["a_bool"] == "BOOLEAN"
        assert schema["a_json"] == "JSON"

    def test_duplicate_create(self, db):
        db.create_table("dup", {"name": "TEXT"})
        db.create_table("dup", {"name": "TEXT"})
        assert "dup" in db.list_tables()




class TestCRUD:
    def test_insert_and_get(self, db_with_contacts):
        row_id = db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "company": "Acme Corp",
                "notes": "VIP client",
                "clv": 5000.0,
                "is_active": True,
            },
        )
        assert row_id > 0
        row = db_with_contacts.get("contacts", row_id)
        assert row is not None
        assert row["first_name"] == "Alice"
        assert row["clv"] == 5000.0
        assert row["is_active"] == 1

    def test_insert_batch(self, db_with_contacts):
        ids = db_with_contacts.insert_batch(
            "contacts",
            [
                {"first_name": "Alice", "company": "Acme"},
                {"first_name": "Bob", "company": "Beta Corp"},
                {"first_name": "Charlie", "notes": "New hire"},
            ],
        )
        assert len(ids) == 3
        assert all(iid > 0 for iid in ids)

    def test_insert_batch_uses_single_timestamp_for_journal_entries(self, db_with_contacts):
        with mock.patch("hybriddb.crud._now_iso", return_value="2026-01-01T00:00:00+00:00") as now:
            db_with_contacts.insert_batch(
                "contacts",
                [
                    {"first_name": "Alice", "company": "Acme", "notes": "VIP"},
                    {"first_name": "Bob", "company": "Beta", "notes": "New"},
                ],
                sync=False,
            )

        assert now.call_count == 1

    def test_insert_can_skip_selected_longtext_journal_columns(self, db_with_contacts):
        db_with_contacts.insert(
            "contacts",
            {"first_name": "Alice", "company": "Acme", "notes": "VIP"},
            sync=False,
            skip_journal_columns={"notes"},
        )

        rows = db_with_contacts.raw_query(
            "SELECT column_name, op FROM _journal WHERE app_table = 'contacts' ORDER BY id"
        )

        assert {row["column_name"] for row in rows if row["op"] == "add"} == {"company"}

    def test_update(self, db_with_contacts):
        row_id = db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "company": "Acme",
                "clv": 1000.0,
            },
        )
        ok = db_with_contacts.update("contacts", row_id, {"clv": 2000.0, "first_name": "Alice2"})
        assert ok
        row = db_with_contacts.get("contacts", row_id)
        assert row["clv"] == 2000.0
        assert row["first_name"] == "Alice2"

    def test_update_nonexistent(self, db_with_contacts):
        ok = db_with_contacts.update("contacts", 99999, {"first_name": "Nope"})
        assert not ok

    def test_delete(self, db_with_contacts):
        row_id = db_with_contacts.insert("contacts", {"first_name": "ToDelete", "company": " Gone"})
        ok = db_with_contacts.delete("contacts", row_id)
        assert ok
        assert db_with_contacts.get("contacts", row_id) is None

    def test_delete_nonexistent(self, db_with_contacts):
        ok = db_with_contacts.delete("contacts", 99999)
        assert not ok

    def test_query(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "clv": 1000.0})
        db_with_contacts.insert("contacts", {"first_name": "Bob", "clv": 2000.0})
        results = db_with_contacts.query("contacts", where="clv > 1500.0")
        assert len(results) == 1
        assert results[0]["first_name"] == "Bob"

    def test_count(self, db_with_contacts):
        db_with_contacts.insert_batch(
            "contacts",
            [
                {"first_name": "A", "clv": 100.0},
                {"first_name": "B", "clv": 200.0},
                {"first_name": "C", "clv": 300.0},
            ],
        )
        assert db_with_contacts.count("contacts") == 3
        assert db_with_contacts.count("contacts", where="clv > 150.0") == 2


class TestInsertSync:
    def test_sync_false_defers_chroma(self, db_with_contacts):
        db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "notes": "Important notes here",
            },
            sync=False,
        )
        assert db_with_contacts._journal_count("contacts") > 0
        db_with_contacts.process_journal()
        assert db_with_contacts._journal_count("contacts") == 0


class TestSchemaOperations:
    def test_add_column(self, db_with_contacts):
        db_with_contacts.add_column("contacts", "region", "TEXT")
        schema = db_with_contacts.get_schema("contacts")
        assert "region" in schema
        assert schema["region"] == "TEXT"

    def test_add_longtext_column(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "old notes"})
        db_with_contacts.add_column("contacts", "bio", "LONGTEXT")
        schema = db_with_contacts.get_schema("contacts")
        assert schema["bio"] == "LONGTEXT"

    def test_drop_and_rename_longtext_with_chroma_disabled_do_not_log_chroma_errors(self, tmp_dir):
        db = HybridDB(tmp_dir, embedding_fn=_mock_embedding, max_chroma_index_gb=0)
        db.create_table("docs", {"title": "TEXT", "body": "LONGTEXT"})

        with mock.patch("hybriddb.schema.logger") as mock_logger:
            db.rename_column("docs", "body", "content")
            db.drop_column("docs", "content")

        logged_events = [call.args[0] for call in mock_logger.warning.call_args_list if call.args]
        assert "hybriddb.rename_chroma_failed" not in logged_events

    def test_drop_column(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "clv": 100.0})
        db_with_contacts.drop_column("contacts", "clv")
        schema = db_with_contacts.get_schema("contacts")
        assert "clv" not in schema
        assert "first_name" in schema

    def test_drop_nonexistent_raises(self, db_with_contacts):
        with pytest.raises(ValueError):
            db_with_contacts.drop_column("contacts", "nonexistent")

    def test_rename_column(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "clv": 100.0})
        db_with_contacts.rename_column("contacts", "clv", "customer_lifetime_value")
        schema = db_with_contacts.get_schema("contacts")
        assert "clv" not in schema
        assert "customer_lifetime_value" in schema
        assert schema["customer_lifetime_value"] == "REAL"

    def test_rename_nonexistent_raises(self, db_with_contacts):
        with pytest.raises(ValueError):
            db_with_contacts.rename_column("contacts", "nonexistent", "something")


class TestSearch:
    def test_keyword_search(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "company": "Acme Corp"})
        db_with_contacts.insert("contacts", {"first_name": "Bob", "company": "Beta Inc"})
        results = db_with_contacts.search("contacts", "company", "Acme", mode=SearchMode.KEYWORD)
        assert len(results) >= 1
        found = any(r["company"] == "Acme Corp" for r in results)
        assert found

    def test_semantic_search(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"notes": "key decision maker for enterprise deals"})
        db_with_contacts.insert("contacts", {"notes": "prefers morning meetings and tea"})
        results = db_with_contacts.search(
            "contacts", "notes", "executive choices", mode=SearchMode.SEMANTIC
        )
        assert isinstance(results, list)

    def test_hybrid_search(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"notes": "key decision maker for enterprise deals"})
        db_with_contacts.insert("contacts", {"notes": "handles morning standup meetings"})
        results = db_with_contacts.search("contacts", "notes", "decision", mode=SearchMode.HYBRID)
        assert isinstance(results, list)
        for r in results:
            assert "_score" in r

    def test_search_text_column_keyword_only(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "last_name": "Smith"})
        results = db_with_contacts.search(
            "contacts", "first_name", "Alice", mode=SearchMode.KEYWORD
        )
        assert len(results) >= 1

    def test_search_text_column_no_semantic(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice"})
        results = db_with_contacts.search(
            "contacts", "first_name", "Alice", mode=SearchMode.SEMANTIC
        )
        assert results == []

    def test_search_all(self, db_with_contacts):
        db_with_contacts.insert(
            "contacts",
            {
                "company": "Acme Corp builds rockets",
                "notes": "key decision maker for space projects",
            },
        )
        results = db_with_contacts.search_all("contacts", "rockets")
        assert isinstance(results, list)

    def test_search_with_recency(self, db):
        db.create_table(
            "messages",
            {
                "role": "TEXT",
                "content": "LONGTEXT",
                "ts": "TEXT",
            },
        )
        from datetime import UTC, datetime, timedelta

        old_ts = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        new_ts = datetime.now(UTC).isoformat()

        db.insert("messages", {"role": "user", "content": "old message about python", "ts": old_ts})
        db.insert("messages", {"role": "user", "content": "new message about python", "ts": new_ts})

        results = db.search(
            "messages", "content", "python", recency_weight=0.3, recency_column="ts"
        )
        assert len(results) >= 2
        for r in results:
            assert "_score" in r

    def test_search_empty_query(self, db_with_contacts):
        results = db_with_contacts.search("contacts", "company", "", mode=SearchMode.KEYWORD)
        assert results == []

    def test_search_nonexistent_table(self, db):
        results = db.search("nonexistent", "col", "query")
        assert results == []


class TestJournal:
    def test_journal_auto_processes_on_insert(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"})
        assert db_with_contacts._journal_count("contacts") == 0

    def test_journal_deferred_with_sync_false(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"}, sync=False)
        count = db_with_contacts._journal_count("contacts")
        assert count > 0

    def test_process_journal(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"}, sync=False)
        processed = db_with_contacts.process_journal()
        assert processed > 0
        assert db_with_contacts._journal_count("contacts") == 0

    def test_journal_status(self, db_with_contacts):
        status = db_with_contacts.journal_status()
        assert "pending" in status
        assert "failed" in status


class TestHealthAndReconcile:
    def test_health_ok(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"})
        h = db_with_contacts.health("contacts")
        assert h["status"] == "ok"
        assert h["sqlite_rows"] == 1
        assert "contacts_notes" in h["chroma_docs"]

    def test_health_drift_with_pending(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"}, sync=False)
        h = db_with_contacts.health("contacts")
        assert h["status"] in ("ok", "drift")

    def test_reconcile(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"})
        result = db_with_contacts.reconcile("contacts")
        assert "ghosts_deleted" in result
        assert "missing_added" in result
        assert "metadata_updated" in result

    def test_reconcile_batches_missing_row_metadata(self, db_with_contacts):
        db_with_contacts.insert("contacts", {"first_name": "Alice", "notes": "test"})
        collection = db_with_contacts._get_collection("contacts_notes")
        collection.delete(ids=["1"])

        with mock.patch.object(db_with_contacts, "get", wraps=db_with_contacts.get) as mock_get:
            result = db_with_contacts.reconcile("contacts")

        assert result["missing_added"] == 1
        assert mock_get.call_count == 0


class TestMetadataStrategy:
    def test_text_in_metadata(self, db_with_contacts):
        row_id = db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "company": "Acme",
                "notes": "VIP",
            },
        )
        row = db_with_contacts.get("contacts", row_id)
        meta = db_with_contacts._row_to_metadata("contacts", row)
        assert "first_name" in meta
        assert meta["first_name"] == "Alice"

    def test_longtext_not_in_metadata(self, db_with_contacts):
        row_id = db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "company": "Acme",
                "notes": "VIP",
            },
        )
        row = db_with_contacts.get("contacts", row_id)
        meta = db_with_contacts._row_to_metadata("contacts", row)
        assert "company" not in meta
        assert "notes" not in meta

    def test_json_not_in_metadata(self, db):
        db.create_table("items", {"name": "TEXT", "meta": "JSON", "desc": "LONGTEXT"})
        row_id = db.insert("items", {"name": "test", "meta": '{"k":"v"}', "desc": "description"})
        row = db.get("items", row_id)
        meta = db._row_to_metadata("items", row)
        assert "meta" not in meta

    def test_null_omitted_from_metadata(self, db_with_contacts):
        row_id = db_with_contacts.insert("contacts", {"first_name": "Alice", "clv": None})
        row = db_with_contacts.get("contacts", row_id)
        meta = db_with_contacts._row_to_metadata("contacts", row)
        assert "clv" not in meta

    def test_boolean_in_metadata(self, db_with_contacts):
        row_id = db_with_contacts.insert(
            "contacts",
            {
                "first_name": "Alice",
                "is_active": True,
            },
        )
        row = db_with_contacts.get("contacts", row_id)
        meta = db_with_contacts._row_to_metadata("contacts", row)
        assert "is_active" in meta
        assert meta["is_active"] is True


class TestConversationSchema:
    def test_messages_table(self, db):
        db.create_table(
            "messages",
            {
                "ts": "TEXT NOT NULL",
                "role": "TEXT NOT NULL",
                "content": "LONGTEXT",
                "metadata": "JSON",
            },
        )
        schema = db.get_schema("messages")
        assert schema["content"] == "LONGTEXT"
        assert schema["metadata"] == "JSON"

        row_id = db.insert(
            "messages",
            {
                "ts": "2026-01-01T00:00:00Z",
                "role": "user",
                "content": "Hello world",
            },
        )
        row = db.get("messages", row_id)
        assert row["role"] == "user"

        results = db.search("messages", "content", "Hello", mode=SearchMode.KEYWORD)
        assert len(results) >= 1


class TestMemorySchema:
    def test_memories_table(self, db):
        db.create_table(
            "memories",
            {
                "id": "TEXT PRIMARY KEY",
                "trigger": "LONGTEXT",
                "action": "LONGTEXT",
                "confidence": "REAL",
                "domain": "TEXT",
                "structured_data": "LONGTEXT",
                "linked_to": "JSON",
            },
        )
        schema = db.get_schema("memories")
        assert schema["trigger"] == "LONGTEXT"
        assert schema["action"] == "LONGTEXT"
        assert schema["linked_to"] == "JSON"

        row_id = db.insert(
            "memories",
            {
                "id": "abc123",
                "trigger": "user likes python",
                "action": "suggest python tools",
                "confidence": 0.8,
                "domain": "preferences",
                "structured_data": "{}",
            },
        )
        row = db.get("memories", row_id)
        assert row["domain"] == "preferences"

    def test_insights_table(self, db):
        db.create_table(
            "insights",
            {
                "id": "TEXT PRIMARY KEY",
                "summary": "LONGTEXT",
                "domain": "TEXT",
                "confidence": "REAL",
            },
        )
        db.insert(
            "insights",
            {
                "id": "i1",
                "summary": "User prefers morning meetings",
                "domain": "preferences",
                "confidence": 0.5,
            },
        )
        results = db.search("insights", "summary", "morning meetings")
        assert len(results) >= 1


class TestAutoIncrement:
    def test_ids_not_reused(self, db_with_contacts):
        id1 = db_with_contacts.insert("contacts", {"first_name": "A"})
        id2 = db_with_contacts.insert("contacts", {"first_name": "B"})
        db_with_contacts.delete("contacts", id1)
        id3 = db_with_contacts.insert("contacts", {"first_name": "C"})
        assert id3 > id1
        assert id3 > id2


class TestHnswHeaderCorrupt:
    @staticmethod
    def _make_header(**kw: int) -> bytearray:
        """Build a minimal valid header.bin (≥100 bytes) with overrideable fields.

        Layout (Chroma HNSW persistHeader):
          0: version     (i, 4B) — must be 1
          12: max_elements (Q, 8B)
          28: size_data_per_element (Q, 8B)
          68: maxM0        (Q, 8B)
        """
        data = bytearray(100)
        struct.pack_into("i", data, 0, kw.get("version", 1))
        struct.pack_into("Q", data, 12, kw.get("max_elements", 100000))
        struct.pack_into("Q", data, 28, kw.get("size_data_per_element", EMBEDDING_DIM * 4))
        struct.pack_into("Q", data, 68, kw.get("maxM0", 32))
        return data

    def test_valid_header(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header())
        assert HybridDB._is_hnsw_header_corrupt(header_path) is False

    def test_index_health_logs_when_link_file_exceeds_max_size(self, tmp_dir):
        db = HybridDB(tmp_dir, embedding_fn=_mock_embedding, max_chroma_index_gb=1)
        segment = Path(tmp_dir) / "vectors" / "segment"
        segment.mkdir()
        header_path = segment / "header.bin"
        link_path = segment / "link_lists.bin"
        with open(header_path, "wb") as f:
            f.write(self._make_header())
        with open(link_path, "wb") as f:
            f.truncate(1024**3 + 1)

        with mock.patch("hybriddb.maintenance.logger") as mock_logger:
            db._check_index_health()

        mock_logger.error.assert_called()

    def test_corrupt_max_elements_zero(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header(max_elements=0))
        assert HybridDB._is_hnsw_header_corrupt(header_path) is True

    def test_corrupt_max_elements_too_large(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header(max_elements=_CHROMA_INDEX_MAX_ELEMENTS + 1))
        assert HybridDB._is_hnsw_header_corrupt(header_path) is True

    def test_corrupt_size_data_per_element(self, tmp_dir):
        """Per-element size is checked against the collection's real
        dimension-derived expectation (hybriddb 0.5.6 fix)."""
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header(size_data_per_element=999))
        # Without an expected size the value is not judged (avoiding the
        # healthy-index false positive); with a mismatched expectation it is.
        assert HybridDB._is_hnsw_header_corrupt(header_path) is False
        assert (
            HybridDB._is_hnsw_header_corrupt(
                header_path, expected_size=EMBEDDING_DIM * 4
            )
            is True
        )

    def test_matching_size_data_per_element_is_healthy(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header())  # default size = EMBEDDING_DIM * 4
        assert (
            HybridDB._is_hnsw_header_corrupt(
                header_path, expected_size=EMBEDDING_DIM * 4
            )
            is False
        )

    def test_corrupt_max_neighbors_too_large(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(self._make_header(maxM0=_CHROMA_INDEX_MAX_M0 + 1))
        assert HybridDB._is_hnsw_header_corrupt(header_path) is True

    def test_missing_file(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "nonexistent.bin")
        assert HybridDB._is_hnsw_header_corrupt(header_path) is True

    def test_short_file(self, tmp_dir):
        header_path = os.path.join(tmp_dir, "header.bin")
        with open(header_path, "wb") as f:
            f.write(b"\x00" * 10)
        assert HybridDB._is_hnsw_header_corrupt(header_path) is True


class TestIndexHealthCheck:
    def test_healthy_index_passes(self, tmp_dir):
        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        db.create_table(
            "test",
            {"name": "TEXT", "notes": "LONGTEXT"},
        )
        db.insert("test", {"name": "x", "notes": "some content"})
        db.process_journal()
        db.close()

        db2 = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        assert db2._chroma is not None
        db2.close()

    def test_corrupt_header_logs_error(self, tmp_dir):
        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        db.create_table(
            "test",
            {"name": "TEXT", "notes": "LONGTEXT"},
        )
        db.insert("test", {"name": "x", "notes": "some content"})
        db.process_journal()
        db.close()

        seg_dir = None
        vector_dir = Path(tmp_dir) / "vectors"
        for d in vector_dir.iterdir():
            if d.is_dir() and (d / "header.bin").exists():
                seg_dir = d
                break
        assert seg_dir is not None, "No HNSW segment found"

        header_file = seg_dir / "header.bin"
        os.rename(str(header_file), str(header_file) + ".orig")
        corrupt = bytearray(72)
        struct.pack_into("Q", corrupt, 8, _CHROMA_INDEX_MAX_ELEMENTS + 1)
        with open(str(header_file), "wb") as f:
            f.write(corrupt)

        with mock.patch("hybriddb.maintenance.logger") as mock_logger:
            db2 = HybridDB(
                tmp_dir,
                embedding_fn=_mock_embedding,
                max_chroma_index_gb=5,
            )
            mock_logger.error.assert_called()
            db2.close()

        os.rename(str(header_file) + ".orig", str(header_file))

    def test_size_bloat_logs_error(self, tmp_dir):
        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=1,
        )
        db.create_table(
            "test",
            {"name": "TEXT", "notes": "LONGTEXT"},
        )
        db.insert("test", {"name": "x", "notes": "some content"})
        db.process_journal()
        db.close()

        original_stat = os.stat

        def _patched_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            p = str(path) if isinstance(path, os.PathLike) else path
            if isinstance(p, str) and "link_lists.bin" in p:
                return mock.MagicMock(
                    st_size=3 * 1024**3,
                    st_blocks=3 * 1024**3 // 512,
                    st_mode=result.st_mode,
                    st_ino=result.st_ino,
                    st_dev=result.st_dev,
                    st_nlink=result.st_nlink,
                    st_uid=result.st_uid,
                    st_gid=result.st_gid,
                    st_atime=result.st_atime,
                    st_mtime=result.st_mtime,
                    st_ctime=result.st_ctime,
                )
            return result

        with mock.patch("os.stat", side_effect=_patched_stat):
            with mock.patch("hybriddb.maintenance.logger") as mock_logger:
                db2 = HybridDB(
                    tmp_dir,
                    embedding_fn=_mock_embedding,
                    max_chroma_index_gb=1,
                )
                mock_logger.error.assert_called()
                db2.close()

    def test_force_rebuild_creates_new_index(self, tmp_dir):
        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        db.create_table(
            "test",
            {"name": "TEXT", "notes": "LONGTEXT"},
        )
        db.insert("test", {"name": "x", "notes": "hello world"})
        db.process_journal()
        db.close()

        db2 = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        result = db2.force_rebuild_chroma_index()
        assert result["status"] == "rebuilt"
        assert result["vectors_copied"] > 0

        results = db2.search("test", "notes", "hello world")
        assert len(results) >= 1
        db2.close()

    def test_disabled_health_check(self, tmp_dir):
        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=0,
        )
        db.close()

    def test_health_check_no_segments(self, tmp_dir):
        vectors_dir = Path(tmp_dir) / "vectors"
        vectors_dir.mkdir(parents=True, exist_ok=True)

        db = HybridDB(
            tmp_dir,
            embedding_fn=_mock_embedding,
            max_chroma_index_gb=5,
        )
        assert db._chroma is not None
        db.close()
