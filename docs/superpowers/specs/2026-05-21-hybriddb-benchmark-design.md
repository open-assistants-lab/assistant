# HybridDB Performance Benchmark Suite

Date: 2026-05-21
Status: Approved Design

## 1. Goals

- Provide reproducible, statistically meaningful performance benchmarks for HybridDB
- Serve both OSS contributors (quick smoke check) and internal validation (comprehensive)
- Work identically across in-repo (`assistant`) and standalone (`HybridDB`) repos
- Track performance over time to catch regressions

## 2. Non-Goals

- Micro-benchmarking individual SQLite or ChromaDB operations
- Measuring network or distributed performance (HybridDB is embedded/single-node)
- Production load testing with gigabytes of data (disk space constrained)
- Tracking performance per git commit automatically (manual snapshot comparison)

## 3. Framework: pytest-benchmark

Add `pytest-benchmark>=4.0.0` as a dev dependency in both repos.

Why:
- Integrates with existing pytest infrastructure (fixtures, markers, config)
- Handles calibration, warmup, outlier detection, statistical comparison
- Outputs machine-readable JSON for cross-run comparison
- Widely used, well-maintained

## 4. Project Layout

### In-repo (`assistant`)
```
tests/hybriddb/benchmarks/
├── conftest.py              # pytest-benchmark fixtures, scale fixture, markers
├── test_search.py           # TEXT + LONGTEXT keyword, vector, hybrid
├── test_graph.py            # node/edge CRUD, traversal, algorithms
├── test_analytics.py        # DuckDB query latency, HybridDB overhead
├── test_concurrent.py       # multi-threaded read/write contention
├── test_storage.py          # disk usage, ChromaDB segment growth
scripts/
├── run_benchmarks.sh        # smoke & full runners
├── compare_results.py       # diff two JSON snapshots
```

### Standalone OSS (`HybridDB`)
```
tests/benchmarks/
├── conftest.py              # same structure, different import paths
├── test_search.py           # IDENTICAL to in-repo
├── test_graph.py            # IDENTICAL
├── test_analytics.py        # IDENTICAL
├── test_concurrent.py       # IDENTICAL
├── test_storage.py          # IDENTICAL
scripts/
├── run_benchmarks.sh        # identical
├── compare_results.py       # identical
```

The benchmark test files themselves are identical between repos. Only `conftest.py` differs due to the import path (`hybriddb.db` vs `src.sdk.hybrid_db`).

## 5. Smoke vs Full Scale

Controlled by `--benchmark-full` CLI flag.

| Dimension | Smoke | Full |
|-----------|-------|------|
| Documents | 1,000 | 100,000 |
| Graph nodes | 100 | 10,000 |
| Graph edges | 500 | 50,000 |
| Analytics rows | 10,000 | 1,000,000 |
| Analytics data size | ~10 MB | ~1 GB |
| Concurrent duration | 2s | 30s |
| Expected runtime | <30s | ~5-15 min |

## 6. Embedding Strategy

**Model**: `all-MiniLM-L6-v2` (384 dim, ~80MB download). Added as optional dep in `[benchmark]` extras group.

### Two modes controlled by `--precompute-embeddings` (default: true):

**Mode A: Pre-computed (default)** — embeddings generated once per session, cached to `data/benchmark_embeddings.npy`. Insert uses pre-computed vectors. Search latency excludes embedding time. Fast, stable, reproducible.

**Mode B: End-to-end** — `--precompute-embeddings=false`. Embedding generation happens during insert via HybridDB's built-in embedding fn. Tests measure full stack latency.

### Fixture
```python
@pytest.fixture(scope="session")
def embeddings(request):
    """Cached embedding array — generates once, reused across all tests."""
    cache = Path("data/benchmark_embeddings.npy")
    if cache.exists() and request.config.getoption("--precompute-embeddings"):
        return np.load(cache)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = generate_all_texts()
    embs = model.encode(texts, show_progress_bar=True)
    if request.config.getoption("--precompute-embeddings"):
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, embs)
    return embs
```

## 7. Test Dimensions

### 7.1 Search (test_search.py)

| Test | Columns | Modes | Metric |
|------|---------|-------|--------|
| keyword_search | TEXT | keyword | latency, recall@10 |
| keyword_search | LONGTEXT | keyword | latency, recall@10 |
| vector_search | TEXT | vector | latency, recall@10 |
| vector_search | LONGTEXT | vector | latency, recall@10 |
| hybrid_search | TEXT | hybrid | latency, recall@10 |
| hybrid_search | LONGTEXT | hybrid | latency, recall@10 |

Accuracy measured by generating a known ground truth (documents with specific keywords, specific embedding anchors), then checking whether the expected document appears in top-K results.

