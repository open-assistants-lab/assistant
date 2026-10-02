# HybridDB — Second Scan Findings (Peer Review)

**Date:** 2026-05-04
**File:** `src/sdk/hybrid_db.py` (2345 lines)

---

## Bugs

### B1: `self._chroma.delete_collection()` with ChromaDB disabled (LOW)

**References:**
- `drop_column`: line 955 — `self._chroma.delete_collection(f"{table}_{column}")`
- `rename_column`: line 995 — `self._chroma.delete_collection(f"{table}_{old_name}")`

**Problem:** When `max_chroma_index_gb=0`, `self._chroma` is `None` (set at line 166). Both
`drop_column` and `rename_column` call `self._chroma.delete_collection(...)` without a None
check. This raises `AttributeError: 'NoneType' object has no attribute 'delete_collection'`.

Both are wrapped in `try/except Exception` that silently swallows the error:
- `drop_column` lines 953-958 — caught, no cleanup done, execution continues
- `rename_column` lines 980-1001 — caught, logged as "rename_chroma_failed", execution continues

**Impact:** Silent failure. ChromaDB collections are not cleaned up when columns are dropped
or renamed in ChromaDB-disabled mode. Since ChromaDB is disabled, there should be no collections
to clean — but the code path still executes and fails silently. No data loss, but the error is
misleading in logs and wastes exception handling.

**Fix:** Add `self._chroma is not None` guard before `delete_collection` calls.

---

## Optimization Opportunities

### O1: `_search_graph` re-embeds query per table per column

**Reference:** line 1788

```python
for table in searchable_tables:
    for col_name in self._get_longtext_columns(table):
        embedding = self._get_embedding(query)  # ← recomputed per iteration
```

**Cost:** `len(tables) × len(longtext_cols) - 1` redundant embedding calls. For 3 tables with
2 LONGTEXT columns each: 5 extra embedding computations per search_graph call.

**Fix:** Move `embedding = self._get_embedding(query)` before the outer loop.

### O2: `_create_fts5` queries `_has_autoincrement_id` per column

**Reference:** line 768 (`use_id = self._has_autoincrement_id(table)`)

```python
def _create_fts5(self, cur, table, col):
    use_id = self._has_autoincrement_id(table)  # ← queries sqlite_master every call
```

**Cost:** `_has_autoincrement_id` does `SELECT sql FROM sqlite_master WHERE name = ?`. For a
table with 20 TEXT columns created via `create_table`, this query runs 20 times — all returning
the same answer.

**Fix:** Cache the result per table in `create_table` and pass as a parameter:

```python
has_auto_id = self._has_autoincrement_id(table)
for col in text_cols:
    self._create_fts5(cur, table, col, has_auto_id)
```

### O3: `reconcile` fetches full rows one-by-one

**Reference:** line 2248
```python
for mid in missing:
    full_row = self.get(table, int(mid))  # ← 1 SQL query per missing row
```

**Cost:** For 1000 missing rows: 1000 `SELECT * WHERE id = ?` queries. Each query opens a new
SQLite connection via `self._connect()`.

**Fix:** Batch fetch all missing rows in one query:

```python
placeholders = ",".join("?" * len(missing))
rows = self.raw_query(
    f"SELECT * FROM {table} WHERE id IN ({placeholders})",
    tuple(int(m) for m in missing),
)
id_to_full_row = {str(r["id"]): dict(r) for r in rows}
```

### O4: DuckDB `SET threads = 4` per analytics query

**Reference:** line 470
```python
def analytics(self, sql):
    with self._db_lock:
        dk = self._duckdb_conn
        dk.execute("SET threads = 4")  # ← called every analytics() invocation
```

**Cost:** Negligible per-call (~0.1ms), but unnecessary. This is a session-level DuckDB setting.

**Fix:** Move to `_init_duckdb` — set once at connection creation:

```python
self._duckdb_conn.execute("SET threads = 4")
```

### O5: `_now_iso()` called redundantly in batch operations

**References:**
- `insert`: lines 1051, 1057 (`now = _now_iso()` twice)
- `insert_batch`: lines 1106, 1112 (`now = _now_iso()` per row, twice per row)
- Plus once per LONGTEXT column at lines 1051, 1106

