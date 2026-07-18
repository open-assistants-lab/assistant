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


@dataclass
class Message:
    """A single message in the conversation."""

    id: str
    ts: datetime
    role: str
    content: str
    metadata: dict[str, Any] | None = None


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
        self.workspace_id = workspace_id
        if base_dir is not None:
            base_path = Path(base_dir)
        else:
            paths = get_paths(user_id, workspace_id=workspace_id)
            if workspace_id == "personal":
                base_path = paths.conversation_dir()
            else:
                base_path = paths.workspace_conversation_path().parent
        base_path.mkdir(parents=True, exist_ok=True)

        # Migrate id column BEFORE MemoryCore initializes HybridDB+FTS triggers
        self._migrate_id_column(base_path)

        # Migrate old memory store data into conversation DB
        self._migrate_memory_store(user_id, base_path)

        self._core = MemoryCore(
            path=str(base_path),
            enable_observations=True,
            enable_reflections=True,
            observation_kwargs={"session_id": workspace_id},
        )

        try:
            with self._core.db._connect() as cur:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)")
        except Exception:
            pass

        try:
            self._core.db.register_duckdb_table("messages")
        except Exception:
            pass

        # Start background observer and reflector workers
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._core.start_pipelines())  # type: ignore[attr-defined]
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
        memories = self._core.fetch(limit=limit, session_id=session_id)
        return [self._to_msg(m) for m in reversed(memories)]

    def get_sessions(self) -> list[dict[str, str]]:
        """List all chat sessions with titles derived from the first user message.

        Queries the session_id column directly since CoreMem's fetch does not
        surface session_id on the Message dataclass.
        """
        try:
            with self._core.db._connect() as cur:
                rows = cur.execute(
                    "SELECT session_id, content FROM messages "
                    "WHERE role = 'user' AND session_id != '' "
                    "ORDER BY ts ASC"
                ).fetchall()
        except Exception:
            return []

        sessions: dict[str, str] = {}
        for row in rows:
            sid = row[0]
            content = row[1] or ""
            if sid and sid not in sessions:
                sessions[sid] = content[:60] if len(content) > 60 else content

        return [{"session_id": sid, "title": title} for sid, title in sessions.items()]

    def get_recent_messages(self, count: int = 100) -> list[Message]:
        memories = self._core.fetch(limit=count)
        return [self._to_msg(m) for m in reversed(memories)]

    def get_recent_messages_for_workspace(
        self, workspace_id: str = "personal", count: int = 100
    ) -> list[Message]:
        memories = self._core.fetch(limit=count, metadata={"workspace_id": workspace_id})
        return [self._to_msg(m) for m in reversed(memories)]

    def get_messages_with_summary(self, limit: int = 50, workspace_id: str | None = None) -> list[Message]:
        if limit <= 0:
            return []
        summaries = self._core.fetch(limit=1, role="summary")
        if not summaries:
            memories = self._core.fetch(limit=limit, metadata={"workspace_id": workspace_id}) if workspace_id else self._core.fetch(limit=limit)
            return [self._to_msg(m) for m in reversed(memories)]
        non_summaries = self._core.fetch(limit=limit, metadata={"workspace_id": workspace_id}) if workspace_id else self._core.fetch(limit=limit)
        non_summaries = [m for m in non_summaries if m.role != "summary"]
        result: list[Message] = [self._to_msg(summaries[0])]
        result += [self._to_msg(m) for m in non_summaries]
        return result[:limit]

    def add_summary_message(self, content: str) -> str:
        return self.add_message("summary", content)

    def has_summary(self) -> bool:
        return len(self._core.fetch(limit=1, role="summary")) > 0

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

    def clear(self) -> None:
        self._core.clear()


_stores: dict[str, MessageStore] = {}


def get_message_store(user_id: str = "default_user", workspace_id: str = "personal") -> MessageStore:
    key = f"{user_id}:{workspace_id}:msgstore"
    if key not in _stores:
        _stores[key] = MessageStore(user_id, workspace_id=workspace_id)
    return _stores[key]


def clear_message_store(user_id: str, workspace_id: str) -> None:
    """Evict a MessageStore from the cache (e.g. after workspace deletion)."""
    key = f"{user_id}:{workspace_id}:msgstore"
    _stores.pop(key, None)
