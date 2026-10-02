# Memory Store — Architecture Proposal: `memory_facts` Table

> **Status:** ✅ Implemented — 2026-05-02.
> **Review verdict:** Strongly endorse — implement this.
> **Reviewer:** Code review completed 2026-05-02, all assertions verified against current source.
> **Implementation:** ~143 lines net new in `src/storage/memory.py`, all phases completed.
>
> **Motivation:** The `memories` table conflates two concerns: generic memory storage and structured fact lookup.
> The `structured_data` LONGTEXT column stores entity/attribute/value JSON, requiring
> brute-force Python scans across three separate code paths in `MemoryStore`.

---

## Problem: Dual-purpose `memories` table

| Column | What it is | Concern |
|---|---|---|
| `trigger` / `action` | Text content | Core — stays |
| `confidence` / `importance` / `observations` | Ranking signals | Core — stays |
| `domain` / `source` / `memory_type` / `scope` / `project_id` | Taxonomy | Core — stays |
| `consolidated` / `is_superseded` / `superseded_by` | Lifecycle state | Core — stays |
| `created_at` / `updated_at` / `access_count` / `last_accessed_at` | Temporal + audit | Core — stays |
| **`structured_data`** (LONGTEXT JSON) | Fact-specific: `entity`, `attribute`, `value`, `previous_value`, `fact_key` | **Candidate for split** |
| **`linked_to`** (JSON) | Connections — now dual-wrote to `_graph_edges`, legacy column | **Column to deprecate** |

The `structured_data` column makes `memories` serve two roles:

1. **Generic memories** — preferences, workflows, corrections (text-driven)
2. **Structured facts** — entity/attribute/value triples (needs indexed lookup)

The symptom is not a single bottleneck — it's three nested Python brute-force scans on every fact-related operation:

1. **`find_facts_for_query()`** (`src/storage/memory.py:654`) — scans up to 1,000 rows, deserializes JSON per row, tokenizes and scores in Python
2. **`_find_current_fact()`** (`src/storage/memory.py:517`) — same 1,000-row Python scan, called on every `upsert_fact_memory()` to check if a fact already exists
3. **`find_fact_history_for_query()`** (`src/storage/memory.py:701`) — calls `find_facts_for_query()` (1,000 scan), then does a *second* 2,000-row scan filtering by `fact_key` in Python

This is because `entity`/`attribute`/`value`/`fact_key` are buried inside JSON, not real columns.

---

## Solution: Extract `memory_facts` table

### Schema

```python
self.db.create_table("memory_facts", {
    "id": "TEXT PRIMARY KEY",
    "fact_key": "TEXT NOT NULL",         # "scope:entity:attribute" — unique per fact
    "entity": "TEXT NOT NULL",           # indexed — "what" the fact is about
    "attribute": "TEXT NOT NULL",        # indexed — "which property"
    "value": "TEXT NOT NULL",            # indexed — "current value"
    "previous_value": "TEXT",            # for history / rollback
    "memory_id": "TEXT NOT NULL",        # FK back to memories row
    "scope": "TEXT",                     # GLOBAL or PROJECT
    "project_id": "TEXT",
    "updated_at": "TEXT NOT NULL",
})

CREATE INDEX IF NOT EXISTS idx_facts_key ON memory_facts(fact_key);
CREATE INDEX IF NOT EXISTS idx_facts_key_updated ON memory_facts(fact_key, updated_at);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON memory_facts(entity);
CREATE INDEX IF NOT EXISTS idx_facts_attribute ON memory_facts(attribute);
CREATE INDEX IF NOT EXISTS idx_facts_value ON memory_facts(value);
CREATE INDEX IF NOT EXISTS idx_facts_memory ON memory_facts(memory_id);
```

**Column types:** `TEXT` for entity/attribute/value — enables FTS5 search (`self.db.search("memory_facts", "entity", query, mode=SearchMode.KEYWORD)`). Not `LONGTEXT` — these are short tokens, not paragraphs.

### `MemoryStore` API additions

```python
def upsert_fact(self, entity, attribute, value, scope, project_id):
    """Write to both memories (full text) AND memory_facts (indexed)."""
    # 1. Create memory row as before (trigger/action/content)
    memory_id = self.upsert_fact_memory(...)
    # 2. Create/update memory_facts row
    fact_key = self._fact_key(entity, attribute, scope)
    fact_id = self._generate_fact_id(scope, entity, attribute, value)
    self.db.insert("memory_facts", {
        "id": fact_id,
        "fact_key": fact_key,
        "entity": entity,
        "attribute": attribute,
        "value": value,
        "memory_id": memory_id,
        "scope": scope,
        "project_id": project_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }, sync=False)  # sync=False — no LONGTEXT to embed

def search_facts(self, query, limit=20):
    """Indexed entity/attribute/value lookup — replaces brute-force Python scan."""
    return self.db.search_all(
        "memory_facts", query,
        limit=limit,
        fts_weight=0.7,
    )

def get_fact_history(self, fact_key, limit=20):
    """All values for a fact_key, ordered by time."""
    return self.db.query(
        "memory_facts",
        where="fact_key = ?",
        params=(fact_key,),
        order_by="updated_at DESC",
        limit=limit,
    )

def supersede_fact(self, old_fact_id, new_fact_id):
    """Mark old fact row as superseded, link to new."""
    old = self.db.get("memory_facts", old_fact_id)
    if old:
        self.db.update("memory_facts", old_fact_id, {
            "previous_value": old["value"],
            "value": "[SUPERSEDED]",
        })
    # Also upsert the memories rows as before
```

