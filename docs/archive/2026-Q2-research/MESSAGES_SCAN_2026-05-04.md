# Message Store — Scan Findings (Peer Review)

**Date:** 2026-05-04
**File:** `src/storage/messages.py` (311 lines)

---

## Bugs

### B1: `add_message_with_embedding` — fragile manual journal cleanup

**Reference:** lines 119-125

```python
with self.db._connect() as cur:
    cur.execute(
        "DELETE FROM _journal WHERE app_table = ? AND row_id = ? "
        "AND column_name = ? AND op = 'add'",
        ("messages", row_id, "content"),
    )
self.db.process_journal()
```

After inserting with `sync=False`, manually inserting into ChromaDB, then manually deleting
the journal entry for the content column. This assumes:
1. The journal entry added by `insert(sync=False)` has `op='add'` and `column_name='content'`
2. Those journal contract details won't change

If the journal format changes (e.g., column name becomes `content_LONGTEXT` or op becomes
`row_add`), this silently fails — the journal entry still exists and `process_journal` tries
to add a duplicate ChromaDB document.

**Fix:** Either make `insert` accept an `skip_journal_columns` parameter, or use a proper
bypass API rather than manual journal surgery.

---

### B2: `add_message_with_embedding` accesses private `_row_to_metadata`

**Reference:** line 110

```python
vector_metadata = self.db._row_to_metadata("messages", row)
```

Accessing a private HybridDB method from outside the class. Works today but breaks if the
method signature changes or is removed.

**Fix:** Make `_row_to_metadata` part of the public API or provide a public wrapper.

---

### B3: `get_messages_with_summary` — `limit <= 1` edge case returns summary-only unintuitively

**Reference:** lines 249-250

```python
if limit <= 1:
    rows = summary_rows
```

When `limit=1`, returns only the summary message. The function name suggests "messages with
summary" but the summary alone is returned. The `if limit <= 1` check prevents the
`where="id > ?"` query entirely (which would return 0 rows with limit=0). But returning
`[summary]` for `limit=1` may surprise callers who expect the summary + 0 more messages.

**Fix:** Clarify the edge case or return `[summary]` only when `limit > 0`, empty otherwise.

---

## Optimization Opportunities

### O1: Duplicate date-range logic in `get_messages` and `count_messages`

**Reference:** lines 202-213 vs lines 280-290

Identical `where_parts` construction duplicated in both methods:

```python
where_parts = []
params: list[str] = []
if start_date:
    where_parts.append("ts >= ?")
    params.append(datetime.combine(start_date, datetime.min.time()).isoformat())
if end_date:
    where_parts.append("ts <= ?")
    params.append(datetime.combine(end_date, datetime.max.time()).isoformat())
where = " AND ".join(where_parts) if where_parts else ""
```

**Fix:** Extract to private helper `_build_date_filter`.

### O2: Duplicate `SearchResult` construction in 3 search methods

**Reference:** lines 134-143, 151-159, 184-193

All three methods (`search_keyword`, `search_vector`, `search_hybrid`) have identical list
comprehensions building `SearchResult` objects from row dicts.

**Fix:** Extract to `_rows_to_search_results`.

### O3: `get_recent_messages` reads DESC then reverses in Python

**Reference:** lines 228-239

```python
rows = self.db.query("messages", order_by="ts DESC", limit=count)
messages = [... for r in rows]
return list(reversed(messages))
```

For `count=100`, reads 100 rows in DESC, converts to Message objects, then reverses 100 items.
The reverse is O(n) extra work — could query `ORDER BY ts ASC` with a subquery or use
a materialization approach.

**Fix:** Replace with single ASC query using a two-step approach:
```python
# Get the threshold ID for the last N messages
oldest_id = self.db.query("messages", order_by="ts DESC", limit=count)[-1]["id"]
return self.get_messages(after_id=oldest_id, limit=count)
```

Minor — 100 items reversed in Python is negligible.

### O4: `has_summary` queries full row

**Reference:** line 276

```python
rows = self.db.query("messages", where="role = 'summary'", limit=1)
return len(rows) > 0
```

`db.query` does `SELECT *` and fetches the full row (including `content` LONGTEXT). For
an existence check, this wastes bandwidth.

**Fix:** Use `db.count()` or a raw `SELECT 1 LIMIT 1` query:
```python
return self.db.count("messages", where="role = 'summary'") > 0
```

