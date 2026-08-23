"""Message storage using CoreMem.

Thin adapter over MemoryCore, preserving the
Message/SearchResult dataclasses and public API for callers.
"""

import base64
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from coremem.core import MemoryCore
from coremem.types import Memory as _CoreMem
from coremem.types import SearchResult as _CoreMemResult

from src.storage.paths import get_paths

USER_LEVEL_CONTEXT = "user"
SUMMARY_SOURCE = "summarization_middleware"
SUMMARY_REASONS = {"threshold", "provider_overflow", "manual"}


@dataclass
class Message:
    """A single message in the conversation."""

    id: str
    ts: datetime
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    session_id: str = ""
    source: str | None = None


@dataclass
class SearchResult:
    """Search result with score."""

    id: str
    content: str
    ts: datetime
    role: str
    score: float


@dataclass(frozen=True)
class _StoredMessage:
    sequence: int
    id: str
    ts: datetime
    role: str
    content: str
    metadata: dict[str, Any] | None
    session_id: str


class MessageStore:
    """Manages message storage via MemoryCore.

    Structure:
        data/private/conversation/
        ├── app.db    # SQLite + FTS5 + journal (HybridDB)
        └── vectors/ # ChromaDB for semantic search
    """

    def __init__(self, user_id: str, base_dir: Path | str | None = None, workspace_id: str = "personal"):
        self.user_id = user_id
        self.workspace_id = USER_LEVEL_CONTEXT
        root_path: Path | None = None
        if base_dir is not None:
            base_path = Path(base_dir)
        else:
            paths = get_paths(user_id, workspace_id=workspace_id)
            base_path = paths.conversation_dir()
            root_path = paths.root
        base_path.mkdir(parents=True, exist_ok=True)

        # Migrate id column BEFORE MemoryCore initializes HybridDB+FTS triggers
        self._migrate_id_column(base_path)

        # Migrate old memory store data into conversation DB
        self._migrate_memory_store(user_id, base_path)

        # CoreMem >=0.10 replaced the observer/reflector architecture with
        # compiler + dreaming + search; the constructor no longer takes
        # observation kwargs (migrated 2026-08-21).
        self._core = MemoryCore(path=str(base_path))

        if root_path is not None:
            self._migrate_workspace_conversations(user_id, root_path, base_path)

        try:
            with self._core.db._connect() as cur:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_session_ts "
                    "ON messages(session_id, ts DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_session_role_ts "
                    "ON messages(session_id, role, ts DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_session_rowid "
                    "ON messages(session_id)"
                )
        except Exception:
            pass

        try:
            self._core.db.register_duckdb_table("messages")
        except Exception:
            pass

    @property
    def core(self) -> MemoryCore:
        return self._core

    @staticmethod
    def _migrate_id_column(base_path: Path) -> None:
        """Migrate messages.id from INTEGER PK to TEXT PK if needed.

        The old schema used INTEGER PRIMARY KEY AUTOINCREMENT, but CoreMem
        generates string UUIDs. This mismatch causes HybridDB FTS triggers
        to attempt using a string as an FTS5 rowid, raising:
            IntegrityError: datatype mismatch

        Must run BEFORE MemoryCore is created so HybridDB sees TEXT PK
        and uses new.rowid (not new.id) in FTS triggers.
        """
        db_path = base_path / "app.db"
        if not db_path.exists():
            return
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            info = conn.execute("PRAGMA table_info('messages')").fetchone()
            if info and 'INTEGER' in (info[2] or '').upper():
                conn.executescript("""
                    DROP TABLE IF EXISTS messages_new;
                    CREATE TABLE messages_new (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT,
                        metadata TEXT,
                        session_id TEXT,
                        user_id TEXT,
                        agent_id TEXT
                    );
                    INSERT INTO messages_new(id, ts, role, content, metadata, session_id, user_id, agent_id)
                        SELECT CAST(id AS TEXT), ts, role, content, metadata, session_id, user_id, agent_id
                        FROM messages;
                    DROP TABLE messages;
                    ALTER TABLE messages_new RENAME TO messages;
                    DELETE FROM _schema WHERE table_name = 'messages';
                """)
                # _journal may not exist on fresh DBs
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if '_journal' in tables:
                    conn.execute("DELETE FROM _journal WHERE app_table = 'messages'")
            conn.close()
        except Exception:
            if conn is not None:
                conn.close()

    @staticmethod
    def _migrate_memory_store(user_id: str, base_path: Path) -> None:
        """Migrate old memory/app.db observations and reflections into conversation/app.db.

        Runs once per user. Gated by sentinel file in conversation dir.
        """
        sentinel = base_path / ".memory_migrated"
        if sentinel.exists():
            return

        from src.storage.paths import get_paths
        old_path = get_paths(user_id).user_memory_dir() / "app.db"
        if not old_path.exists():
            sentinel.touch()
            return

        try:
            old_conn = sqlite3.connect(str(old_path))
            new_conn = sqlite3.connect(str(base_path / "app.db"))

            # Check if old DB has observations table
            tables = [r[0] for r in old_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )]
            if "observations" in tables:
                rows = old_conn.execute(
                    "SELECT id, content, priority, observation_ts, "
                    "referenced_date, source_message_range FROM observations"
                ).fetchall()
                for row in rows:
                    oid, content, priority, obs_ts, ref_date, src_range = row
                    importance = 0.3
                    if priority == "🔴":
                        importance = 0.8
                    elif priority == "🟡":
                        importance = 0.5
                    new_conn.execute(
                        "INSERT OR IGNORE INTO observations "
                        "(id, kind, content, source_quote, source_fact_ids, "
                        "source_message_ids, referenced_date, observation_ts, "
                        "user_id, agent_id, session_id, alignment_tier, "
                        "alignment_confidence, importance, confidence, "
                        "memory_type, durability, sensitivity, status, "
                        "valid_from, valid_to, superseded_by, entities, "
                        "reflected, embedding) "
                        "VALUES (?, 'fact', ?, '', '[]', ?, ?, ?, "
                        "?, '', '', '', "
                        "?, 0.800, "
                        "'', 'durable', 'normal', 'candidate', "
                        "'', '', '', '[]', "
                        "0, '')",
                        (oid, content, json.dumps([src_range]) if src_range else "[]",
                         ref_date or "", obs_ts or "",
                         user_id, importance),
                    )

            if "reflections" in tables:
                rows = old_conn.execute(
                    "SELECT id, content, domain, linked_observation_ids, "
                    "confidence FROM reflections"
                ).fetchall()
                for row in rows:
                    rid, content, domain, linked, confidence = row
                    if isinstance(linked, str):
                        try:
                            json.loads(linked)
                        except (json.JSONDecodeError, TypeError):
                            linked = json.dumps([linked])
                    else:
                        linked = json.dumps(linked or [])
                    new_conn.execute(
                        "INSERT OR IGNORE INTO reflections "
                        "(id, content, domain, linked_observation_ids, "
                        "score, embedding, user_id, session_id) "
                        "VALUES (?, ?, ?, ?, "
                        "?, '', ?, '')",
                        (rid, content, domain or "", linked,
                         float(confidence) if confidence else 0.6,
                         user_id),
                    )

            new_conn.commit()
            new_conn.close()
            old_conn.close()
        except Exception:
            pass  # Migration failed — non-fatal, old DB still exists

        sentinel.touch()

    @staticmethod
    def _migrate_workspace_conversations(user_id: str, root_path: Path, base_path: Path) -> None:
        """Import legacy per-workspace conversation DBs into the user-level DB.

        Old runtime storage used Workspaces/{workspace_id}/conversation.app.db.
        The user-level store now owns Conversation/app.db; repeated startup is
        safe because imported message ids are stable and source-prefixed.
        """
        workspaces_dir = root_path / "Workspaces"
        if not workspaces_dir.exists():
            return

        target_db = base_path / "app.db"
        if not target_db.exists():
            return

        legacy_dbs = sorted(workspaces_dir.glob("*/conversation.app.db"))
        for legacy_db in legacy_dbs:
            try:
                MessageStore._import_workspace_conversation_db(
                    user_id=user_id,
                    workspace_id=legacy_db.parent.name,
                    source_db=legacy_db,
                    target_db=target_db,
                )
            except Exception:
                pass

    @staticmethod
    def _import_workspace_conversation_db(
        user_id: str, workspace_id: str, source_db: Path, target_db: Path
    ) -> None:
        src = sqlite3.connect(str(source_db))
        dst = sqlite3.connect(str(target_db))
        try:
            tables = [
                r[0]
                for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            ]
            if "messages" not in tables:
                return

            source_columns = {
                row[1] for row in src.execute("PRAGMA table_info('messages')").fetchall()
            }
            if not {"id", "ts", "role"}.issubset(source_columns):
                return

            select_columns = [
                "id",
                "ts",
                "role",
                "content" if "content" in source_columns else "'' AS content",
                "metadata" if "metadata" in source_columns else "'{}' AS metadata",
                "session_id" if "session_id" in source_columns else "'' AS session_id",
                "user_id" if "user_id" in source_columns else "'' AS user_id",
                "agent_id" if "agent_id" in source_columns else "'' AS agent_id",
            ]
            rows = src.execute(f"SELECT {', '.join(select_columns)} FROM messages").fetchall()
            for msg_id, ts, role, content, metadata, session_id, row_user_id, agent_id in rows:
                row_user_id = row_user_id or ""
                if row_user_id:
                    if row_user_id != user_id:
                        continue
                elif user_id != "default_user":
                    continue
                old_id = str(msg_id)
                old_session_id = session_id or "default"
                imported_id = f"legacyws:{workspace_id}:{old_id}"
                imported_session_id = f"legacy-{workspace_id}-{old_session_id}"
                imported_metadata = MessageStore._legacy_import_metadata(
                    metadata=metadata,
                    workspace_id=workspace_id,
                    legacy_id=old_id,
                    legacy_session_id=old_session_id,
                )
                dst.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(id, ts, role, content, metadata, session_id, user_id, agent_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        imported_id,
                        ts,
                        role,
                        content or "",
                        imported_metadata,
                        imported_session_id,
                        row_user_id or user_id,
                        agent_id or "",
                    ),
                )
            dst.commit()
        finally:
            src.close()
            dst.close()

    @staticmethod
    def _legacy_import_metadata(
        metadata: str | None, workspace_id: str, legacy_id: str, legacy_session_id: str
    ) -> str:
        try:
            parsed = json.loads(metadata) if metadata else {}
            if not isinstance(parsed, dict):
                parsed = {"legacy_metadata": parsed}
        except (json.JSONDecodeError, TypeError):
            parsed = {"legacy_metadata": metadata}
        parsed["legacy_id"] = legacy_id
        parsed["source_id"] = legacy_id
        parsed["legacy_workspace_id"] = workspace_id
        parsed["legacy_session_id"] = legacy_session_id
        parsed["legacy_source"] = "workspace_conversation"
        return json.dumps(parsed)

    def add_message(
        self, role: str, content: str, metadata: dict[str, Any] | None = None, session_id: str | None = None
    ) -> str:
        result = self._core.ingest(role, content or "(empty)", session_id=session_id, metadata=metadata)
        return self._resolve_message_id(result, session_id) or result or ""

    def add_message_with_embedding(
        self,
        role: str,
        content: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        result = self._core.ingest(
            role, content or "(empty)", session_id=session_id, metadata=metadata, embedding=embedding
        )
        return self._resolve_message_id(result, session_id) or result or ""

    def _resolve_message_id(self, ingest_result: str | None, session_id: str | None) -> str | None:
        """Resolve the row message id from an ingest result.

        CoreMem >=0.10 `ingest` returns the turn_id, not the message id — the
        app's add_message contract returns the message id (used for provenance
        lookups and session metadata). Query the row back by turn_id.
        """
        if not ingest_result:
            return None
        try:
            with self._core.db._connect() as cur:
                row = cur.execute(
                    "SELECT id FROM messages WHERE turn_id = ? ORDER BY ts DESC LIMIT 1",
                    (ingest_result,),
                ).fetchone()
                if row is not None:
                    return str(row[0])
        except Exception:
            pass
        return None

    @staticmethod
    def _to_msg(m: _CoreMem) -> Message:
        return Message(
            id=m.id,
            ts=m.ts if m.ts is not None else datetime.now(UTC),
            role=m.role,
            content=m.content,
            metadata=m.metadata,
            session_id=m.session_id or "",
        )

    @staticmethod
    def _require_session_id(session_id: str) -> str:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id must be nonempty")
        return session_id

    @staticmethod
    def _summary_provenance(
        metadata: object,
    ) -> tuple[set[str], set[str]] | None:
        if not isinstance(metadata, dict):
            return None
        if metadata.get("source") != SUMMARY_SOURCE:
            return None
        if metadata.get("compression_reason") not in SUMMARY_REASONS:
            return None
        if "session_id" in metadata:
            return None

        summarized = metadata.get("summarized_message_ids")
        preserved = metadata.get("preserved_message_ids")
        if not isinstance(summarized, list) or not isinstance(preserved, list):
            return None
        if not summarized:
            return None
        if any(not isinstance(value, str) or not value.strip() for value in summarized + preserved):
            return None

        summarized_ids = set(summarized)
        preserved_ids = set(preserved)
        if len(summarized_ids) != len(summarized) or len(preserved_ids) != len(preserved):
            return None
        if summarized_ids & preserved_ids:
            return None
        return summarized_ids, preserved_ids

    @staticmethod
    def _parse_stored_timestamp(value: object) -> datetime:
        if isinstance(value, datetime):
            timestamp = value
        elif isinstance(value, str):
            try:
                timestamp = datetime.fromisoformat(value)
            except ValueError:
                return datetime.min.replace(tzinfo=UTC)
        else:
            return datetime.min.replace(tzinfo=UTC)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)

    @staticmethod
    def _parse_stored_metadata(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _read_scoped_rows(
        self, session_id: str, limit: int | None = None
    ) -> list[_StoredMessage]:
        columns = "rowid, id, ts, role, content, metadata, session_id"
        params: list[str | int] = [session_id]
        if limit is None:
            query = f"SELECT {columns} FROM messages WHERE session_id = ? ORDER BY rowid ASC"
        else:
            query = (
                f"SELECT {columns} FROM ("
                f"SELECT {columns} FROM messages WHERE session_id = ? "
                "ORDER BY rowid DESC LIMIT ?"
                ") ORDER BY rowid ASC"
            )
            params.append(limit)
        with self._core.db._connect() as cur:
            rows = cur.execute(query, params).fetchall()
        return [
            _StoredMessage(
                sequence=int(row[0]),
                id=str(row[1]),
                ts=self._parse_stored_timestamp(row[2]),
                role=str(row[3]),
                content=str(row[4] or ""),
                metadata=self._parse_stored_metadata(row[5]),
                session_id=str(row[6] or ""),
            )
            for row in rows
        ]

    @staticmethod
    def _stored_to_message(row: _StoredMessage) -> Message:
        source = None
        if row.metadata:
            source = row.metadata.get("source")
        return Message(
            id=row.id,
            ts=row.ts,
            role=row.role,
            content=row.content,
            metadata=row.metadata,
            session_id=row.session_id,
            source=source,
        )

    def _validated_summary_provenance(
        self,
        summary: _StoredMessage,
        rows_by_id: dict[str, _StoredMessage],
        cache: dict[int, tuple[set[str], set[str]] | None],
        visiting: set[int] | None = None,
    ) -> tuple[set[str], set[str]] | None:
        if summary.sequence in cache:
            return cache[summary.sequence]
        if summary.role != "summary":
            return None
        provenance = self._summary_provenance(summary.metadata)
        if provenance is None:
            cache[summary.sequence] = None
            return None

        visiting = set() if visiting is None else visiting
        if summary.sequence in visiting:
            cache[summary.sequence] = None
            return None
        visiting.add(summary.sequence)
        summarized_ids, preserved_ids = provenance
        for message_id in summarized_ids:
            referenced = rows_by_id.get(message_id)
            if referenced is None or referenced.sequence >= summary.sequence:
                cache[summary.sequence] = None
                visiting.remove(summary.sequence)
                return None
            if referenced.role == "summary" and self._validated_summary_provenance(
                referenced, rows_by_id, cache, visiting
            ) is None:
                cache[summary.sequence] = None
                visiting.remove(summary.sequence)
                return None
        for message_id in preserved_ids:
            referenced = rows_by_id.get(message_id)
            if (
                referenced is None
                or referenced.sequence >= summary.sequence
                or referenced.role == "summary"
            ):
                cache[summary.sequence] = None
                visiting.remove(summary.sequence)
                return None
        visiting.remove(summary.sequence)
        cache[summary.sequence] = provenance
        return provenance

    def _newest_valid_summary(
        self, rows: list[_StoredMessage]
    ) -> tuple[_StoredMessage, tuple[set[str], set[str]]] | None:
        rows_by_id = {row.id: row for row in rows}
        cache: dict[int, tuple[set[str], set[str]] | None] = {}
        for row in reversed(rows):
            provenance = self._validated_summary_provenance(row, rows_by_id, cache)
            if provenance is not None:
                return row, provenance
        return None

    @staticmethod
    def _to_sr(r: _CoreMemResult) -> SearchResult:
        return SearchResult(
            id=r.memory.id,
            content=r.memory.content,
            ts=r.memory.ts if r.memory.ts is not None else datetime.now(UTC),
            role=r.memory.role,
            score=r.score,
        )

    def search_keyword(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not query:
            return []
        results = self._core.recall(query, strategy="direct", limit=limit)
        return [self._to_sr(r) for r in cast(list[_CoreMemResult], results)]

    def search_vector(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not query:
            return []
        results = self._core.recall(query, strategy="episodic", limit=limit)
        return [self._to_sr(r) for r in cast(list[_CoreMemResult], results)]

    def search_hybrid(
        self, query: str, query_embedding: list[float] | None = None, limit: int = 10, **kwargs: Any
    ) -> list[SearchResult]:
        if not query:
            return []
        strategy = kwargs.pop("strategy", "fusion")
        results = self._core.recall(query, strategy=strategy, limit=limit, **kwargs)
        return [self._to_sr(r) for r in cast(list[_CoreMemResult], results)]

    def get_messages(
        self, start_date: date | None = None, end_date: date | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        ts_after = f"{start_date.isoformat()}T00:00:00" if start_date else None
        ts_before = f"{end_date.isoformat()}T23:59:59" if end_date else None
        memories = self._core.fetch(
            limit=limit or 10000,
            ts_after=ts_after,
            ts_before=ts_before,
        )
        return [self._to_msg(m) for m in reversed(memories)]

    def get_messages_by_session_id(self, session_id: str, limit: int = 50) -> list[Message]:
        session_id = self._require_session_id(session_id)
        if limit <= 0:
            return []
        return [
            self._stored_to_message(row)
            for row in self._read_scoped_rows(session_id, limit=limit)
        ]

    def get_sessions(self) -> list[dict[str, str]]:
        """List all chat sessions with titles.

        Uses session_title from metadata if available, falls back to
        first user message content truncated to 60 chars.
        Returns sessions in descending order by creation time (newest first).
        """
        try:
            with self._core.db._connect() as cur:
                rows = cur.execute(
                    "SELECT session_id, "
                    "COALESCE(json_extract(metadata, '$.session_title'), "
                    "SUBSTR(content, 1, 60)) as title, "
                    "MIN(ts) as created_at "
                    "FROM messages "
                    "WHERE role = 'user' AND session_id != '' "
                    "GROUP BY session_id "
                    "ORDER BY created_at DESC"
                ).fetchall()
        except Exception:
            return []

        return [
            {"session_id": row[0], "title": row[1] or "", "created_at": row[2] or ""}
            for row in rows
            if row[0]
        ]

    def get_session_title(self, session_id: str) -> str | None:
        """Return the stored title for a session (metadata session_title only).

        Unlike get_sessions(), this does NOT fall back to the first message
        content — a session without a stored title returns None so callers can
        distinguish "never titled" from "titled with the first message text".
        """
        try:
            with self._core.db._connect() as cur:
                row = cur.execute(
                    "SELECT json_extract(metadata, '$.session_title') "
                    "FROM messages "
                    "WHERE session_id = ? AND role = 'user' "
                    "ORDER BY ts ASC LIMIT 1",
                    [session_id],
                ).fetchone()
            if not row or not row[0]:
                return None
            return str(row[0])
        except Exception:
            return None

    def update_session_title(self, session_id: str, title: str) -> None:
        """Update the title for a chat session (stored on first user message metadata)."""
        try:
            memories = self._core.fetch(limit=1, session_id=session_id, role="user")
            if not memories:
                return
            first = memories[0]
            meta = first.metadata or {}
            meta["session_title"] = title
            with self._core.db._connect() as cur:
                cur.execute(
                    "UPDATE messages SET metadata = ? WHERE id = ?",
                    [json.dumps(meta), first.id],
                )
        except Exception:
            pass

    def get_recent_messages(self, count: int = 100) -> list[Message]:
        memories = self._core.fetch(limit=count)
        return [self._to_msg(m) for m in reversed(memories)]

    def get_recent_messages_for_workspace(
        self, workspace_id: str = "personal", count: int = 100
    ) -> list[Message]:
        memories = self._core.fetch(limit=count)
        return [self._to_msg(m) for m in reversed(memories)]

    def _find_newest_summary_rowid(self, session_id: str) -> int | None:
        """Find the rowid of the newest valid summary using indexed queries.

        Two-phase: first find candidate summary rowids, then validate
        provenance against only the rows needed. Avoids loading all rows.
        Scans backwards in batches to handle many invalid summaries.
        """
        with self._core.db._connect() as cur:
            min_rowid = cur.execute(
                "SELECT MIN(rowid) FROM messages WHERE session_id = ?",
                [session_id],
            ).fetchone()[0]
            if min_rowid is None:
                return None
            max_rowid = cur.execute(
                "SELECT MAX(rowid) FROM messages WHERE session_id = ?",
                [session_id],
            ).fetchone()[0]
        batch_size = 100
        current_max = max_rowid
        while current_max >= min_rowid:
            batch_min = max(current_max - batch_size + 1, min_rowid)
            with self._core.db._connect() as cur:
                candidate_rows = cur.execute(
                    "SELECT rowid, id, ts, role, content, metadata, session_id "
                    "FROM messages "
                    "WHERE session_id = ? AND role = 'summary' "
                    "AND rowid BETWEEN ? AND ? "
                    "ORDER BY rowid DESC",
                    [session_id, batch_min, current_max],
                ).fetchall()
            if candidate_rows:
                candidates = [
                    _StoredMessage(
                        sequence=int(r[0]),
                        id=str(r[1]),
                        ts=self._parse_stored_timestamp(r[2]),
                        role=str(r[3]),
                        content=str(r[4] or ""),
                        metadata=self._parse_stored_metadata(r[5]),
                        session_id=str(r[6] or ""),
                    )
                    for r in candidate_rows
                ]
                newest_sequence = candidates[0].sequence
                columns = "rowid, id, ts, role, content, metadata, session_id"
                with self._core.db._connect() as cur:
                    all_rows_raw = cur.execute(
                        f"SELECT {columns} FROM messages "
                        "WHERE session_id = ? AND rowid <= ? ORDER BY rowid ASC",
                        [session_id, newest_sequence],
                    ).fetchall()
                all_rows = [
                    _StoredMessage(
                        sequence=int(r[0]),
                        id=str(r[1]),
                        ts=self._parse_stored_timestamp(r[2]),
                        role=str(r[3]),
                        content=str(r[4] or ""),
                        metadata=self._parse_stored_metadata(r[5]),
                        session_id=str(r[6] or ""),
                    )
                    for r in all_rows_raw
                ]
                rows_by_id = {row.id: row for row in all_rows}
                cache: dict[int, tuple[set[str], set[str]] | None] = {}
                for candidate in candidates:
                    if self._validated_summary_provenance(candidate, rows_by_id, cache) is not None:
                        return candidate.sequence
            current_max = batch_min - 1
        return None

    def get_messages_with_summary(self, session_id: str, limit: int = 50) -> list[Message]:
        session_id = self._require_session_id(session_id)
        if limit <= 0:
            return []
        summary_sequence = self._find_newest_summary_rowid(session_id)
        if summary_sequence is None:
            with self._core.db._connect() as cur:
                rows_raw = cur.execute(
                    "SELECT rowid, id, ts, role, content, metadata, session_id "
                    "FROM messages "
                    "WHERE session_id = ? AND role != 'summary' AND role != 'tool' "
                    "AND COALESCE(json_extract(metadata, '$.include_in_model_context'), 1) != 0 "
                    "ORDER BY rowid DESC LIMIT ?",
                    [session_id, limit],
                ).fetchall()
            rows = [
                _StoredMessage(
                    sequence=int(r[0]),
                    id=str(r[1]),
                    ts=self._parse_stored_timestamp(r[2]),
                    role=str(r[3]),
                    content=str(r[4] or ""),
                    metadata=self._parse_stored_metadata(r[5]),
                    session_id=str(r[6] or ""),
                )
                for r in reversed(rows_raw)
            ]
            return [self._stored_to_message(row) for row in rows]

        # Load rows up to summary_sequence + extra after it
        with self._core.db._connect() as cur:
            rows_raw = cur.execute(
                "SELECT rowid, id, ts, role, content, metadata, session_id "
                "FROM messages "
                "WHERE session_id = ? AND rowid <= ? "
                "ORDER BY rowid ASC",
                [session_id, summary_sequence + limit],
            ).fetchall()
        rows = [
            _StoredMessage(
                sequence=int(r[0]),
                id=str(r[1]),
                ts=self._parse_stored_timestamp(r[2]),
                role=str(r[3]),
                content=str(r[4] or ""),
                metadata=self._parse_stored_metadata(r[5]),
                session_id=str(r[6] or ""),
            )
            for r in rows_raw
        ]
        summary = next(r for r in rows if r.sequence == summary_sequence)
        provenance = self._validated_summary_provenance(
            summary,
            {row.id: row for row in rows},
            {},
        )
        summarized_ids, preserved_ids = provenance or (set(), set())
        retained = [
            row
            for row in rows
            if row.role != "summary"
            and row.role != "tool"
            and row.id not in summarized_ids
            and (row.id in preserved_ids or row.sequence > summary.sequence)
            and (row.metadata or {}).get("include_in_model_context") is not False
        ]
        if limit == 1:
            return [self._stored_to_message(summary)]
        return [self._stored_to_message(summary)] + [
            self._stored_to_message(row) for row in retained[-(limit - 1) :]
        ]

    def add_summary_message(
        self, content: str, *, session_id: str, metadata: dict[str, Any]
    ) -> str:
        session_id = self._require_session_id(session_id)
        if not content.strip():
            raise ValueError("content must be nonblank")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        summary_metadata = dict(metadata)
        summary_metadata["source"] = SUMMARY_SOURCE
        summary_metadata.pop("session_id", None)
        if self._summary_provenance(summary_metadata) is None:
            raise ValueError("invalid summary metadata or compression_reason")
        # Validate and write in a transaction to prevent TOCTOU races
        with self._core.db._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            try:
                rows_raw = cur.execute(
                    "SELECT rowid, id, ts, role, content, metadata, session_id "
                    "FROM messages WHERE session_id = ? ORDER BY rowid ASC",
                    [session_id],
                ).fetchall()
                rows = [
                    _StoredMessage(
                        sequence=int(r[0]),
                        id=str(r[1]),
                        ts=self._parse_stored_timestamp(r[2]),
                        role=str(r[3]),
                        content=str(r[4] or ""),
                        metadata=self._parse_stored_metadata(r[5]),
                        session_id=str(r[6] or ""),
                    )
                    for r in rows_raw
                ]
                candidate = _StoredMessage(
                    sequence=(rows[-1].sequence + 1) if rows else 1,
                    id="",
                    ts=datetime.now(UTC),
                    role="summary",
                    content=content,
                    metadata=summary_metadata,
                    session_id=session_id,
                )
                rows_by_id = {row.id: row for row in rows}
                if self._validated_summary_provenance(candidate, rows_by_id, {}) is None:
                    raise ValueError("invalid summary provenance")
                mid = str(uuid.uuid4())[:12]
                cur.execute(
                    "INSERT INTO messages (id, role, content, session_id, metadata, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        mid,
                        "summary",
                        content or "(empty)",
                        session_id,
                        json.dumps(summary_metadata),
                        datetime.now(UTC).isoformat(),
                    ],
                )
                cur.execute("COMMIT")
                return mid
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def has_summary(self, session_id: str) -> bool:
        session_id = self._require_session_id(session_id)
        return self._newest_valid_summary(self._read_scoped_rows(session_id)) is not None

    def count_messages(self, start_date: date | None = None, end_date: date | None = None) -> int:
        if not start_date and not end_date:
            return self._core.count()
        ts_after = f"{start_date.isoformat()}T00:00:00" if start_date else None
        ts_before = f"{end_date.isoformat()}T23:59:59" if end_date else None
        try:
            with self._core.db._connect() as cur:
                query = "SELECT COUNT(*) FROM messages WHERE 1=1"
                params: list[str] = []
                if ts_after:
                    query += " AND ts >= ?"
                    params.append(ts_after)
                if ts_before:
                    query += " AND ts <= ?"
                    params.append(ts_before)
                return int(cur.execute(query, params).fetchone()[0] or 0)
        except Exception:
            return len(self._core.fetch_all(ts_after=ts_after, ts_before=ts_before))

    def delete_messages_for_workspace(self, workspace_id: str) -> int:
        """Delete all messages for a workspace.

        Uses direct SQLite DELETE because coremem's delete method cannot
        resolve auto-assigned integer ids via DuckDB (id shows as None
        due to the _patch_coremem_for_integer_pk patch that omits id
        from INSERT statements).
        """
        with self._core.db._connect() as cur:
            cur.execute(
                "DELETE FROM messages WHERE json_extract(metadata, '$.workspace_id') = ?",
                [workspace_id],
            )
            count = cur.rowcount
            cur.execute(
                "DELETE FROM _journal WHERE app_table = 'messages'"
                " AND json_extract(metadata, '$.workspace_id') = ?",
                [workspace_id],
            )
        if self._core.db._chroma is not None:
            try:
                memories = self._core.fetch(limit=10000, metadata={"workspace_id": workspace_id})
                ids = [m.id for m in memories if m.id != "None"]
                if ids:
                    self._core.db._chroma.delete(
                        collection_name="messages_content",
                        ids=ids,
                    )
            except Exception:
                pass
        try:
            self._core.db.sync_duckdb_table("messages")
        except Exception:
            pass
        return cast(int, count)

    def persist_run(
        self,
        run_id: str,
        session_id: str,
        user_message_id: str,
        final_answer: Message,
        audit_records: list[Message],
        metadata: dict[str, Any],
        pre_messages: list[Message] | None = None,
    ) -> str:
        """Persist a completed run's final answer and audit records.

        The final answer is persisted with run_id in metadata. pre_messages
        (e.g. the assistant's reasoning, which arrived BEFORE the answer in
        the stream) are inserted first so transcript ordering matches the
        stream. Audit records (tools, rubric feedback) are stored with
        include_in_model_context: false so they are excluded from future
        model context loading; pre_messages stay in context.

        Idempotent on run_id — retry after uncertain disconnect reads
        existing run instead of writing duplicates.
        """
        pre_messages = list(pre_messages or [])
        session_id = self._require_session_id(session_id)
        with self._core.db._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            try:
                existing = cur.execute(
                    "SELECT id FROM messages WHERE session_id = ? AND json_extract(metadata, '$.run_id') = ? AND role = ?",
                    [session_id, run_id, final_answer.role],
                ).fetchone()
                if existing is not None:
                    cur.execute("COMMIT")
                    return str(existing[0])

                answer_metadata = dict(metadata)
                answer_metadata["run_id"] = run_id
                answer_metadata["include_in_model_context"] = True

                # Insert pre-messages (reasoning etc.) BEFORE the final
                # answer so the stored transcript matches the stream order.
                for pre in pre_messages:
                    pre_metadata = dict(metadata)
                    pre_metadata["run_id"] = run_id
                    pre_metadata["include_in_model_context"] = True
                    pre_mid = str(uuid.uuid4())[:12]
                    cur.execute(
                        "INSERT INTO messages (id, role, content, session_id, metadata, ts) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            pre_mid,
                            pre.role,
                            pre.content or "(empty)",
                            session_id,
                            json.dumps(pre_metadata),
                            datetime.now(UTC).isoformat(),
                        ],
                    )

                # Audit records (tool executions) are inserted BEFORE the
                # final answer so the stored transcript matches the stream
                # order (reasoning, tools, answer) — tools ran before the
                # answer, and a reload must not flip them below it.
                for record in audit_records:
                    record_metadata = dict(metadata)
                    record_metadata.update(record.metadata or {})
                    record_metadata["run_id"] = run_id
                    record_metadata["include_in_model_context"] = False
                    record_mid = str(uuid.uuid4())[:12]
                    cur.execute(
                        "INSERT INTO messages (id, role, content, session_id, metadata, ts) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            record_mid,
                            record.role,
                            record.content or "(empty)",
                            session_id,
                            json.dumps(record_metadata),
                            datetime.now(UTC).isoformat(),
                        ],
                    )

                answer_mid = str(uuid.uuid4())[:12]
                cur.execute(
                    "INSERT INTO messages (id, role, content, session_id, metadata, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        answer_mid,
                        final_answer.role,
                        final_answer.content or "(empty)",
                        session_id,
                        json.dumps(answer_metadata),
                        datetime.now(UTC).isoformat(),
                    ],
                )

                cur.execute("COMMIT")
                return answer_mid
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def clear(self) -> None:
        self._core.clear()

    def get_turns(
        self, session_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return complete turns grouped by run_id, with cursor pagination.

        Each turn is a dict with:
          - run_id: str | None (None for legacy messages without run_id)
          - messages: list[Message] in chronological order
          - metadata: dict (run-level metadata from the final assistant message)

        Returns (turns, next_cursor). next_cursor is None when no more pages.
        Cursor is an opaque string (base64-encoded rowid of the last returned message).
        """
        session_id = self._require_session_id(session_id)
        if limit <= 0:
            return [], None

        start_rowid = 0
        if cursor:
            try:
                start_rowid = int(base64.b64decode(cursor).decode("utf-8"))
            except Exception:
                start_rowid = 0

        turns: list[dict[str, Any]] = []
        last_msg_id: str | None = None
        fetch_size = limit * 5

        while len(turns) < limit:
            with self._core.db._connect() as cur:
                rows_raw = cur.execute(
                    "SELECT rowid, id, ts, role, content, metadata, session_id "
                    "FROM messages "
                    "WHERE session_id = ? AND rowid > ? "
                    "ORDER BY rowid ASC LIMIT ?",
                    [session_id, start_rowid, fetch_size],
                ).fetchall()

            if not rows_raw:
                break

            rows = [
                _StoredMessage(
                    sequence=int(r[0]),
                    id=str(r[1]),
                    ts=self._parse_stored_timestamp(r[2]),
                    role=str(r[3]),
                    content=str(r[4] or ""),
                    metadata=self._parse_stored_metadata(r[5]),
                    session_id=str(r[6] or ""),
                )
                for r in rows_raw
            ]

            current_run_id: str | None = None
            current_turn: list[Message] = []

            for row in rows:
                msg = self._stored_to_message(row)
                run_id = None
                if row.metadata:
                    run_id = row.metadata.get("run_id")

                should_split = False
                if run_id != current_run_id and current_turn:
                    should_split = True
                elif run_id is None and current_run_id is None and current_turn:
                    last_role = current_turn[-1].role
                    if msg.role == "user" and last_role in ("assistant", "tool", "reasoning"):
                        should_split = True

                if should_split:
                    turns.append(self._build_turn(current_run_id, current_turn))
                    current_turn = []
                    if len(turns) >= limit:
                        last_msg_id = row.id
                        break

                current_run_id = run_id
                current_turn.append(msg)
                last_msg_id = row.id

            if current_turn and len(turns) < limit:
                turns.append(self._build_turn(current_run_id, current_turn))

            if len(rows_raw) < fetch_size:
                break

            start_rowid = rows_raw[-1][0]

        next_cursor = None
        if last_msg_id is not None and len(turns) >= limit:
            with self._core.db._connect() as cur:
                row = cur.execute(
                    "SELECT rowid FROM messages WHERE session_id = ? AND id = ?",
                    [session_id, last_msg_id],
                ).fetchone()
            if row is not None:
                next_cursor = base64.b64encode(str(row[0]).encode("utf-8")).decode("utf-8")

        return turns[:limit], next_cursor

    @staticmethod
    def _build_turn(run_id: str | None, messages: list[Message]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.metadata:
                metadata = msg.metadata
                break
        return {
            "run_id": run_id,
            "messages": messages,
            "metadata": metadata,
        }

    def delete_session(self, session_id: str) -> int:
        """Delete all messages in a specific chat session."""
        try:
            with self._core.db._connect() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE session_id = ?",
                    [session_id],
                )
                count = cur.rowcount
            try:
                self._core.db.sync_duckdb_table("messages")
            except Exception:
                pass
            return cast(int, count)
        except Exception:
            return 0


_stores: dict[str, MessageStore] = {}


def get_message_store(user_id: str = "default_user", workspace_id: str = "personal") -> MessageStore:
    key = f"{user_id}:msgstore"
    if key not in _stores:
        _stores[key] = MessageStore(user_id, workspace_id=workspace_id)
    return _stores[key]


def clear_message_store(user_id: str, workspace_id: str) -> None:
    """Evict a MessageStore from the cache (e.g. after workspace deletion)."""
    key = f"{user_id}:msgstore"
    _stores.pop(key, None)
