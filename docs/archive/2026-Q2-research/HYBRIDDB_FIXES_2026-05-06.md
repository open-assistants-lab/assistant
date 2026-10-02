# HybridDB Bug Fixes

## Summary

Re-scanned `src/sdk/hybrid_db.py`, confirmed three actionable defects, and fixed them with regression coverage.

## Fixed Issues

### 1. FTS Shadow Tables Leaked From `list_tables()`

`list_tables()` previously queried `sqlite_master` and filtered only some internal names. SQLite FTS5 creates tables such as `contacts_fts_name_data`, which were returned as app tables.

Fix: `list_tables()` now reads table names from HybridDB's `_schema` metadata table instead of SQLite internals.

### 2. DuckDB Analytics Became Stale After Schema Changes

Registered DuckDB mirror tables were created from the SQLite schema at registration time. Later calls to `add_column()`, `drop_column()`, or `rename_column()` changed SQLite but left the DuckDB table schema stale.

Fix: added `_refresh_duckdb_table_if_registered()` and call it after schema mutations so registered DuckDB tables are rebuilt from current metadata and resynced.

### 3. Chroma Index Health Compared GB To Bytes

`_check_index_health()` computed `size_gb` but compared it to thresholds expressed in bytes, so size warnings/errors would not trigger at the configured limit.

Fix: compare `size_bytes` to byte thresholds while still logging `size_gb` for readability.

## Regression Tests Added

- `test_list_tables_excludes_fts_shadow_tables`
- `test_index_health_logs_when_link_file_exceeds_max_size`
- `test_add_column_refreshes_registered_duckdb_table`
- `test_rename_column_refreshes_registered_duckdb_table`
- `test_drop_column_refreshes_registered_duckdb_table`

## Verification

```bash
uv run pytest tests/sdk/test_hybrid_db.py tests/sdk/test_graph_analytics.py -q
```

Result: `131 passed in 9.97s`

```bash
uv run ruff check src/sdk/hybrid_db.py tests/sdk/test_hybrid_db.py tests/sdk/test_graph_analytics.py
```

Result: `All checks passed!`
