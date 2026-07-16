# MessageStore on CoreMem — Refactor Design

**Date:** 2026-05-31
**Status:** Design
**Author:** Agent

## Context

### The two systems

The EA codebase grew two independent storage layers for conversation messages.

**MessageStore** (`src/storage/messages.py`, ~355 lines) is the original EA storage, wrapping Assistant's custom `HybridDB` (forked from the OSS `hybriddb` package with added embedding model validation, DuckDB sync, and edge rules). It provides CRUD: `add_message`, `get_recent_messages`, `get_messages_by_session_id`, `get_messages_with_summary`, `delete_messages_for_workspace`, and three search methods (`search_keyword`, `search_vector`, `search_hybrid`). It owns the `id INTEGER PRIMARY KEY AUTOINCREMENT` schema.

**CoreMem** (`CoreMem/coremem/`, ~800 lines total) is a retrieval-focused OSS library wrapping the OSS `hybriddb.HybridDB`. It provides `search_enhanced` (multi-query expansion + cross-encoder reranking + heuristics), `search`, `export`, `import_batch`. It is self-contained with no EA dependencies, and originally used `id TEXT PRIMARY KEY`.

Both connect to the same SQLite database (`conversation/app.db`) for the same `messages` table.

### The friction

During the metadata/filters implementation (May 2026) we discovered three classes of conflict:

1. **Schema mismatch** — MessageStore creates `id INTEGER PRIMARY KEY AUTOINCREMENT`. CoreMem's `_ensure_tables()` declared `id TEXT PRIMARY KEY`. With `IF NOT EXISTS`, when MessageStore creates first, CoreMem's table creation silently no-ops. But `ingest_batch()` with string UUIDs fails against the INTEGER column.

2. **Embedding model stamping** — OSS HybridDB stamps `_schema.embedding_model = "chroma:all-MiniLM-L6-v2"` on `create_table()`. Assistant's HybridDB checks `_schema.embedding_model` on connect and raises `EmbeddingModelError` if it doesn't match its own default `"unknown"`. Each system overwrites the other's stamp via `INSERT OR REPLACE`.

3. **Duplicate logic** — Both implement `json_extract` metadata queries independently (MessageStore in `get_messages_by_session_id`, `get_recent_messages_for_workspace`, `delete_messages_for_workspace`; CoreMem in `_matches_filters()` and `list()`).

### The 2026-05-31 band-aid

We fixed the schema mismatch: removed `id TEXT PRIMARY KEY` from `_ensure_tables()` and made `ingest_batch()` use HybridDB's auto-increment integer IDs. Both systems can now read and write to the same table. But the architecture is still two HybridDB instances, two stamping behaviors, duplicate code paths.

### Why refactor now

CoreMem is headed for PyPI. Before locking in the EA–OSS boundary, we need a clean answer: is MessageStore a separate storage system, or an adapter?

The usage data makes it clear: MessageStore's three search methods (`search_keyword`, `search_vector`, `search_hybrid`) are never called from production code. EA tools only go through `core.search_enhanced()`. MessageStore has become a CRUD wrapper — 15 methods for insert, list, delete, with no retrieval intelligence.

CoreMem already handles all CRUD operations (`ingest`, `export`, `import_batch`, `clear`). It's missing only `delete(filters)` and an optional `embedding` param on `ingest`. Once those land, MessageStore becomes a ~50-line adapter.

The refactor eliminates:
- Two HybridDB instances on one database
- Embedding model stamping conflicts
- Duplicated SQL and schema definitions
- ~200 lines of boilerplate
- Future maintenance burden of two storage paths

## Goal

Make MessageStore a thin adapter over MemoryCore. Every MessageStore method delegates to CoreMem. All DB writes and reads flow through one path.

## Current Interface Audit

### MessageStore methods called from production code

| Method | Call sites | CoreMem equivalent |
|--------|-----------|-------------------|
| `add_message(role, content, metadata)` | ws.py, conversation.py (many), runner.py | `core.ingest(role, content, metadata=metadata)` |
| `get_recent_messages(count, offset)` | middleware_observation.py | `core.export(limit=count, offset=offset)` |
| `get_recent_messages_for_workspace(ws_id, count)` | conversation.py | `core.export(limit=count, filters={"workspace_id": ws_id})` |
| `get_messages_by_session_id(sid, limit)` | message.py (tools) | `core.export(limit=limit, filters={"session_id": sid})` |
| `get_messages(start_date, end_date, limit)` | companion_scheduler.py | `core.export(limit=limit)` with post-filter |
| `get_messages_with_summary(limit)` | ws.py, conversation.py (many) | `core.export(limit=limit)`, extract summary in Python |
| `add_summary_message(content)` | runner.py | `core.ingest("summary", content)` |
| `has_summary()` | conversation.py | check `core.export(limit=1, filters={"role": "summary"})` non-empty |
| `count_messages(start_date, end_date)` | message.py (tools) | `core.count()` |
| `delete_messages_for_workspace(ws_id)` | workspaces.py | **Not in CoreMem** — needs `core.delete(filters=...)` |
| `clear()` | (only tests) | `core.clear()` |
| `search_hybrid(query, ...)` | message.py (tools) | Already replaced by `core.search_enhanced()` — only called from `message_count` tool |