**Cost:** `datetime.now(UTC).isoformat()` per row × per column. For 1000 batch inserts with
2 LONGTEXT columns: ~4000 `datetime.now()` calls. Each call is ~1µs, so ~4ms total — small
but avoidable.

**Fix:** Cache timestamp per batch transaction:
```python
now = _now_iso()
for data in rows:
    cur.execute(..., (..., now, ...))
```

### O6: `search_all` serial search across columns

**References:** lines 1901-1908

```python
for col in all_text_cols:
    col_fts = self._fts_search(table, col, query, limit * 2)  # serial
for col in lt_cols:
    col_vec = self._vector_search(table, col, query, where, limit * 2)  # serial
```

**Cost:** Sequential I/O across N text columns + M LONGTEXT columns. No cross-column ranking
until all results are collected and fused.

**Discussion point (not a clear bug):** Could be parallelized with `asyncio.gather` if the
search methods were async. Currently HybridDB is fully synchronous. Parallelization would
require making search async throughout, which is a larger design decision.

---

## Design Notes (not bugs, for discussion)

### N1: ChromaDB client pool leak

**Reference:** line 43 — `_chroma_client_pool: dict[str, Any] = {}`

Global dict of ChromaDB PersistentClient instances, keyed by vector path. Added at line 330
(`_chroma_client_pool[key] = client`). Only removed on heartbeat failure at line 310. If a
HybridDB instance is garbage-collected without `close()`, its client stays in the pool
referencing a path that may no longer be used. Not a leak in practice (instances are
long-lived), but worth noting.

### N2: Edge rule `target_match` requires identical column names

**Reference:** line 1297-1299
```python
pairs = self.raw_query(
    f"SELECT s.id as sid, t.id as tid FROM {src_table} s "
    f"JOIN {tgt_table} t ON s.{match}"
)
```

This joins `s.{match} = t.{match}` — so both source and target tables must have the same
column name for the join key. If they differ (e.g., `emails.sender_email` joining to
`contacts.email`), the rule silently produces zero results. Documentation should note this.

---

## Summary

| # | Type | Severity | Lines |
|---|---|---|---|
| B1 | Bug | Low | 955, 995 |
| O1 | Optimization | Medium | 1788 |
| O2 | Optimization | Low | 768 |
| O3 | Optimization | Medium | 2248 |
| O4 | Optimization | Low | 470 |
| O5 | Optimization | Low | 1051, 1057, 1106, 1112 |
| O6 | Discussion | Low | 1901-1908 |
| N1 | Note | Info | 43, 330 |
| N2 | Note | Info | 1297-1299 |

---

## 2026-05-07 Re-evaluation Verdicts

| # | Verdict | Action |
|---|---|---|
| B1 | Agreed | Added explicit `self._chroma is not None` guards before LONGTEXT collection delete/rename cleanup. |
| O1 | Agreed | `search_graph()` now computes the query embedding once and reuses it across searchable LONGTEXT columns. |
| O2 | Agreed | `_create_fts5()` accepts a cached autoincrement-id decision; table creation/rebuild paths avoid repeated `sqlite_master` lookups. |
| O3 | Agreed | `reconcile()` now batch-fetches full missing rows once instead of calling `get()` for each missing row. |
| O4 | Agreed | DuckDB `SET threads = 4` moved to `_init_duckdb()`; `analytics()` no longer sets it per query. |
| O5 | Agreed | `insert()` and `insert_batch()` reuse one timestamp for their journal writes; batch insert now uses one timestamp per transaction. |
| O6 | No change | Still a design discussion, not a targeted fix. Parallel search would require a broader async/sync architecture decision. |
| N1 | No change | Informational note. Existing pool behavior is acceptable for long-lived stores; no targeted fix made. |
| N2 | No change | Informational/documentation note. Edge rule join semantics are existing behavior; changing the rule shape would be a feature/design change. |

Regression coverage was added for B1 and O1-O5 in `tests/sdk/test_hybrid_db.py` and `tests/sdk/test_graph_analytics.py`.
