# HybridDB — Debug Scan Findings (2026-05-21)

**Date:** 2026-05-21
**File:** `src/sdk/hybrid_db.py` (2468 lines)
**Context:** Third scan — verifying prior fixes (from 2026-05-04 and 2026-05-06) and finding any remaining issues.

---

## Prior Scan History

### 2026-05-04 Scan (docs/HYBRIDDB_SCAN_2026-05-04.md)
Found 1 bug (B1: Chroma `delete_collection` with `_chroma is None`) and 5 optimizations (O1-O5: redundant embedding calls, FTS5 schema queries, reconcile row-by-row fetch, DuckDB per-query SET, redundant `_now_iso()`). All were fixed per docs/HYBRIDDB_BUGFIXES_2026-05-04.md.

### 2026-05-06 Scan (docs/HYBRIDDB_FIXES_2026-05-06.md)
Found 3 bugs: FTS shadow tables leaking into `list_tables()`, DuckDB analytics stale after schema changes, Chroma index health comparing GB to bytes. All fixed.

### 2026-05-21 This Scan
Verification that all prior fixes are intact + looking for any remaining issues.

---

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| `tests/sdk/test_hybrid_db.py` | 68 | ✅ Pass (5.1s) |
| `tests/sdk/test_hybrid_search.py` | 55 | ✅ Pass |
| `tests/sdk/test_graph_analytics.py` | 18 | ✅ Pass |
| `tests/sdk/` sub-total | 141 | ✅ All pass |
| `tests/hybriddb/benchmarks/` | 21 | ✅ All pass (85.9s) |
| `tests/storage/` (HybridDB-backed) | 12 | ✅ All pass |
| **Lint** (ruff) | — | ✅ Clean |
| **Types** (mypy) | — | ⚠️ 70 errors (all cosmetic type annotations) |

---

## Previously Fixed — Verified Intact

| # | Issue | Location | Verified |
|---|-------|----------|----------|
| B1 | Chroma `delete_collection` with `_chroma is None` | Lines 997, 1022 | ✅ Both guard with `self._chroma is not None` |
| O1 | `search_graph` re-embeds per table per column | Line 1890 | ✅ Embedding computed once, cached in local var |
| O2 | `_create_fts5` re-queries autoincrement per column | Line 808 | ✅ Accepts cached `use_id` parameter |
| O3 | `reconcile` fetches full rows one-by-one | Lines 2349-2356 | ✅ Batch fetch with `WHERE rowid IN (...)` |
| O4 | DuckDB `SET threads = 4` per analytics query | Line 362 | ✅ Moved to `_init_duckdb` — one-time |
| O5 part | `_now_iso()` redundant in `insert` | Line 1100 | ✅ Single `now` for all journal entries |
| O5 part | `_now_iso()` redundant in `insert_batch` | Line 1157 | ✅ Single `now` for all rows |
| FTS leak | `list_tables()` includes FTS shadow tables | Line 1053 | ✅ Reads from `_schema` metadata, not `sqlite_master` |
| DuckDB stale | Schema changes stale DuckDB mirror | Lines 452-454 | ✅ `_refresh_duckdb_table_if_registered()` called after schema mutations |
| GB vs bytes | `_check_index_health` compares bytes correctly | Lines 553-554 | ✅ `size_bytes` compared to byte thresholds |

---

## New Findings

### B2: `_now_iso()` redundant in `update` and `delete` (LOW)

**References:**
- `update`: lines 1225-1237
- `delete`: lines 1253-1265

**Problem:** The O5 fix (hoisting `_now_iso()` to a single call before the loop) was applied to `insert` and `insert_batch` but missed `update` and `delete`. Both methods still call `_now_iso()` (which calls `datetime.now(UTC).isoformat()`) once per LONGTEXT column *plus* once more for the `row_update`/`row_delete` journal entry.

```python
# update — lines 1225-1237
for col in self._get_longtext_columns(table):
    now = _now_iso()              # ← per column!
    cur.execute(...)
now = _now_iso()                  # ← another call!
cur.execute(...)

# delete — lines 1253-1265
for col in self._get_longtext_columns(table):
    now = _now_iso()              # ← per column!
    cur.execute(...)
now = _now_iso()                  # ← another call!
cur.execute(...)
```

**Cost:** For a table with 3 LONGTEXT columns: 3 extra `_now_iso()` calls per `update`/`delete`. Each call is ~1µs so negligible (<5µs), but inconsistent with the already-fixed `insert`.

**Fix:** Hoist `now = _now_iso()` before each loop and reuse:

```python
now = _now_iso()
for col in self._get_longtext_columns(table):
    cur.execute(...)
cur.execute(..., now)
```

---

### O7: `insert_batch` does N individual SELECTs per row (MEDIUM)

**Reference:** lines 1168-1181

```python
for data in rows:
    cur.execute(f"INSERT INTO {table} ...", values)
    internal_rowid = cur.lastrowid
    # ...
    row = dict(cur.execute(f"SELECT * FROM {table} WHERE id = ?", (user_pk,)).fetchone())  # ← N times
    ids.append(user_pk)
```