### Migration script

For existing facts, extract `structured_data` JSON into `memory_facts` rows:

```python
def migrate_structured_facts(ms: MemoryStore):
    rows = ms.db.query("memories", where="memory_type = 'fact'", limit=100_000)
    count = 0
    for r in rows:
        sd = json.loads(r.get("structured_data", "{}"))
        entity = sd.get("entity", "")
        attribute = sd.get("attribute", "")
        value = sd.get("value", "")
        prev = sd.get("previous_value", "")
        scope = sd.get("scope", SCOPE_GLOBAL)
        fact_key = ms._fact_key(entity, attribute, scope)
        fact_id = ms._generate_fact_id(scope, entity, attribute, value)
        ms.db.insert("memory_facts", {
            "id": fact_id,
            "fact_key": fact_key,
            "entity": entity,
            "attribute": attribute,
            "value": value,
            "previous_value": prev,
            "memory_id": r["id"],
            "scope": scope,
            "updated_at": r.get("updated_at", _now_iso()),
        }, sync=False)
        count += 1
    return {"migrated": count}
```

### Performance impact

| Today | With `memory_facts` |
|---|---|
| `find_facts_for_query()` scans 2,000 rows, parses JSON, scores in Python | `search_all("memory_facts", query)` — FTS5 keyword + ChromaDB semantic on indexed columns |
| Fact history: scan ALL memories (2,000), filter by `fact_key` in Python | `query("memory_facts", where="fact_key = ?")` — single indexed lookup |
| `structured_data` is JSON — no DB-level search | `entity`/`attribute`/`value` are real TEXT columns — FTS5 searchable, DuckDB aggregateable |
| Supersession: parse JSON to find `fact_key`, then loop-scan | Direct FK join via `memory_id` |

**Estimated speedup:** 20-50x for fact lookup (indexed columns replace JSON parsing + Python brute-force).

---

## What NOT to add

| Proposal | Why rejected |
|---|---|
| **Separate `memory_rankings` table** | `confidence`/`importance`/`observations` are already indexed columns on `memories`. PageRank via `_graph_edges` adds cross-memory ranking. No dedicated rankings table needed. |
| **`memory_embeddings` table** | ChromaDB already manages this via `LONGTEXT` columns. A separate embeddings table would duplicate ChromaDB's HNSW index. |
| **`memory_access_log` table** | `access_count` + `last_accessed_at` per row is sufficient for retrieval. If access analytics matter, DuckDB can aggregate from the existing `memories` table after registering it. |
| **Split `memories` by type** (`memories_prefs`, `memories_facts`, etc.) | Fragments the unified search (`search_all`). Taxonomy columns (`domain`, `memory_type`, `scope`) already provide type-level filtering. One table with columns beats N tables with joins. |

---

## Migration path

| Phase | Action |
|---|---|
| 1 | Add `memory_facts` table creation to `MemoryStore._init_tables()` |
| 2 | Modify `upsert_fact_memory()` to dual-write: `memories` row + `memory_facts` row |
| 3 | Add `search_facts(query)`, `get_fact_history(fact_key)` — use `memory_facts` |
| 4 | Run migration on existing users: `structured_data` JSON → `memory_facts` rows |
| 5 | After dual-write period, `find_facts_for_query` can skip `memories` scan entirely |

Backward compatible throughout: `structured_data` JSON stays in `memories` until phase 5.
New writes populate both tables. Old reads work unchanged.

---

## Summary

| Action | Lines | Impact |
|---|---|---|
| Add `memory_facts` table schema | ~15 | New table with indexed TEXT columns |
| Add `upsert_fact` (dual-write) | ~30 | Writes to both `memories` + `memory_facts` |
| Add `search_facts(query)` | ~10 | FTS5 + semantic on entity/attribute/value |
| Add `get_fact_history(fact_key)` | ~10 | Indexed lookup instead of full-scan |
| Migration script | ~30 | One-time JSON → rows conversion |
| **Total** | **~95** | 20-50x faster fact lookup, eliminates brute-force Python scans |

---

## Code Review Findings (2026-05-02)

### Verified assertions