### O5: `clear()` does full `register_duckdb_table` after clearing

**Reference:** line 301

```python
self.db.register_duckdb_table("messages")
```

After deleting all rows, calls `register_duckdb_table` which does `DROP TABLE IF EXISTS` +
`CREATE TABLE` + `_full_sync_duckdb_table`. The schema hasn't changed — only the data.
Just the full sync is needed.

**Fix:** Call `self.db._full_sync_duckdb_table("messages")` instead.

---

## Design Notes

### N1: MessageStore cache has no eviction

**Reference:** lines 304-311

```python
_stores: dict[str, MessageStore] = {}
def get_message_store(user_id, workspace_id="personal"):
    key = f"{user_id}:{workspace_id}"
    if key not in _stores:
        _stores[key] = MessageStore(user_id, workspace_id=workspace_id)
    return _stores[key]
```

Same unbounded-cache pattern as `MemoryStore` (shared concern between both stores).

### N2: `search_hybrid` hardcodes `recency_weight=0.3`

**Reference:** line 179

Unlike `fts_weight` which is a parameter, `recency_weight` is hardcoded. Callers cannot
disable recency weighting without modifying code.

### N3: `get_messages` default limit is 10000

**Reference:** line 213

`limit=limit or 10000` — a misconfigured call that passes `limit=None` gets 10000 messages.
For a conversation with 500K messages, this could be expensive. No pagination support
(no offset, no cursor-based). Intentionally simple; likely fine for single-user desktop.

### N4: Date-range filter uses naive datetime for end_bound

**Reference:** line 209

```python
params.append(datetime.combine(end_date, datetime.max.time()).isoformat())
```

Produces a timezone-naive ISO string (e.g., `"2026-05-04T23:59:59.999999"`), while stored
`ts` values include timezone offset (`"2026-05-04T10:00:00+00:00"`). SQLite string
comparison IS lexicographically correct for this case (ISO format is sortable), but
the mismatch is fragile. Explicitly adding UTC timezone is safer.

---

## Summary

| # | Type | Severity | Lines |
|---|---|---|---|
| B1 | Bug | Medium | 119-125 |
| B2 | Bug | Low | 110 |
| B3 | Bug | Low | 249-250 |
| O1 | Optimization | Low | 202-213, 280-290 |
| O2 | Optimization | Low | 134-143, 151-159, 184-193 |
| O3 | Optimization | Low | 228-239 |
| O4 | Optimization | Low | 276 |
| O5 | Optimization | Low | 301 |
| N1 | Note | Info | 304-311 |
| N2 | Note | Info | 179 |
| N3 | Note | Info | 213 |
| N4 | Note | Info | 209 |

---

## 2026-05-07 Re-evaluation Verdicts

| # | Verdict | Action |
|---|---|---|
| B1 | Agreed | Added `HybridDB.insert(..., skip_journal_columns=...)` and updated `add_message_with_embedding()` to avoid fragile post-insert journal deletion. |
| B2 | Agreed | Added public `HybridDB.row_to_metadata()` and `HybridDB.vector_upsert()` hooks; `MessageStore` no longer calls `_row_to_metadata()` or `_get_collection()` directly. |
| B3 | Agreed | `get_messages_with_summary(limit=0)` now returns an empty list; `limit=1` intentionally returns the summary only. |
| O1 | Agreed | Extracted shared UTC date filter construction into `_date_filter_bounds()`. |
| O2 | Agreed | Extracted shared row conversion into `_rows_to_search_results()` and `_rows_to_messages()`. |
| O3 | No change | Reversing up to the requested recent-message count is negligible and keeps the query simple. |
| O4 | Agreed | `has_summary()` now uses `db.count()` instead of fetching a full row. |
| O5 | Agreed | Added public `HybridDB.sync_duckdb_table()` and updated `clear()` to resync DuckDB data without re-registering schema. |
| N1 | No change | Store cache eviction is a broader lifecycle policy shared with other stores. |
| N2 | Already addressed | `search_hybrid()` already exposes `recency_weight`, including `0.0` to disable recency. |
| N3 | No change | Default `10000` limit is retained for now to preserve existing behavior. |
| N4 | Agreed | Date-range bounds now use explicit UTC-aware datetimes. |

Regression coverage was added in `tests/storage/test_messages_store.py` and `tests/sdk/test_hybrid_db.py`.