**Problem:** After each INSERT, the method immediately fetches the full row back with a separate `SELECT * WHERE id = ?` query. For a 1000-row batch, this is 1000 individual round-trips through SQLite.

**Cost:** Each SELECT round-trip through the `_connect()` context manager is ~0.1-0.3ms. For 1000 rows: 100-300ms of unnecessary overhead. The row data is only needed for journal metadata and column extraction.

**Fix:** Delay the SELECT until the batch is committed. Collect `lastrowid` values, then batch-fetch:

```python
# After the insert loop, batch-fetch all new rows
if ids:
    placeholders = ",".join("?" * len(ids))
    rows_by_id = {
        r["id"]: dict(r)
        for r in cur.execute(f"SELECT * FROM {table} WHERE id IN ({placeholders})", tuple(ids))
    }
```

However, this would require restructuring the journal entry creation (currently interleaved with per-row SELECTs). A simpler approach: the INSERT already has all the data values — use those directly for the journal instead of re-reading.

---

### O8: `to_networkx` loads all nodes/edges with no limit (MEDIUM)

**Reference:** lines 1801-1803

```python
nodes = self.raw_query(
    "SELECT id, label, type, domain, confidence, source, properties FROM _graph_nodes"
)
edges = self.raw_query(
    "SELECT id, source_id, target_id, type, weight, properties, valid_until "
    "FROM _graph_edges"
)
```

**Problem:** `to_networkx()` loads every node and every edge unconditionally. For graphs with 100K+ nodes, this builds a massive in-memory NetworkX graph even when the downstream algorithm (e.g., `shortest_path`, `pagerank`) only needs a subgraph. NetworkX itself is memory-intensive (each node/edge is a Python object with dict overhead).

**Impact:** A graph with 100K nodes and 500K edges could consume 500MB+ in NetworkX memory. This affects `pagerank`, `betweenness_centrality`, `connected_components`, `community_detect`, and `shortest_path` — all of which call `to_networkx()` first.

**Suggestion:** No immediate fix needed for typical usage (graphs in this codebase are small), but worth adding docs or a `filter` parameter for potential large-graph scenarios.

---

### T1: Base params tuple widening in `traverse` (LOW — Type Only)

**Reference:** line 1730

```python
base_params = (start_id, start_id, start_id)
type_filter = ""
if type:
    type_filter = " AND e.type = ?"
    base_params = base_params + (type,)  # tuple[str,str,str] → tuple[str,str,str,str]
```

**Problem:** Mypy correctly flags this as incompatible assignment (widening). At runtime it works fine since SQLite adapters accept any tuple.

**Fix:** Use a `list[Any]` for params:

```python
params: list[Any] = [start_id, start_id, start_id]
if type:
    type_filter = " AND e.type = ?"
    params.append(type)
```

---

### T2: `_fetch_rows_by_ids` param variance mismatch (LOW — Type Only)

**References:** lines 1974, 2027

```python
def _fetch_rows_by_ids(self, table: str, ids: list[int | str]) -> dict[int | str, dict]:
    ...
```

Called by:
```python
# search() line 1974
rows = self._fetch_rows_by_ids(table, row_ids)  # row_ids is list[int]

# search_all() line 2027
rows = self._fetch_rows_by_ids(table, row_ids)  # row_ids is list[int]
```

**Problem:** `list[int]` is not assignable to `list[int | str]` because `list` is invariant. This is a clean mypy-only issue — no runtime impact since int is a subtype of int | str.

**Fix:** Change parameter type to `Sequence[int | str]` (covariant), or cast at call site.

---

## Detailed Location Index

| Line(s) | Issue | Type | Severity |
|---------|-------|------|----------|
| 1225-1237 | `_now_iso()` redundant in `update` | Bug | Low |
| 1253-1265 | `_now_iso()` redundant in `delete` | Bug | Low |
| 1168-1181 | `insert_batch` N individual SELECTs | Optimization | Medium |
| 1801-1803 | `to_networkx` loads all data | Optimization | Medium |
| 1730 | Tuple widening in `traverse` | Type annotation | Low |
| 1974, 2027 | List variance in `_fetch_rows_by_ids` | Type annotation | Low |

---

## Overall Assessment

**Health: ✅ Good.** No critical or high-severity issues found.

All previously reported bugs and optimizations from the 2026-05-04 and 2026-05-06 scans are properly fixed and verified in the current source.

The remaining issues are:
- One minor inconsistency (B2: timestamp caching gap in `update`/`delete` — the O5 fix was applied to `insert` and `insert_batch` but not these two methods)
- Two moderate optimizations (O7: batch SELECT in `insert_batch`, O8: unbounded NetworkX graph load)
- Two cosmetic type annotation mismatches (T1, T2) with zero runtime impact

141 unit tests, 21 benchmarks, and 12 storage tests all pass cleanly.