### MessageStore methods NOT called from production code

| Method | Used in | Action |
|--------|---------|--------|
| `add_message_with_embedding(...)` | tests + benchmarks only | Keep as thin wrapper on `core.ingest(embedding=...)` |
| `search_keyword(query, limit)` | nowhere | Drop — callers use `core.search()` instead |
| `search_vector(query, limit)` | nowhere | Drop — callers use `core.search()` instead |

### CoreMem API (public methods on MemoryCore)

```
ingest(role, content, session_id=None, metadata=None) -> str
ingest_many(messages, session_id=None) -> list[str]
search(query, limit=10, filters=None) -> list[SearchResult]
search_enhanced(query, limit=10, filters=None, depth=5) -> list[SearchResult]
wake_up(user_id, session_id=None) -> str
deep_search_context(query, limit=10) -> str | None
export(filters=None, limit=1000, offset=0) -> list[Memory]
export_all(filters=None) -> list[Memory]
import_batch(memories) -> list[str]
count() -> int
clear() -> None
warmup() -> None
```

**Missing for MessageStore parity:**
- `delete(filters)` — filtered delete
- `ingest()` optional `embedding` param

## Design

### Phase 1: CoreMem additions (CoreMem OSS, ~2hrs)

#### 1a. Add `delete()` to `StoreBackend`

```python
@abstractmethod
def delete(self, filters: dict | None = None) -> int:
    """Delete memories matching filters. Returns count deleted."""
    ...
```

#### 1b. Implement `delete()` on both backends

**ChromaBackend:**
```python
def delete(self, filters=None) -> int:
    result = self._collection.get(where=filters)
    ids = result["ids"]
    if ids:
        self._collection.delete(ids=ids)
    return len(ids)
```

**HybridBackend:**
```python
def delete(self, filters=None) -> int:
    parts, params = self._build_where_clause(filters or {})
    where = " AND ".join(parts) if parts else "1"
    rows = self._db.query("messages", where=where, params=tuple(params))
    ids = [r["id"] for r in rows]
    if not ids:
        return 0
    self._delete_with_journal(ids)
    return len(ids)

def _delete_with_journal(self, ids: list[int]) -> None:
    """Delete rows, journal entries, ChromaDB vectors, and sync DuckDB."""
    with self._db._connect() as cur:
        placeholders = ",".join("?" for _ in ids)
        cur.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        cur.execute(
            f"DELETE FROM _journal WHERE app_table = 'messages' AND row_id IN ({placeholders})",
            ids,
        )
    if self._db._chroma is not None:
        try:
            self._db._chroma.delete(
                collection_name="messages_content",
                ids=[str(i) for i in ids],
            )
        except Exception:
            pass
    self._db.sync_duckdb_table("messages")
```

This mirrors the exact pattern MessageStore already uses in `delete_messages_for_workspace` (delete SQL rows, delete journal entries, delete ChromaDB vectors, sync DuckDB). OSS HybridDB v0.3.0 doesn't have `delete_batch()` — if a future release adds it, switch to that.

**Note:** The ChromaDB collection name `messages_content` is the convention set by OSS HybridDB's `create_table` + LONGTEXT column naming: `{table}_{column}`. This matches the existing MessageStore convention.

#### 1c. Add `delete()` to `MemoryCore`

```python
def delete(self, filters: dict[str, Any] | None = None) -> int:
    return self._backend.delete(filters=filters)
```

#### 1d. Add optional `embedding` param to `StoreBackend.ingest`

```python
@abstractmethod
def ingest(self, memory: Memory, embedding: list[float] | None = None) -> str:
    ...
```

**ChromaBackend:** pass inline `embeddings=[embedding]` to `_collection.add()` when provided. Only used for single-insert calls.

**HybridBackend:** call `self._db.vector_upsert()` after `insert_batch()` when `embedding` is provided.

#### 1e. Extract `_build_where_clause()` helper on HybridBackend

Currently duplicated between `search()` (post-filter via `_matches_filters`) and `list()` (SQL `json_extract`). Pull into a shared method:

```python
def _build_where_clause(self, filters: dict) -> tuple[list[str], list[str]]:
    """Build SQL WHERE parts and params from metadata equality filters."""
    parts, params = [], []
    for key, value in filters.items():
        safe_key = key.replace("'", "''")
        parts.append(f"json_extract(metadata, '$.{safe_key}') = ?")
        params.append(str(value))
    return parts, params
```

`search()` can use this for SQL-level filtering if HybridDB's `search()` supports it, or keep the existing in-memory `_matches_filters` post-filter. `list()` already uses the SQL path — just extract the shared string-building logic.

### Phase 2: Rewrite MessageStore (EA, ~3hrs)

#### 2a. Constructor

```python
from coremem.backends.hybrid import HybridBackend
from coremem.core import MemoryCore

class MessageStore:
    def __init__(self, user_id, base_dir=None, workspace_id="personal"):
        if base_dir is None:
            base_dir = get_paths(user_id).conversation_dir()
        self._core = MemoryCore(backend=HybridBackend(path=str(base_dir)))
        self._workspace_id = workspace_id
```

No more EA `HybridDB` import. No `create_table`, `_ensure_tables`, `_init_chroma`. The table is managed exclusively by CoreMem's `HybridBackend._ensure_tables()`.

#### 2b. Keep EA dataclasses (backward compat)

```python
@dataclass
class Message:
    id: int          # int for backward compat with HTTP JSON serialization
    ts: datetime
    role: str
    content: str
    metadata: dict | None = None

@dataclass
class SearchResult:
    id: int
    content: str
    ts: datetime
    role: str
    score: float
```

#### 2c. Delegation methods

```python
def add_message(self, role, content, metadata=None) -> int:
    return int(self._core.ingest(role, content, metadata=metadata))

def add_message_with_embedding(self, role, content, embedding,
                                metadata=None) -> int:
    return int(self._core.ingest(role, content, metadata=metadata,
                                 embedding=embedding))

def get_messages(self, start_date=None, end_date=None, limit=None) -> list[Message]:
    memories = self._core.export(limit=limit or 10000)
    # CoreMem returns newest-first (ts DESC). Reverse for chronological order.
    memories = list(reversed(memories))
    if start_date or end_date:
        memories = [m for m in memories if self._in_date_range(m.ts, start_date, end_date)]
    return [self._to_msg(m) for m in memories]

def get_recent_messages(self, count=100, offset=0) -> list[Message]:
    # export() returns newest-first. Reverse for oldest-first (backward compat).
    memories = self._core.export(limit=count, offset=offset)
    return [self._to_msg(m) for m in reversed(memories)]

def get_recent_messages_for_workspace(self, ws_id, count=100) -> list[Message]:
    memories = self._core.export(limit=count, filters={"workspace_id": ws_id})
    return [self._to_msg(m) for m in reversed(memories)]

def get_messages_by_session_id(self, session_id, limit=50) -> list[Message]:
    memories = self._core.export(limit=limit, filters={"session_id": session_id})
    # export() orders by ts DESC. Keep as-is (callers expect newest-first).
    return [self._to_msg(m) for m in memories]

def get_messages_with_summary(self, limit=50) -> list[Message]:
    memories = self._core.export(limit=limit)
    summaries = [m for m in memories if m.role == "summary"]
    non_summaries = [m for m in memories if m.role != "summary"]
    if not summaries:
        return [self._to_msg(m) for m in reversed(memories)]
    # Summary first, then non-summary messages in chronological order
    latest = summaries[0]  # newest-first from export()
    result: list[Message] = [self._to_msg(latest)]
    result += [self._to_msg(m) for m in reversed(non_summaries)]
    return result[:limit]

def add_summary_message(self, content) -> int:
    return self.add_message("summary", content)

def has_summary(self) -> bool:
    return len(self._core.export(limit=1, filters={"role": "summary"})) > 0

def count_messages(self, start_date=None, end_date=None) -> int:
    return self._core.count()

def delete_messages_for_workspace(self, ws_id) -> int:
    return self._core.delete(filters={"workspace_id": ws_id})

def clear(self) -> None:
    self._core.clear()

def search_hybrid(self, query, **kwargs) -> list[SearchResult]:
    # Deprecated. EA tools now use core.search_enhanced() directly.
    # Keep for backward compat.
    results = self._core.search(query, limit=kwargs.get("limit", 10))
    return [self._to_sr(r) for r in results]

# -- Helpers --

def _to_msg(self, m: Memory) -> Message:
    return Message(
        id=int(m.id),
        ts=m.ts,
        role=m.role,
        content=m.content,
        metadata=m.metadata,
    )

def _to_sr(self, r: SearchResult) -> "SearchResult":
    """Convert CoreMem SearchResult → EA SearchResult."""
    return SearchResult(
        id=int(r.memory.id),
        content=r.memory.content,
        ts=r.memory.ts,
        role=r.memory.role,
        score=r.score,
    )

@staticmethod
def _in_date_range(ts: datetime | None,
                   start_date: date | None,
                   end_date: date | None) -> bool:
    if not ts:
        return False
    d = ts.date()
    if start_date and d < start_date:
        return False
    if end_date and d > end_date:
        return False
    return True
```

