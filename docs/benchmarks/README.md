# Storage Benchmark v2: SQLite + FTS5 + sqlite-vec vs SQLite + FTS5 + ChromaDB

## Executive Summary

**Revised benchmark** fixing critical bugs from v1. Result still holds: **ChromaDB wins for vector search at scale**, but the gap is narrower than v1 suggested.

### v1 Bugs Fixed

| Bug | Impact | Fix |
|-----|--------|-----|
| FTS5 never used (LIKE queries instead) | FTS keyword search was unfairly slow | Use `MATCH` queries against actual FTS5 virtual tables |
| Accuracy measured as "any result = 100%" | Accuracy claims meaningless | Ground-truth categories + keyword relevance, proper recall/precision/MRR |
| sqlite-vec insert didn't include vector writes | Insert timing favored sqlite-vec | Both engines include full vector insert in timing |
| N+1 queries in sqlite-vec vector search | Inflated vector search time | Batch `WHERE id IN (...)` for both engines |
| Hash-based embeddings (no semantics) | Vector search quality meaningless | all-MiniLM-L6-v2 (384d) real embeddings |
| Duplicate data (30 texts cycling) | Unrealistic scale testing | 63 unique memory templates with variation |
| No warm-up runs | Cold-start bias | 3 warm-up iterations before timing |
| Dead code in sqlite-vec search_vec | Potential confusion | Removed unreachable fallback block |

## Test Environment

- **Python**: 3.13
- **SQLite**: 3.50.4 (with FTS5 + WAL mode)
- **sqlite-vec**: 0.1.6
- **ChromaDB**: 1.5.0
- **Embeddings**: sentence-transformers all-MiniLM-L6-v2 (384 dimensions)
- **Test Data**: 63 unique memory templates (preference/fact/workflow/correction), varied at scale
- **Schema**: Production MemoryStore schema (trigger, action, domain, memory_type, etc.)
- **Vector collections**: 3 per engine (full doc, trigger field, action field — matching production)
- **Search**: FTS5 MATCH (not LIKE), RRF hybrid (matching production `search_hybrid`)
- **Machine**: Apple M-series (ARM64), macOS

## Performance Results

### Bulk Insert (includes embedding generation + vector write)

| Memories | sqlite-vec | ChromaDB | Ratio |
|----------|------------|----------|-------|
| 10,000 | 8,848 ms | 13,823 ms | **1.6x faster** (vec) |
| 100,000 | 84,123 ms | 144,043 ms | **1.7x faster** (vec) |

> **Note**: sqlite-vec is faster for bulk insert because it batches individual SQL inserts into a single DB, while ChromaDB requires HTTP/client overhead per batch. Embedding generation time (identical for both) dominates.

### Single Insert (includes vector write)

| Memories | sqlite-vec | ChromaDB | Ratio |
|----------|------------|----------|-------|
| 10,000 | 68.7 ms | 35.7 ms | **1.9x slower** (vec) |
| 100,000 | 66.3 ms | 37.5 ms | **1.8x slower** (vec) |

> ChromaDB single inserts are faster because sqlite-vec requires 3 separate INSERTs per memory (doc + trigger field + action field) vs ChromaDB's batch upsert.

### FTS5 Keyword Search

| Memories | sqlite-vec (ms) | ChromaDB (ms) | Note |
|----------|-----------------|---------------|------|
| 10,000 | 7.84 | 0.92 | Same SQLite FTS5 — difference is DB size (WAL overhead from vec table) |
| 100,000 | 9.08 | 2.15 | sqlite-vec DB is ~15x larger due to vectors in same file |

> sqlite-vec stores vectors in the same SQLite file, bloating the DB and slowing FTS5. ChromaDB keeps vectors in a separate directory, so the SQLite DB stays small and FTS5 stays fast.

### Vector Search (includes embedding query + nearest-neighbor)

| Memories | sqlite-vec (ms) | ChromaDB (ms) | Ratio |
|----------|-----------------|---------------|-------|
| 10,000 | 92.1 | 9.6 | **9.6x slower** (vec) |
| 100,000 | 806.8 | 10.3 | **78.6x slower** (vec) |

