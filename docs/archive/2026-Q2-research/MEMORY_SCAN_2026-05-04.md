# Memory Store — Scan Findings (Peer Review)

**Date:** 2026-05-04
**File:** `src/storage/memory.py` (1741 lines)

---

## Bugs

### B1: `reconcile_vectors` runs once then breaks — loop logic broken

**Status:** Fixed on 2026-05-07. `reconcile_vectors()` now delegates once to `HybridDB.reconcile("memories")` without a row-query gate.

**Reference:** lines 933-942

```python
def reconcile_vectors(self, limit: int = 100) -> int:
    rows = self.db.query("memories", where="is_superseded = 0", limit=limit)
    for row in rows:
        r = self.db.reconcile("memories")  # ← reconciles ENTIRE table
        reconciled += r.get("missing_added", 0)
        break  # ← breaks after first row
```

`db.reconcile("memories")` reconciles the *entire* memories table — all columns, all rows.
Running it per-row makes no sense. The `break` after one iteration makes the loop a no-op.
The `limit` parameter affects how many rows are returned from `query`, but the reconcile
itself is table-level.

**Actual effect:** Reconciles the entire table once (on the first row), then returns.
Functions correctly for the `limit >= 1` case, but the loop structure implies it was
meant to reconcile per-column or per-batch. The returned `reconciled` count reflects
the whole-table reconciliation, not per-column or per-batch.

**Fix:** Drop the loop:

```python
def reconcile_vectors(self) -> int:
    r = self.db.reconcile("memories")
    return r.get("missing_added", 0)
```

---

### B2: `add_memories_batch` missing `maybe_decay_confidence`

**Status:** Fixed on 2026-05-07. Batch insertion now runs confidence decay once per batch.

**Reference:** lines 1010-1070 vs line 434

`add_memory` calls `self.maybe_decay_confidence()` at line 434. `add_memories_batch` never
calls it. Over time, batch-inserted memories accumulate without decay, while single-inserted
memories are properly maintained.

**Impact:** Memory quality drifts for applications that use batch insertion (LLM extraction
pipelines). Stale learned memories persist at inflated confidence.

**Fix:** Add `self.maybe_decay_confidence()` at the top of `add_memories_batch`.

---

### B3: `_find_current_fact` fallback scans up to 1000 rows without index

**Status:** Mitigated on 2026-05-07. The fallback remains for unmigrated stores, but now emits a warning when used so stale/unmigrated stores are visible operationally.

**Reference:** lines 566-576

```python
rows = self.db.query(
    "memories",
    where="memory_type = ? AND is_superseded = 0 AND scope = ?",
    params=(MEMORY_TYPE_FACT, scope),
    limit=1000,
)
for row in rows:
    memory = self._row_to_memory(row)
    if memory.structured_data.get("fact_key") == fact_key:
        return memory
```

When `memory_facts` table lookup misses, scans up to 1000 fact memories, deserializing
`structured_data` JSON per row. The `fact_key` is inside a JSON blob, not an indexed column.

**Impact:** For memory stores with many facts that don't use `memory_facts` indexing (pre-migration),
every fact lookup does O(n) scan with JSON deserialization.

**Fix:** After `migrate_structured_facts()` runs, this fallback should rarely trigger. Add a
warning log when the fallback is hit to detect unmigrated stores.

---

### B4: `add_memory` calls `maybe_decay_confidence` on every insert

**Status:** Fixed on 2026-05-07. Single inserts no longer trigger the full decay scan; batch inserts run it once.

**Reference:** line 434

Every call to `add_memory` triggers a full decay scan of up to 10000 stale memories + 10000
low-confidence deletions. For burst inserts (e.g., LLM extraction producing 50 memories),
this is 50 full decay scans.

**Impact:** Minor — `maybe_decay_confidence` returns fast when there's nothing to decay.
But it does 2 SQL queries per `add_memory` call regardless.

**Fix:** Throttle — only run decay every N inserts or every M minutes. Since HybridDB is
single-threaded per instance, a simple counter suffices:

```python
self._decay_counter = (self._decay_counter or 0) + 1
if self._decay_counter % 50 == 0:
    self.maybe_decay_confidence()
```

---

## Optimization Opportunities

### O1: `_row_to_memory` + `get_memory` double DB query pattern

**Status:** Partially fixed on 2026-05-07. `search_semantic()` and `search_hybrid()` now reuse search result rows directly instead of issuing an extra `get_memory()` query.

**References:** `search_hybrid` line 1262, `search_facts` line 785, `find_fact_history_for_query` line 905

```python
mem = self.get_memory(mid)  # ← does db.get() + _row_to_memory
```

But the caller often already has the row dict from a search result. `get_memory` does a second
`db.get()` query when the row dict is already available.

**Fix:** Add `_dict_to_memory` or accept an optional `row` parameter:

```python
def get_memory(self, memory_id: str, row: dict | None = None) -> Memory | None:
    if row is not None:
        return self._row_to_memory(row)
    row = self.db.get("memories", memory_id)
    if not row:
        return None
    return self._row_to_memory(row)
```

### O2: `search_facts` boosts access on ALL results, even those that won't be returned

**Reference:** lines 792-793

```python
for memory in results:
    self._boost_access(memory.id)
```