#### 2d. Removed helpers

| Helper | Reason |
|--------|--------|
| `_rows_to_messages` | Replaced by `_to_msg` |
| `_rows_to_search_results` | Replaced by `_to_sr` |
| `_parse_metadata` | Handled by CoreMem (metadata is already parsed in `Memory.metadata`) |
| `_date_filter_bounds` | Replaced by `_in_date_range` (post-filter) |

### Phase 3: Cleanup (EA)

- Remove `from src.sdk.hybrid_db import HybridDB` import from `messages.py`
- Remove unused imports: `Path`, `UTC`, `date` (check all are still needed by the adapter)
- Verify no other module breaks — other importers of `MessageStore` use the CRUD API and never access `.db` directly
- Run full test suite

## Semantic Differences

| Aspect | Before | After | Impact |
|--------|--------|-------|--------|
| Summary ordering | `id > summary_id` (`ORDER BY id DESC`) | `ts DESC` (newest-first, reversed for oldest-first) | Equivalent in practice — `ts` is set to `now()` at insert |
| Date queries | SQL-level `ts >= ? AND ts <= ?` | Python post-filter | <10K messages, negligible perf diff |
| `search_hybrid` | Tunable FTS/recency weights | `core.search()` with fixed weights | Only called from `message_count` tool — weights were `recency_weight=0.1`, ignored in new path. Verify `message_count` quality. |
| MessageStore `.db` attribute | EA `HybridDB` instance | **Removed** — no `.db` access | Only `storage/messages.py` itself accessed `.db`. But `test_messages_store.py` and `benchmarks/longmemeval_adapter.py` access it directly. |

## Open Questions

1. **DuckDB sync** — MessageStore currently calls `self.db.register_duckdb_table("messages")` on init and `sync_duckdb_table` on delete/clear. OSS HybridDB already has DuckDB sync built in via `_auto_register_duckdb_tables`. Verify this is true for the OSS version in use before removing the manual calls.

2. **Date-range performance** — `get_messages` fetches up to 10K and post-filters in Python. For companion scheduler (the only caller), this is a once-daily query. Acceptable. If needed later, add `date_range` param to `export()`.

3. **`id` type in HTTP** — Assistant's HTTP API returns `Message.id` as `int`. CoreMem returns `str`. Adapter converts via `int(m.id)`. Safe cast since IDs are auto-increment integers from HybridDB.

4. **`add_message_with_embedding`** — Only used in tests/benchmarks, never in production. Keep for backward compat but the production path (`add_message`) never uses custom embeddings.

5. **Test access to `.db`** — `tests/storage/test_messages_store.py` and `benchmarks/longmemeval_adapter.py` access `store.db` directly. After the refactor, `store.db` no longer exists. These need to:
   - Call `store.add_message()` instead of `store.db.insert()`
   - Or access `store._core._backend._db` for raw HybridDB operations (if essential for test isolation)

## Risks

- **ChromaDB cleanup on delete** — Must match MessageStore's pattern exactly: delete SQL rows, delete journal entries, delete ChromaDB vectors via direct API, sync DuckDB. If any step fails, orphan vectors or stale DuckDB data may remain. Same risk as current implementation.
- **Embedding model stamping eliminated** — MessageStore no longer imports EA HybridDB, so the `_init_chroma` validation is never triggered. CoreMem's OSS HybridDB stamps `chroma:all-MiniLM-L6-v2` in `_schema`. This is only a problem if something else creates an EA HybridDB at the same path — nothing does in the conversation-path code path.
- **`search_hybrid` weights change** — The `message_count` tool passes `recency_weight=0.1`. CoreMem's `search()` doesn't use recency weight (it applies heuristics differently). The quality impact is small since `message_count` already runs `expand_queries` + `search_hybrid` across all queries, then deduplicates. Verify before shipping.

## Success Criteria

1. All 54 existing message tests pass (`tests/sdk/test_messages.py` + `tests/storage/test_messages_store.py`)
   - May need minor test updates for `.db` attribute removal and `SearchResult` type changes
2. CoreMem 33 tests pass
3. `message_history`, `message_search`, `message_count`, `message_timeline` tools work correctly
4. Workspace deletion works (HTTP `DELETE /workspaces/:id`)
5. Conversation HTTP endpoints return correct messages, summaries, and metadata
6. Companion scheduler start-of-day summary works
7. Observation middleware gets correct recent messages