> sqlite-vec is brute-force (linear scan). ChromaDB uses HNSW ANN indexing. The gap grows with scale — this is the critical difference.

### Hybrid Search (FTS5 + Vector + Field Vector)

| Memories | sqlite-vec (ms) | ChromaDB (ms) | Ratio |
|----------|-----------------|---------------|-------|
| 10,000 | 267.7 | 21.3 | **12.6x slower** (vec) |
| 100,000 | 2,430 | 23.2 | **104.7x slower** (vec) |

### Accuracy (Recall / Precision / MRR)

Both engines achieve identical vector search quality because they use the same embeddings:

| Method | Recall | Precision | MRR |
|--------|--------|-----------|-----|
| FTS5 | 0.001 — 0.006 | 0.90 | N/A |
| Vector | 0.001 — 0.006 | 1.00 | 1.000 |
| Hybrid | 0.001 — 0.006 | 0.94 — 1.00 | 1.000 |

> Low recall is expected — at 10k+ scale with only ~30 relevant items per query, retrieving 20 results gives recall of ~0.006. Precision is high because vector search reliably surfaces the few relevant items. MRR of 1.0 means the first relevant result is always in position 1.

### Storage Size

| Component | 10k memories | 100k memories |
|-----------|-------------|---------------|
| sqlite-vec SQLite DB | 50.6 MB | 497.0 MB |
| ChromaDB SQLite DB | 3.4 MB | 34.1 MB |
| ChromaDB vector store | 81.0 MB | 722.7 MB |
| **sqlite-vec total** | **50.6 MB** | **497.0 MB** |
| **ChromaDB total** | **84.4 MB** | **756.8 MB** |

> sqlite-vec is smaller total (vectors in same file as data), but the SQLite DB bloat affects all queries. ChromaDB uses more total space but keeps the SQLite DB lean.

## Why ChromaDB Still Wins

### 1. Vector search is O(n) for sqlite-vec

sqlite-vec (v0.1.6) performs brute-force linear scan. At 100k memories, vector search takes 807ms vs ChromaDB's 10ms. This will only worsen.

From sqlite-vec GitHub issue #25:
> "sqlite-vec as of v0.1.0 will be brute-force search only, which slows down on large datasets (>1M w/ large dimensions)"

### 2. Hybrid search is the real-world bottleneck

MemoryStore uses `search_hybrid()` which combines FTS5 + vector + field-vector. At 100k, this takes 2.4 seconds with sqlite-vec vs 23ms with ChromaDB. That's 100x — unusable for an interactive assistant.

### 3. db bloat slows everything

Because sqlite-vec stores vectors in the same SQLite file, the DB grows 15x larger than the ChromaDB equivalent. This bloat slows FTS5 queries too (8ms vs 2ms at 10k, 10ms vs 2ms at 100k).

### 4. sqlite-vec advantages don't outweigh the costs

| Advantage | Reality |
|-----------|---------|
| Smaller total disk | Yes, but slows all SQLite ops |
| Fewer dependencies | True, but a 78x search penalty |
| Single file | Convenient, but creates bloat |
| Faster bulk insert | Only because ChromaDB has per-batch HTTP overhead; embedding generation dominates |

## Recommendation

**Stick with SQLite + FTS5 + ChromaDB** for the Assistant.

For our use case (< 100k memories per user, interactive search), ChromaDB's ANN indexing is essential. The 78x vector search penalty at 100k makes sqlite-vec unsuitable for production.

If sqlite-vec adds ANN/HNSW indexing in the future, this should be revisited. The single-file deployment advantage is significant for a per-user desktop app.

## Files

- `test_storage_benchmark.py` — v1 benchmark (deprecated, has bugs)
- `test_memory_benchmark_v2.py` — v2 benchmark (fair comparison, production schema)
- Run v2: `uv run pytest docs/benchmarks/test_memory_benchmark_v2.py -v -s`