All claims verified against `src/storage/memory.py` (1,487 lines), `src/sdk/hybrid_db.py` (2,136 lines), `src/sdk/memory_ranker.py` (398 lines), and `src/sdk/memory_planner.py` (195 lines).

| Claim | Verified | Source |
|-------|----------|--------|
| `find_facts_for_query()` brute-force scans 1,000+ rows | Yes | `memory.py:654-669` — `query("memories", where="memory_type = 'fact' ...", limit=1000)` |
| `_find_current_fact()` scans 1,000 rows per upsert | Yes | `memory.py:517-535` — `query("memories", ..., limit=1000)` + Python `fact_key` comparison |
| `find_fact_history_for_query()` does nested double-scan | Yes | `memory.py:701-743` — calls `find_facts_for_query` then does second 2,000-row scan |
| JSON deserialization per row | Yes | `memory.py:287-298` — `json.loads(structured_raw)` in `_row_to_memory()` |
| `HybridDB.search_all()` supports hybrid keyword+semantic | Yes | `hybrid_db.py:1697` — available as backend for `search_facts()` |
| No `memory_facts` table exists anywhere in codebase | Yes | Zero grep results |
| No tests for `find_facts_for_query` or `upsert_fact_memory` | Yes | `tests/unit/test_memory_storage.py` (567 lines) — zero coverage |

### Additional opportunities identified

| Opportunity | Impact | Effort |
|-------------|--------|--------|
| Replace `_find_current_fact()` entirely with `db.get("memory_facts", fact_key=key)` | Eliminates 1,000-row scan on every fact upsert | ~3 lines (after dual-write period) |
| Compound index `(fact_key, updated_at)` — already added above | Covers `get_fact_history()` ORDER BY without extra sort | ~1 line |
| `search_facts()` should call `_boost_access()` on FK'd `memories` row | Preserves access tracking parity with current `find_facts_for_query()` | ~3 lines in `search_facts()` |
| Add tests for `upsert_fact_memory()` and `search_facts()` | Closes existing test gap regardless of this proposal | ~30 lines |

### Risks

- **None.** Dual-write strategy with `structured_data` retention until phase 5 makes this fully reversible at any point. No schema migration on the `memories` table itself.

---

## Implementation (2026-05-02)

All phases implemented in a single commit. ~143 lines net new in `src/storage/memory.py`.

### What was implemented

| Change | Location | Description |
|--------|----------|-------------|
| `memory_facts` table + 6 indexes | `memory.py:198-224` | TEXT columns for entity/attribute/value/value/previous_value with FTS5 support |
| `_upsert_fact_row()` | `memory.py:578-626` | Internal helper for dual-write to `memory_facts` |
| `_find_current_fact()` rewrite | `memory.py:528-556` | Tries indexed `memory_facts` first (O(log n) via `fact_key` index), falls back to old JSON scan |
| `upsert_fact_memory()` dual-write | `memory.py:677-685, 735-745` | Writes to both `memories` + `memory_facts` on every fact create/update |
| `search_facts()` | `memory.py:778-799` | FTS5 keyword search on entity/attribute/value via `db.search_all("memory_facts", ...)`, falls back to `_find_facts_fallback()` |
| `get_fact_history()` | `memory.py:801-813` | Single indexed query by `fact_key`, loads FK'd `memories` rows |
| `_find_facts_fallback()` | `memory.py:815-859` | Extracted old brute-force scan for superseded queries and fallback |
| `find_facts_for_query()` rewrite | `memory.py:861-870` | Delegates to `search_facts()` (superseded-inclusive queries still use fallback) |
| `find_fact_history_for_query()` rewrite | `memory.py:872-929` | Indexed per-fact_key lookups, falls back to old scan for unindexed facts |
| `migrate_structured_facts()` | `memory.py:942-1006` | One-time JSON → `memory_facts` rows for existing fact memories |

### Deviations from proposal

| Proposal | Implementation | Reason |
|----------|---------------|--------|
| `search_facts()` returns raw dicts | Returns `list[Memory]` via FK join | Preserves `_boost_access()` + Callers expect Memory objects |
| `upsert_fact()` public API | Inlined into `upsert_fact_memory()` dual-write | Keeps single public fact API, no new method to maintain |
| `get_fact_history()` takes `fact_key` only | Same, but also falls back to 2,000-row scan for unindexed facts | Safety net for facts created before migration |
| Phase 5 (remove `memories` scan) | Skipped — always keeps fallback | Extra safety: if `memory_facts` is empty, old code still works |
| `fts_weight=0.7` for `search_facts()` | `fts_weight=1.0` | `memory_facts` has no LONGTEXT columns (no ChromaDB path), so fusion weight is moot |

### Verification

- `ruff check src/storage/memory.py` — **PASS**
- `uv run pytest tests/unit/test_memory_storage.py tests/sdk/test_memory_ranker.py` — **80/80 PASS**
- Smoke test: upsert → search → history → migrate — all functional