### 7.2 Graph (test_graph.py)

| Test | Metric |
|------|--------|
| add_nodes_batch | latency, throughput (nodes/sec) |
| add_edges_batch | latency, throughput (edges/sec) |
| get_neighbors | latency |
| bfs_traverse | latency vs depth |
| shortest_path | latency |
| pagerank | latency |
| community_detect | latency |
| decay_edges | latency |

Graph generated with known properties: random graph with configurable n_nodes and avg_degree.

### 7.3 DuckDB Analytics (test_analytics.py)

| Test | Description | Metric |
|------|-------------|--------|
| simple_aggregation | SELECT COUNT, AVG, SUM | latency |
| group_by | GROUP BY multi-category | latency |
| join | join analytics table with metadata | latency |
| overhead | HybridDB.analytics() vs native DuckDB connection | overhead ratio |

Data generated with multiple category columns, numeric measures, and timestamps.

### 7.4 Concurrent Access (test_concurrent.py)

| Test | Description | Metric |
|------|-------------|--------|
| read_contention | N reader threads, single writer | latency, throughput |
| read_write_mixed | concurrent readers + writers (WAL mode) | latency, throughput, correctness |
| write_contention | N writer threads, no readers | latency, throughput |

Correctness check: after concurrent writes, verify total row count matches expected.

### 7.5 Storage (test_storage.py)

| Test | Metric |
|------|--------|
| db_file_growth | SQLite file size per 10k rows |
| chroma_segment_growth | ChromaDB directory size per 10k rows |
| total_storage | total disk usage at smoke/full scale |
| chroma_bloat_check | segment count and average size (regression catch) |

## 8. Data Generation

All test data generated deterministically (seeded random) for reproducibility. Each test generates its own dataset within its fixture — no shared mutable state between tests.

```python
def generate_docs(n, columns, seed=42):
    """Generate n documents with specified columns."""
    rng = random.Random(seed)
    titles = [f"Document {i}: {_random_title(rng)}" for i in range(n)]
    bodies = [_random_body(rng, length="medium" if "LONGTEXT" not in columns else "long")
              for _ in range(n)]
    ...
```

## 9. Reporting

### Run commands
```bash
# Smoke
uv run pytest tests/hybriddb/benchmarks/ --benchmark-only --benchmark-json=results/smoke.json

# Full
uv run pytest tests/hybriddb/benchmarks/ --benchmark-full \
  --benchmark-only --benchmark-json=results/full.json

# End-to-end embedding mode
uv run pytest tests/hybriddb/benchmarks/ --benchmark-full \
  --benchmark-only --precompute-embeddings=false \
  --benchmark-json=results/full-e2e.json
```

### JSON snapshot archiving
Each run auto-saves to `results/YYYY-MM-DD-<git-hash>.json`. The latest run also overwrites `results/latest.json`.

### compare_results.py
```bash
# Show change from last baseline
uv run python scripts/compare_results.py --diff

# Compare two specific runs
uv run python scripts/compare_results.py results/v1-hash.json results/v2-hash.json

# Generate markdown report
uv run python scripts/compare_results.py --markdown > results/changes.md
```

Output format:
```
| Test                    | Scale | Before (ms) | After (ms) | Δ     |
|-------------------------|-------|-------------|-------------|-------|
| keyword_search (TEXT)   | full  | 12.3 ± 0.5  | 14.1 ± 0.6  | +14%  |
| vector_search (TEXT)    | full  | 45.2 ± 2.1  | 43.8 ± 1.9  | -3%   |
```

## 10. Dependencies

Added to `[project.optional-dependencies]` in both repos:

```toml
# In-repo pyproject.toml
[project.optional-dependencies]
benchmark = [
    "pytest-benchmark>=4.0.0",
    "pytest-timeout>=2.3.0",
    "sentence-transformers>=3.0.0",
    "numpy>=1.24.0",
]

# Standalone pyproject.toml
[project.optional-dependencies]
benchmark = [
    "pytest-benchmark>=4.0.0",
    "pytest-timeout>=2.3.0",
    "sentence-transformers>=3.0.0",
    "numpy>=1.24.0",
]
```

## 11. Error Handling & Edge Cases

- **No embedding model**: test_search.py should skip gracefully with a clear message if sentence-transformers is not installed
- **DuckDB unavailable**: test_analytics.py skipped if duckdb not installed
- **NetworkX unavailable**: test_graph.py graph algorithms skipped if networkx or scipy not installed
- **Empty database**: all tests generate their own data, no dependence on pre-existing state
- **Disk full**: storage tests catch `OSError` and report available space rather than crashing
- **Timeouts**: concurrent tests use `pytest-timeout` to prevent hanging on deadlock
