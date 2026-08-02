"""Message storage using CoreMem.

Thin adapter over MemoryCore, preserving the
Message/SearchResult dataclasses and public API for callers.
"""

import asyncio
import json
import sqlite3
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


@dataclass
class SearchResult:
    """Search result with score."""

    id: str
    content: str
    ts: datetime
    role: str
    score: float


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

        self._core = MemoryCore(
            path=str(base_path),
            enable_observations=True,
            enable_reflections=True,
            observation_kwargs={"session_id": USER_LEVEL_CONTEXT},
        )

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
        except Exception:
            pass

        try:
            self._core.db.register_duckdb_table("messages")
        except Exception:
            pass

        # Start background observer and reflector workers
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._core.start_pipelines())
        except (RuntimeError, AttributeError):
            pass  # No event loop or no pipelines — feature not available

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
        return result or ""

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
        return result or ""

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
    def _memory_sort_key(memory: _CoreMem) -> tuple[float, str]:
        timestamp = memory.ts
        if timestamp is None:
            return float("-inf"), memory.id
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.timestamp(), memory.id

    def _scoped_memories(self, session_id: str) -> list[_CoreMem]:
        memories = self._core.fetch_all(session_id=session_id)
        return [memory for memory in memories if (memory.session_id or "") == session_id]

    def _newest_valid_summary(self, memories: list[_CoreMem]) -> _CoreMem | None:
        summaries = sorted(
            (memory for memory in memories if memory.role == "summary"),
            key=self._memory_sort_key,
            reverse=True,
        )
        return next(
            (summary for summary in summaries if self._summary_provenance(summary.metadata)),
            None,
        )

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
        results = self._core.search(query, limit=limit)
        return [self._to_sr(r) for r in results]

    def search_vector(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not query:
            return []
        results = self._core.search(query, limit=limit)
        return [self._to_sr(r) for r in results]

    def search_hybrid(
        self, query: str, query_embedding: list[float] | None = None, limit: int = 10, **kwargs: Any
    ) -> list[SearchResult]:
        if not query:
            return []
        results = self._core.search_enhanced(query, limit=limit, **kwargs)
        return [self._to_sr(r) for r in results]

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
        memories = self._core.fetch(limit=limit, session_id=session_id)
        return [self._to_msg(m) for m in reversed(memories)]

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

    def get_messages_with_summary(self, session_id: str, limit: int = 50) -> list[Message]:
        session_id = self._require_session_id(session_id)
        if limit <= 0:
            return []
        memories = self._scoped_memories(session_id)
        summary = self._newest_valid_summary(memories)
        if summary is None:
            non_summaries = sorted(
                (memory for memory in memories if memory.role != "summary"),
                key=self._memory_sort_key,
            )
            return [self._to_msg(memory) for memory in non_summaries[-limit:]]

        provenance = self._summary_provenance(summary.metadata)
        if provenance is None:  # Guarded by _newest_valid_summary.
            return []
        summarized_ids, preserved_ids = provenance
        summary_key = self._memory_sort_key(summary)
        retained = sorted(
            (
                memory
                for memory in memories
                if memory.role != "summary"
                and memory.id not in summarized_ids
                and (
                    memory.id in preserved_ids
                    or self._memory_sort_key(memory)[0] > summary_key[0]
                )
            ),
            key=self._memory_sort_key,
        )
        if limit == 1:
            return [self._to_msg(summary)]
        return [self._to_msg(summary)] + [
            self._to_msg(memory) for memory in retained[-(limit - 1) :]
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
        return self.add_message(
            "summary", content, metadata=summary_metadata, session_id=session_id
        )

    def has_summary(self, session_id: str) -> bool:
        session_id = self._require_session_id(session_id)
        return self._newest_valid_summary(self._scoped_memories(session_id)) is not None

    def count_messages(self, start_date: date | None = None, end_date: date | None = None) -> int:
        if not start_date and not end_date:
            return cast(int, self._core.count())
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

    def clear(self) -> None:
        self._core.clear()

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