Boosts access after building results. If `limit=8` but search returned 20 results after dedup,
only 8 are returned but all 20 get boosted. Minor — the boost loop is inside `search_facts`
which iterates only visible results.

### O3: `get_stats` queries up to 10000 rows including superseded

**Status:** Fixed on 2026-05-07. Stats now report `total` and `active_total`, while domain/type/source/scope breakdowns and average confidence are computed from active, non-superseded memories.

**Reference:** line 1659

```python
rows_by_domain = self.db.query("memories", limit=10000)
```

No `is_superseded = 0` filter. The 10000 rows include superseded memories. Since superseded are
filtered out in the loop at line 1677 for confidence stats, the superseded rows contribute
nothing except to domain/type counts (which may or may not be desired to include superseded).

**Fix:** Add `where="is_superseded = 0"` for consistent stats.

### O4: `add_memory` has 4 similar code paths with duplicate logic

**Status:** Fixed on 2026-05-07. Learned-confidence cap logic is centralized through helper methods and exported as `INITIAL_LEARNED_CONFIDENCE_CAP`.

**Reference:** lines 465-541

The `if existing / elif is_update / else` branches all construct similar update/insert dicts.
The `min(confidence, MAX_CONFIDENCE if ...)` cap is repeated 4 times. Could be extracted.

**Fix:** Extract cap computation:

```python
cap = MAX_CONFIDENCE if source == SOURCE_LEARNED else 1.0
confidence = min(confidence, cap)
```

### O5: `get_compact_context` sorts full list to select top N

**Status:** Fixed on 2026-05-07. Compact context now uses `heapq.nlargest()` instead of sorting the full candidate list.

**Reference:** line 1528

```python
for m in sorted(memories, key=lambda x: (-x.confidence, -x.observations))[:max_memories]:
```

`memories` is at most `max_memories * 2` (line 1523: `limit=max_memories * 2`). Sorting 10
items to get top 5 is fine. Not a real problem but `heapq.nlargest` avoids full sort.

### O6: `search_fts` duplicate query pattern

**Status:** Fixed on 2026-05-07. `search_fts()` now uses a single `HybridDB.search_all()` call and deduplicates returned rows.

**Reference:** lines 1199-1200

```python
results = self.db.search("memories", "trigger", query, ...)
results += self.db.search("memories", "action", query, ...)
```

Two separate FTS5 queries, then manual dedup. `db.search_all` does this across all columns
with proper scoring. Using `search_all` with `SearchMode.KEYWORD` would be simpler and
return properly scored results.

---

## Design Notes

### N1: `_boost_access` MAX_CONFIDENCE naming is misleading

**Status:** Addressed on 2026-05-07. Added `INITIAL_LEARNED_CONFIDENCE_CAP` as the explicit exported name for the learned-memory initial storage cap; access boosting still uses `MAX_CONFIDENCE + MAX_CONFIDENCE_BOOST_FROM_ACCESS`.

**Reference:** line 411-412

```python
"confidence": min(
    row["confidence"] + CONFIDENCE_BOOST_ON_ACCESS,   # +0.05
    MAX_CONFIDENCE + MAX_CONFIDENCE_BOOST_FROM_ACCESS, # 0.7 + 0.3 = 1.0
),
```

`MAX_CONFIDENCE = 0.7` governs initial storage (`add_memory` caps). Access boosting can push
confidence to 1.0. The variable name suggests an upper bound that's actually the initial cap.

### N2: Memory store cache has no eviction

**Status:** Fixed on 2026-05-07. Added bounded cache eviction with `_MEMORY_STORE_CACHE_MAX` and `clear_memory_store_cache()`.

**Reference:** lines 1709-1717

```python
_memory_store_cache: dict[str, MemoryStore] = {}
def get_memory_store(user_id, workspace_id="personal"):
    if key not in _memory_store_cache:
        _memory_store_cache[key] = MemoryStore(...)
    return _memory_store_cache[key]
```

No TTL, no size limit, no cleanup. For multi-user deployments, each user stays in cache
forever. Acceptable for single-user desktop use (the target).

### N3: `search_all` depends on `HybridDB.search_all` being synchronous

**Status:** Documented on 2026-05-07. No code change; `HybridDB.search_all()` remains synchronous by design, and callers continue to treat it as such.

**Reference:** line 1500

Calls `self.search_hybrid()` → `db.search_all()` which runs FTS5 + ChromaDB serially
across all text columns. No async parallelization path currently exists in HybridDB.

---

## Summary

| # | Type | Severity | Lines |
|---|---|---|---|
| B1 | Bug | Medium | 933-942 |
| B2 | Bug | Medium | 1010 |
| B3 | Bug | Low | 566-576 |
| B4 | Bug | Low | 434 |
| O1 | Optimization | Medium | 1262, 785, 905 |
| O2 | Optimization | Low | 792-793 |
| O3 | Optimization | Low | 1659 |
| O4 | Optimization | Low | 465-541 |
| O5 | Optimization | Low | 1528 |
| O6 | Optimization | Low | 1199-1200 |
| N1 | Note | Info | 411-412 |
| N2 | Note | Info | 1709-1717 |
| N3 | Note | Info | 1500 |
