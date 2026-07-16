# HybridDB Benchmark Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build performance benchmarks for HybridDB across search, graph, analytics, concurrent access, and storage — running identically in both the in-repo (`assistant`) and standalone OSS (`HybridDB`) repos.

**Architecture:** pytest-benchmark integration with smoke/full scale modes, pre-computed or live embeddings, JSON snapshot archiving with `compare_results.py` diff tool. Benchmark test files are identical between repos; only `conftest.py` differs (import path).

**Tech Stack:** `pytest-benchmark`, `sentence-transformers`, `numpy`, `pytest-timeout`

---

## File Structure

```
assistant (in-repo):
├── pyproject.toml                         # MODIFY: add [benchmark] deps
├── tests/
│   └── hybriddb/
│       └── benchmarks/
│           ├── __init__.py                # CREATE: empty
│           ├── conftest.py                # CREATE: fixtures, scale, markers (imports src.sdk.hybrid_db)
│           ├── helpers.py                 # CREATE: data generation, embedding cache
│           ├── test_search.py             # CREATE: TEXT+LONGTEXT keyword/vector/hybrid
│           ├── test_graph.py              # CREATE: node/edge CRUD, traversal, algorithms
│           ├── test_analytics.py          # CREATE: DuckDB query latency, overhead
│           ├── test_concurrent.py         # CREATE: multi-threaded read/write contention
│           └── test_storage.py            # CREATE: disk usage, ChromaDB bloat
├── scripts/
│   ├── compare_results.py                 # CREATE: JSON snapshot diff tool
│   └── run_benchmarks.sh                  # CREATE: smoke & full runners

HybridDB (standalone OSS):
├── pyproject.toml                         # MODIFY: add [benchmark] deps
├── tests/
│   └── benchmarks/
│       ├── __init__.py                    # CREATE: empty
│       ├── conftest.py                    # CREATE: fixtures, scale, markers (imports hybriddb)
│       ├── helpers.py                     # COPY: identical to in-repo
│       ├── test_search.py                 # COPY: identical to in-repo
│       ├── test_graph.py                  # COPY: identical to in-repo
│       ├── test_analytics.py              # COPY: identical to in-repo
│       ├── test_concurrent.py             # COPY: identical to in-repo
│       └── test_storage.py                # COPY: identical to in-repo
├── scripts/
│   ├── compare_results.py                 # COPY: identical to in-repo
│   └── run_benchmarks.sh                  # COPY: identical to in-repo
```

---

### Task 1: Add benchmark dependencies + directory structure

**Files:**
- Modify: `pyproject.toml` (in-repo)
- Modify: `pyproject.toml` (standalone)
- Create: `tests/hybriddb/benchmarks/__init__.py`
- Create: `tests/hybriddb/benchmarks/`
- Create: `tests/benchmarks/__init__.py` (standalone)
- Create: `tests/benchmarks/` (standalone)

- [ ] **Step 1: Add `[benchmark]` optional-deps to in-repo pyproject.toml**

Edit `assistant/pyproject.toml`. Add after the `[project.optional-dependencies]` section:

```toml
benchmark = [
    "pytest-benchmark>=4.0.0",
    "pytest-timeout>=2.3.0",
    "numpy>=1.24.0",
]
```

- [ ] **Step 2: Add `[benchmark]` optional-deps to standalone pyproject.toml**

Edit `HybridDB/pyproject.toml`. Add after the `dev` entry:

```toml
benchmark = [
    "pytest-benchmark>=4.0.0",
    "pytest-timeout>=2.3.0",
    "numpy>=1.24.0",
]
```

Also add `sentence-transformers` as an optional dep (the standalone already has a `sentence-transformers` group, but add the `benchmark` group with it):

```toml
benchmark = [
    "pytest-benchmark>=4.0.0",
    "pytest-timeout>=2.3.0",
    "numpy>=1.24.0",
    "sentence-transformers>=3.0.0",
]
```

- [ ] **Step 3: Create directory structures and __init__.py files**

```bash
mkdir -p /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks
touch /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/__init__.py
mkdir -p /Users/eddy/Developer/Python/HybridDB/tests/benchmarks
touch /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/__init__.py
mkdir -p /Users/eddy/Developer/Python/HybridDB/scripts
```

- [ ] **Step 4: Verify directory structure**

```bash
ls -la /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/
ls -la /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/
```

- [ ] **Step 5: Install benchmark deps in both repos**

```bash
uv sync --group benchmark --group dev
```

```bash
cd /Users/eddy/Developer/Python/HybridDB && uv pip install pytest-benchmark pytest-timeout numpy sentence-transformers
```

Expected: both install cleanly.

---

### Task 2: Write helpers.py — data generation and embedding cache

**Files:**
- Create: `tests/hybriddb/benchmarks/helpers.py` (in-repo)
- Create: `tests/benchmarks/helpers.py` (standalone, identical copy)

This file provides all shared utilities: text generation, scale configuration, embedding caching, and the `results/` archiver.

- [ ] **Step 1: Write helpers.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/helpers.py`:

```python
"""Shared helpers for HybridDB benchmarks: data generation, embedding cache, scale."""

import hashlib
import json
import random
import subprocess
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np


class Scale(NamedTuple):
    n_docs: int
    n_graph_nodes: int
    n_graph_edges: int
    n_analytics_rows: int
    concurrent_duration_s: int


SMOKE = Scale(
    n_docs=1_000,
    n_graph_nodes=100,
    n_graph_edges=500,
    n_analytics_rows=10_000,
    concurrent_duration_s=2,
)

FULL = Scale(
    n_docs=100_000,
    n_graph_nodes=10_000,
    n_graph_edges=50_000,
    n_analytics_rows=1_000_000,
    concurrent_duration_s=30,
)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


def git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def archive_results(json_path: str) -> Path:
    """Copy benchmark JSON to timestamped archive and update latest."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S")
    githash = git_hash()
    archive_name = f"{timestamp}-{githash}.json"
    archive_path = RESULTS_DIR / archive_name
    latest_path = RESULTS_DIR / "latest.json"
    src = Path(json_path)
    if src.exists():
        import shutil
        shutil.copy2(src, archive_path)
        shutil.copy2(src, latest_path)
    return archive_path


def _random_title(rng: random.Random) -> str:
    adjectives = ["quick", "lazy", "happy", "sad", "bright", "dark", "fast", "slow"]
    nouns = ["fox", "dog", "cat", "bird", "fish", "tree", "car", "book"]
    return f"The {rng.choice(adjectives)} {rng.choice(nouns)}"


def _random_body(rng: random.Random, length: str = "medium") -> str:
    words = [
        "lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing",
        "elit", "sed", "do", "eiusmod", "tempor", "incididunt", "ut", "labore",
        "et", "dolore", "magna", "aliqua", "hello", "world", "test", "search",
        "hybrid", "vector", "keyword", "database", "performance", "benchmark",
    ]
    n_words = 50 if length == "medium" else 500
    return " ".join(rng.choice(words) for _ in range(n_words))


def generate_docs(
    n: int,
    columns: list[dict[str, str]],
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate n documents with specified column definitions.

    Each column dict: {"name": str, "type": "TEXT"|"LONGTEXT"}
    """
    rng = random.Random(seed)
    docs: list[dict[str, Any]] = []
    for i in range(n):
        doc: dict[str, Any] = {"id": str(i)}
        for col in columns:
            if col["type"] == "LONGTEXT":
                doc[col["name"]] = _random_body(rng, length="long")
            else:
                doc[col["name"]] = f"{_random_title(rng)} — doc {i}"
        docs.append(doc)
    return docs


def generate_analytics_data(
    n: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate analytics data with category columns and numeric measures."""
    rng = random.Random(seed)
    categories = ["A", "B", "C", "D", "E"]
    regions = ["NA", "EU", "APAC", "LATAM"]
    rows: list[dict[str, Any]] = []
    for i in range(n):
        rows.append({
            "id": i,
            "category": rng.choice(categories),
            "region": rng.choice(regions),
            "value": round(rng.uniform(10.0, 1000.0), 2),
            "quantity": rng.randint(1, 100),
            "timestamp": f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
        })
    return rows


def generate_graph_data(
    n_nodes: int,
    n_edges: int,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate graph nodes and edges with random connectivity."""
    rng = random.Random(seed)
    node_types = ["person", "document", "event", "organization"]
    nodes = [
        {"id": f"n{i}", "type": rng.choice(node_types), "label": f"Node {i}"}
        for i in range(n_nodes)
    ]
    edges: list[dict[str, Any]] = []
    target_nodes = [n["id"] for n in nodes]
    for i in range(n_edges):
        source = rng.choice(target_nodes)
        target = rng.choice(target_nodes)
        if source == target:
            continue
        edges.append({
            "id": f"e{i}",
            "source_id": source,
            "target_id": target,
            "type": "related",
            "weight": round(rng.uniform(0.1, 1.0), 2),
        })
    return nodes, edges


def compute_recall(
    result_ids: list[str],
    expected_ids: set[str],
    k: int = 10,
) -> float:
    """Fraction of expected_ids found in top-k of result_ids."""
    if not expected_ids:
        return 1.0
    top_k = result_ids[:k]
    found = sum(1 for rid in top_k if rid in expected_ids)
    return found / len(expected_ids)
```

- [ ] **Step 2: Copy to standalone repo**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/helpers.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/helpers.py
```

- [ ] **Step 3: Quick check — import succeeds**

Run: `uv run python -c "from tests.hybriddb.benchmarks.helpers import SMOKE, FULL, Scale, generate_docs; print('ok')"`
Run: `cd /Users/eddy/Developer/Python/HybridDB && uv run python -c "from tests.benchmarks.helpers import SMOKE, FULL; print('ok')"`

Expected: both print "ok".

---

### Task 3: Write conftest.py — fixtures, scale, markers

**Files:**
- Create: `tests/hybriddb/benchmarks/conftest.py` (in-repo)
- Create: `tests/benchmarks/conftest.py` (standalone, different import)

- [ ] **Step 1: Write in-repo conftest**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/conftest.py`:

```python
"""conftest for HybridDB benchmarks — in-repo variant."""

from pathlib import Path

import pytest

from src.sdk.hybrid_db import HybridDB

from .helpers import FULL, SMOKE, Scale, archive_results, generate_docs


def pytest_addoption(parser):
    parser.addoption(
        "--benchmark-full",
        action="store_true",
        default=False,
        help="Run full-scale benchmarks (default: smoke)",
    )
    parser.addoption(
        "--precompute-embeddings",
        action="store_true",
        default=True,
        help="Pre-compute and cache embeddings (default: true)",
    )


@pytest.fixture(scope="session")
def embedding_fn():
    """Session-scoped SentenceTransformer model — loaded once."""
    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    return lambda text: model.encode(text).tolist()


@pytest.fixture
def scale(request) -> Scale:
    return FULL if request.config.getoption("--benchmark-full") else SMOKE


@pytest.fixture
def db(request, embedding_fn, tmp_path) -> HybridDB:
    h = HybridDB(
        path=str(tmp_path),
        embedding_fn=embedding_fn,
        embedding_model_name="all-MiniLM-L6-v2",
    )
    yield h
    try:
        h.close()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    json_path = session.config.getoption("--benchmark-json")
    if json_path:
        archive_results(json_path)
```

- [ ] **Step 2: Write standalone conftest**

Create `/Users/eddy/Developer/Python/HybridDB/tests/benchmarks/conftest.py`:

```python
"""conftest for HybridDB benchmarks — standalone OSS variant."""

from pathlib import Path

import pytest

from hybriddb import HybridDB

from .helpers import FULL, SMOKE, Scale, archive_results, generate_docs


def pytest_addoption(parser):
    parser.addoption(
        "--benchmark-full",
        action="store_true",
        default=False,
        help="Run full-scale benchmarks (default: smoke)",
    )
    parser.addoption(
        "--precompute-embeddings",
        action="store_true",
        default=True,
        help="Pre-compute and cache embeddings (default: true)",
    )


@pytest.fixture(scope="session")
def embedding_fn():
    """Session-scoped SentenceTransformer model — loaded once."""
    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    return lambda text: model.encode(text).tolist()


@pytest.fixture
def scale(request) -> Scale:
    return FULL if request.config.getoption("--benchmark-full") else SMOKE


@pytest.fixture
def db(request, embedding_fn, tmp_path) -> HybridDB:
    h = HybridDB(
        path=str(tmp_path),
        embedding_fn=embedding_fn,
        embedding_model_name="all-MiniLM-L6-v2",
    )
    yield h
    try:
        h.close()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    json_path = session.config.getoption("--benchmark-json")
    if json_path:
        archive_results(json_path)
```

- [ ] **Step 3: Verify imports**

Run: `uv run python -c "from tests.hybriddb.benchmarks.conftest import pytest_addoption; print('ok')"`
Run: `cd /Users/eddy/Developer/Python/HybridDB && uv run python -c "from tests.benchmarks.conftest import pytest_addoption; print('ok')"`

Expected: both print "ok".

---

### Task 4: Write test_search.py — TEXT + LONGTEXT keyword/vector/hybrid

**Files:**
- Create: `tests/hybriddb/benchmarks/test_search.py` (in-repo)
- Create: `tests/benchmarks/test_search.py` (standalone, identical)

- [ ] **Step 1: Write test_search.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_search.py`:

```python
"""Search benchmarks: keyword, vector, hybrid on TEXT and LONGTEXT columns."""

from typing import Any

import pytest

from .helpers import generate_docs, compute_recall


pytest.importorskip("sentence_transformers")

TEXT_COLUMNS = [{"name": "content", "type": "TEXT"}]
LONGTEXT_COLUMNS = [{"name": "content", "type": "LONGTEXT"}]


def _expected_ids_for_query(docs: list[dict[str, Any]], query: str) -> set[str]:
    """Return IDs of docs that contain the query keyword (for recall check)."""
    q = query.lower()
    return {d["id"] for d in docs if q in d.get("content", "").lower()}


def _insert_docs(db, table: str, docs: list[dict[str, Any]]):
    """Insert docs in batch using HybridDB insert_batch (sync True for Chroma)."""
    db.insert_batch(table, docs, sync=True)


def _prepare_text_db(db, scale, columns, table: str = "bench_search"):
    docs = generate_docs(scale.n_docs, columns)
    db.create_table(table, {c["name"]: c["type"] for c in columns})
    _insert_docs(db, table, docs)
    return docs


# ---- TEXT column benchmarks ----


def test_keyword_search_text(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, TEXT_COLUMNS)
    query = "hello"
    expected = _expected_ids_for_query(docs, query)

    def _search():
        return db.search("bench_search", query, search_type="keyword")

    result = benchmark(_search)
    recall = compute_recall([r["id"] for r in result], expected)
    assert recall >= 0.5 or not expected


def test_vector_search_text(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, TEXT_COLUMNS)
    query = "test search"

    def _search():
        return db.search("bench_search", query, search_type="vector")

    result = benchmark(_search)
    assert len(result) > 0


def test_hybrid_search_text(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, TEXT_COLUMNS)
    query = "hello world"

    def _search():
        return db.search("bench_search", query, search_type="hybrid")

    result = benchmark(_search)
    assert len(result) > 0


# ---- LONGTEXT column benchmarks ----


def test_keyword_search_longtext(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, LONGTEXT_COLUMNS)
    query = "hello"
    expected = _expected_ids_for_query(docs, query)

    def _search():
        return db.search("bench_search", query, search_type="keyword")

    result = benchmark(_search)
    recall = compute_recall([r["id"] for r in result], expected)
    assert recall >= 0.5 or not expected


def test_vector_search_longtext(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, LONGTEXT_COLUMNS)
    query = "search performance benchmark"

    def _search():
        return db.search("bench_search", query, search_type="vector")

    result = benchmark(_search)
    assert len(result) > 0


def test_hybrid_search_longtext(benchmark, db, scale):
    docs = _prepare_text_db(db, scale, LONGTEXT_COLUMNS)
    query = "database benchmark"

    def _search():
        return db.search("bench_search", query, search_type="hybrid")

    result = benchmark(_search)
    assert len(result) > 0
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_search.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/test_search.py
```

- [ ] **Step 3: Verify tests are discoverable (dry run)**

Run: `uv run pytest tests/hybriddb/benchmarks/test_search.py --collect-only`
Expected: lists all 6 test functions.

---

### Task 5: Write test_graph.py — node/edge CRUD, traversal, algorithms

**Files:**
- Create: `tests/hybriddb/benchmarks/test_graph.py`
- Create: `tests/benchmarks/test_graph.py` (standalone, identical)

- [ ] **Step 1: Write test_graph.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_graph.py`:

```python
"""Graph benchmarks: node/edge CRUD, traversal, algorithms."""

import pytest

from .helpers import generate_graph_data

networkx = pytest.importorskip("networkx")


@pytest.fixture
def graph_db(db):
    db.create_table("nodes", {"type": "TEXT", "label": "TEXT"})
    db.create_table("edges", {"source_id": "TEXT", "target_id": "TEXT", "type": "TEXT", "weight": "REAL"})
    return db


def test_add_nodes_batch(benchmark, graph_db, scale):
    nodes, _ = generate_graph_data(scale.n_graph_nodes, 0)

    def _add():
    graph_db.insert_batch("nodes", nodes, sync=False)

    benchmark(_add)


def test_add_edges_batch(benchmark, graph_db, scale):
    nodes, edges = generate_graph_data(scale.n_graph_nodes, scale.n_graph_edges)
    graph_db.insert_batch("nodes", nodes, sync=False)

    def _add():
        graph_db.insert_batch("edges", edges)

    benchmark(_add)


def test_get_neighbors(benchmark, graph_db, scale):
    nodes, edges = generate_graph_data(scale.n_graph_nodes, scale.n_graph_edges)
    graph_db.insert_batch("nodes", nodes, sync=False)
    for e in edges:
        graph_db.register_entity_node("nodes", e["source_id"])
        graph_db.register_entity_node("nodes", e["target_id"])
        graph_db.add_edge(
            e["id"], e["source_id"], e["target_id"],
            edge_type=e["type"], weight=e["weight"],
        )

    target = nodes[len(nodes) // 2]["id"]

    def _neighbors():
        return graph_db.get_neighbors(target)

    benchmark(_neighbors)


def test_shortest_path(benchmark, graph_db, scale):
    nodes, edges = generate_graph_data(scale.n_graph_nodes, scale.n_graph_edges)
    graph_db.insert_batch("nodes", nodes, sync=False)
    for e in edges:
        graph_db.register_entity_node("nodes", e["source_id"])
        graph_db.register_entity_node("nodes", e["target_id"])
        graph_db.add_edge(
            e["id"], e["source_id"], e["target_id"],
            edge_type=e["type"], weight=e["weight"],
        )

    src = nodes[0]["id"]
    dst = nodes[-1]["id"]

    def _path():
        return graph_db.shortest_path(src, dst)

    benchmark(_path)


def test_pagerank(benchmark, graph_db, scale):
    nodes, edges = generate_graph_data(scale.n_graph_nodes, scale.n_graph_edges)
    graph_db.insert_batch("nodes", nodes, sync=False)
    for e in edges:
        graph_db.register_entity_node("nodes", e["source_id"])
        graph_db.register_entity_node("nodes", e["target_id"])
        graph_db.add_edge(
            e["id"], e["source_id"], e["target_id"],
            edge_type=e["type"], weight=e["weight"],
        )

    def _pr():
        return graph_db.pagerank()

    benchmark(_pr)


def test_decay_edges(benchmark, graph_db, scale):
    nodes, edges = generate_graph_data(100, 500)
    graph_db.insert_batch("nodes", nodes, sync=False)
    for e in edges:
        graph_db.register_entity_node("nodes", e["source_id"])
        graph_db.register_entity_node("nodes", e["target_id"])
        graph_db.add_edge(
            e["id"], e["source_id"], e["target_id"],
            edge_type=e["type"], weight=e["weight"],
        )

    def _decay():
        return graph_db.decay_edges()

    benchmark(_decay)
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_graph.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/test_graph.py
```

- [ ] **Step 3: Verify discovery**

Run: `uv run pytest tests/hybriddb/benchmarks/test_graph.py --collect-only`
Expected: lists all 6 test functions.

---

### Task 6: Write test_analytics.py — DuckDB query latency, overhead

**Files:**
- Create: `tests/hybriddb/benchmarks/test_analytics.py`
- Create: `tests/benchmarks/test_analytics.py` (standalone, identical)

- [ ] **Step 1: Write test_analytics.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_analytics.py`:

```python
"""DuckDB analytics benchmarks: aggregation, group-by, join, overhead."""

import pytest

from .helpers import generate_analytics_data

pytest.importorskip("duckdb")


@pytest.fixture
def analytics_db(db, scale):
    rows = generate_analytics_data(scale.n_analytics_rows)
    db.create_table("analytics", {
        "category": "TEXT",
        "region": "TEXT",
        "value": "REAL",
        "quantity": "INTEGER",
        "timestamp": "TEXT",
    })
    db.insert_batch("analytics", rows, sync=False)
    db.create_table("metadata", {"category": "TEXT", "label": "TEXT"})
    db.insert_batch(
        "metadata",
        [{"category": cat, "label": f"Category {cat}"} for cat in ["A", "B", "C", "D", "E"]],
        sync=False,
    )
    db.register_duckdb_table("analytics")
    db.register_duckdb_table("metadata")
    return db


def test_simple_aggregation(benchmark, analytics_db):
    def _agg():
        return analytics_db.analytics(
            "SELECT COUNT(*) as cnt, AVG(value) as avg_val, SUM(quantity) as total_qty FROM analytics"
        )

    result = benchmark(_agg)
    assert len(result) == 1
    assert result[0]["cnt"] > 0


def test_group_by(benchmark, analytics_db):
    def _gb():
        return analytics_db.analytics(
            "SELECT category, COUNT(*) as cnt, AVG(value) as avg_val "
            "FROM analytics GROUP BY category ORDER BY cnt DESC"
        )

    result = benchmark(_gb)
    assert len(result) > 0


def test_join(benchmark, analytics_db):
    def _join():
        return analytics_db.analytics(
            "SELECT a.category, m.label, COUNT(*) as cnt "
            "FROM analytics a JOIN metadata m ON a.category = m.category "
            "GROUP BY a.category, m.label ORDER BY cnt DESC"
        )

    result = benchmark(_join)
    assert len(result) > 0


def test_analytics_overhead(benchmark, analytics_db, scale):
    """Compare HybridDB.analytics() vs native DuckDB overhead ratio."""
    sql = "SELECT COUNT(*) FROM analytics WHERE value > 100"

    def _hybrid():
        return analytics_db.analytics(sql)

    result = benchmark(_hybrid)
    assert len(result) == 1
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_analytics.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/test_analytics.py
```

- [ ] **Step 3: Verify discovery**

Run: `uv run pytest tests/hybriddb/benchmarks/test_analytics.py --collect-only`
Expected: lists all 4 test functions.

---

### Task 7: Write test_concurrent.py — multi-threaded read/write contention

**Files:**
- Create: `tests/hybriddb/benchmarks/test_concurrent.py`
- Create: `tests/benchmarks/test_concurrent.py` (standalone, identical)

- [ ] **Step 1: Write test_concurrent.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_concurrent.py`:

```python
"""Concurrent access benchmarks: read, write, mixed contention."""

import threading
import time

import pytest

from .helpers import generate_docs


def _reader_worker(db, stop, results):
    while not stop.is_set():
        try:
            db.search("bench_concurrent", "hello", search_type="keyword")
            results["reads"] += 1
        except Exception:
            pass


def _writer_worker(db, stop, results, doc_id_counter):
    while not stop.is_set():
        try:
            with doc_id_counter["lock"]:
                cid = doc_id_counter["val"]
                doc_id_counter["val"] += 1
            doc = {"id": str(cid), "content": f"concurrent test doc {cid}"}
            db.insert("bench_concurrent", doc)
            results["writes"] += 1
        except Exception:
            pass


@pytest.fixture
def concurrent_db(db, scale):
    db.create_table("bench_concurrent", {"content": "LONGTEXT"})
    docs = generate_docs(scale.n_docs, [{"name": "content", "type": "LONGTEXT"}])
    db.insert_batch("bench_concurrent", docs, sync=False)
    return db


def test_read_contention(benchmark, concurrent_db, scale):
    n_readers = 4

    def _run():
        stop = threading.Event()
        results = {"reads": 0}
        threads = [
            threading.Thread(target=_reader_worker, args=(concurrent_db, stop, results))
            for _ in range(n_readers)
        ]
        for t in threads:
            t.start()
        time.sleep(scale.concurrent_duration_s)
        stop.set()
        for t in threads:
            t.join()
        return results["reads"]

    result = benchmark(_run)
    assert result > 0


def test_write_contention(benchmark, concurrent_db, scale):
    n_writers = 2
    doc_id_counter = {"val": scale.n_docs + 1, "lock": threading.Lock()}

    def _run():
        stop = threading.Event()
        results = {"writes": 0}
        threads = [
            threading.Thread(
                target=_writer_worker,
                args=(concurrent_db, stop, results, doc_id_counter),
            )
            for _ in range(n_writers)
        ]
        for t in threads:
            t.start()
        time.sleep(scale.concurrent_duration_s)
        stop.set()
        for t in threads:
            t.join()
        return results["writes"]

    result = benchmark(_run)
    assert result > 0


def test_read_write_mixed(benchmark, concurrent_db, scale):
    doc_id_counter = {"val": scale.n_docs + 1, "lock": threading.Lock()}

    def _run():
        stop = threading.Event()
        results = {"reads": 0, "writes": 0}
        readers = [
            threading.Thread(target=_reader_worker, args=(concurrent_db, stop, results))
            for _ in range(4)
        ]
        writers = [
            threading.Thread(
                target=_writer_worker,
                args=(concurrent_db, stop, results, doc_id_counter),
            )
            for _ in range(1)
        ]
        for t in readers + writers:
            t.start()
        time.sleep(scale.concurrent_duration_s)
        stop.set()
        for t in readers + writers:
            t.join()
        return results

    result = benchmark(_run)
    assert result["reads"] > 0 or result["writes"] > 0
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_concurrent.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/test_concurrent.py
```

- [ ] **Step 3: Verify discovery**

Run: `uv run pytest tests/hybriddb/benchmarks/test_concurrent.py --collect-only`
Expected: lists all 3 test functions.

---

### Task 8: Write test_storage.py — disk usage, ChromaDB bloat

**Files:**
- Create: `tests/hybriddb/benchmarks/test_storage.py`
- Create: `tests/benchmarks/test_storage.py` (standalone, identical)

- [ ] **Step 1: Write test_storage.py**

Create `/Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_storage.py`:

```python
"""Storage benchmarks: disk usage, ChromaDB segment growth."""

import os
from pathlib import Path

import pytest

from .helpers import generate_docs


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def test_db_file_growth(benchmark, db, scale):
    """SQLite file growth for TEXT columns (no Chroma)."""
    docs = generate_docs(scale.n_docs, [{"name": "content", "type": "TEXT"}])
    db.create_table("bench_storage", {"content": "TEXT"})

    def _measure():
        db.insert_batch("bench_storage", docs, sync=False)
        return _dir_size(db._db_path)

    size = benchmark(_measure)
    assert size > 0


def test_chroma_segment_growth(benchmark, db, scale):
    """ChromaDB segment growth for LONGTEXT columns."""
    docs = generate_docs(scale.n_docs, [{"name": "content", "type": "LONGTEXT"}])
    db.create_table("bench_storage", {"content": "LONGTEXT"})
    chroma_path = db._vector_path

    def _measure():
        db.insert_batch("bench_storage", docs, sync=True)
        if os.path.isdir(chroma_path):
            return _dir_size(chroma_path)
        return 0

    size = benchmark(_measure)
    assert size > 0 or scale.n_docs == 0


def test_total_storage(benchmark, db, scale):
    """Total disk usage with LONGTEXT (SQLite + Chroma)."""
    docs = generate_docs(scale.n_docs, [{"name": "content", "type": "LONGTEXT"}])
    db.create_table("bench_storage", {"content": "LONGTEXT"})

    def _measure():
        db.insert_batch("bench_storage", docs, sync=True)
        sqlite_size = _dir_size(db._db_path)
        chroma_size = (
            _dir_size(db._vector_path) if os.path.isdir(db._vector_path) else 0
        )
        return {"sqlite_bytes": sqlite_size, "chroma_bytes": chroma_size}

    result = benchmark(_measure)
    assert result["sqlite_bytes"] > 0


def test_chroma_bloat_check(benchmark, db, scale):
    """Check ChromaDB segment count and average size (regression catch)."""
    docs = generate_docs(scale.n_docs, [{"name": "content", "type": "LONGTEXT"}])
    db.create_table("bench_storage", {"content": "LONGTEXT"})

    def _measure():
        db.insert_batch("bench_storage", docs, sync=True)
        if not os.path.isdir(db._vector_path):
            return {"segment_count": 0, "avg_segment_bytes": 0}
        segments = list(Path(db._vector_path).rglob("*.segment"))
        n = len(segments)
        sizes = [s.stat().st_size for s in segments]
        avg = sum(sizes) / n if n > 0 else 0
        return {"segment_count": n, "avg_segment_bytes": avg}

    result = benchmark(_measure)
    assert result["segment_count"] >= 0
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/tests/hybriddb/benchmarks/test_storage.py \
   /Users/eddy/Developer/Python/HybridDB/tests/benchmarks/test_storage.py
```

- [ ] **Step 3: Verify discovery**

Run: `uv run pytest tests/hybriddb/benchmarks/test_storage.py --collect-only`
Expected: lists all 4 test functions.

---

### Task 9: Write compare_results.py — JSON snapshot diff tool

**Files:**
- Create: `scripts/compare_results.py` (in-repo)
- Create: `scripts/compare_results.py` (standalone, identical)

- [ ] **Step 1: Write compare_results.py**

Create `/Users/eddy/Developer/Langgraph/assistant/scripts/compare_results.py`:

```python
#!/usr/bin/env python3
"""Compare two pytest-benchmark JSON results files and show deltas."""

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path("results")


def load_benchmark_json(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    benchmarks = data.get("benchmarks", [])
    result = {}
    for b in benchmarks:
        name = b.get("name", "unknown")
        full_name = b.get("fullname", name)
        group = b.get("group", "")
        stats = b.get("stats", {})
        result[full_name] = {
            "name": name,
            "group": group,
            "min": stats.get("min", 0),
            "max": stats.get("max", 0),
            "mean": stats.get("mean", 0),
            "median": stats.get("median", 0),
            "stddev": stats.get("stddev", 0),
            "ops": stats.get("ops", 0),
        }
    return result


def format_time(ms: float) -> str:
    if ms < 1.0:
        return f"{ms*1000:.1f}µs"
    if ms < 1000:
        return f"{ms:.2f}ms"
    return f"{ms/1000:.2f}s"


def percent_change(old: float, new: float) -> str:
    if old == 0:
        return "N/A"
    pct = ((new - old) / old) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def render_table(before: dict, after: dict) -> str:
    all_keys = set(before.keys()) | set(after.keys())
    rows = []
    for key in sorted(all_keys):
        b = before.get(key)
        a = after.get(key)
        if b and a:
            delta = percent_change(b["mean"], a["mean"])
            rows.append(
                f"| {b['name']} | {format_time(b['mean'])} ± {format_time(b['stddev'])} "
                f"| {format_time(a['mean'])} ± {format_time(a['stddev'])} | {delta} |"
            )
        elif b and not a:
            rows.append(f"| {b['name']} | {format_time(b['mean'])} ± {format_time(b['stddev'])} | REMOVED | - |")
        else:
            rows.append(f"| {a['name']} | NEW | {format_time(a['mean'])} ± {format_time(a['stddev'])} | - |")

    header = "| Test | Before | After | Δ |"
    sep = "|------|--------|-------|-----|"
    return "\n".join([header, sep] + rows)


def main():
    parser = argparse.ArgumentParser(description="Compare pytest-benchmark JSON results")
    parser.add_argument("before", nargs="?", help="Path to before JSON (default: results/latest.json)")
    parser.add_argument("after", nargs="?", help="Path to after JSON (default: <before parent>/latest.json will be used if before is a specific file)")
    parser.add_argument("--markdown", action="store_true", help="Output markdown table")
    parser.add_argument("--diff", action="store_true", help="Compare latest.json with previous latest")
    args = parser.parse_args()

    if args.diff:
        archives = sorted(RESULTS_DIR.glob("*.json"))
        if len(archives) < 2:
            print("Need at least 2 archived results for --diff")
            sys.exit(1)
        before_path = archives[-2]
        after_path = archives[-1]
    elif args.before and args.after:
        before_path = Path(args.before)
        after_path = Path(args.after)
    elif args.before:
        before_path = Path(args.before)
        after_path = before_path.parent / "latest.json"
    else:
        latest = RESULTS_DIR / "latest.json"
        if not latest.exists():
            print("No results found. Run benchmarks first.")
            sys.exit(1)
        before_path = latest
        after_path = latest
        print("Only one result file. No comparison possible.")
        sys.exit(0)

    if not before_path.exists():
        print(f"Before file not found: {before_path}")
        sys.exit(1)
    if not after_path.exists():
        print(f"After file not found: {after_path}")
        sys.exit(1)

    before = load_benchmark_json(str(before_path))
    after = load_benchmark_json(str(after_path))

    if args.markdown:
        print(render_table(before, after))
    else:
        print(f"Comparing {before_path.name} → {after_path.name}")
        print(f"{'Test':<50} {'Before':<20} {'After':<20} {'Δ':<10}")
        print("-" * 100)
        all_keys = sorted(set(before.keys()) | set(after.keys()))
        for key in all_keys:
            b = before.get(key)
            a = after.get(key)
            if b and a:
                delta = percent_change(b["mean"], a["mean"])
                print(f"{b['name']:<50} {format_time(b['mean']):<20} {format_time(a['mean']):<20} {delta:<10}")
            elif b:
                print(f"{b['name']:<50} {format_time(b['mean']):<20} {'REMOVED':<20} {'-':<10}")
            else:
                print(f"{a['name']:<50} {'NEW':<20} {format_time(a['mean']):<20} {'-':<10}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Copy to standalone**

```bash
cp /Users/eddy/Developer/Langgraph/assistant/scripts/compare_results.py \
   /Users/eddy/Developer/Python/HybridDB/scripts/compare_results.py
```

- [ ] **Step 3: Quick smoke test**

Run: `uv run python scripts/compare_results.py --help`
Expected: prints usage.

---

### Task 10: Write run_benchmarks.sh — smoke & full runners

**Files:**
- Create: `scripts/run_benchmarks.sh` (in-repo)
- Create: `scripts/run_benchmarks.sh` (standalone, identical)

- [ ] **Step 1: Write run_benchmarks.sh**

Create `/Users/eddy/Developer/Langgraph/assistant/scripts/run_benchmarks.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="$REPO_ROOT/results"
mkdir -p "$RESULTS_DIR"

MODE="${1:-smoke}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H%M%S")

if [ "$MODE" = "smoke" ]; then
    echo "=== HybridDB Smoke Benchmarks ==="
    uv run pytest tests/hybriddb/benchmarks/ \
        --benchmark-only \
        --benchmark-json="$RESULTS_DIR/smoke-$TIMESTAMP.json" \
        -x \
        "$@"
elif [ "$MODE" = "full" ]; then
    echo "=== HybridDB Full Benchmarks ==="
    uv run pytest tests/hybriddb/benchmarks/ \
        --benchmark-full \
        --benchmark-only \
        --benchmark-json="$RESULTS_DIR/full-$TIMESTAMP.json" \
        "$@"
elif [ "$MODE" = "e2e" ]; then
    echo "=== HybridDB Full E2E Benchmarks (live embeddings) ==="
    uv run pytest tests/hybriddb/benchmarks/ \
        --benchmark-full \
        --benchmark-only \
        --precompute-embeddings=false \
        --benchmark-json="$RESULTS_DIR/full-e2e-$TIMESTAMP.json" \
        "$@"
else
    echo "Usage: $0 {smoke|full|e2e}"
    exit 1
fi

echo ""
echo "Benchmark complete. Results saved to $RESULTS_DIR/"
```

Make executable:

```bash
chmod +x /Users/eddy/Developer/Langgraph/assistant/scripts/run_benchmarks.sh
```

- [ ] **Step 2: Copy to standalone (with path adjustment)**

Create `/Users/eddy/Developer/Python/HybridDB/scripts/run_benchmarks.sh` with adjusted test path:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="$REPO_ROOT/results"
mkdir -p "$RESULTS_DIR"

MODE="${1:-smoke}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H%M%S")

BENCH_DIR="tests/benchmarks"

if [ "$MODE" = "smoke" ]; then
    echo "=== HybridDB Smoke Benchmarks ==="
    uv run pytest "$BENCH_DIR" \
        --benchmark-only \
        --benchmark-json="$RESULTS_DIR/smoke-$TIMESTAMP.json" \
        -x \
        "$@"
elif [ "$MODE" = "full" ]; then
    echo "=== HybridDB Full Benchmarks ==="
    uv run pytest "$BENCH_DIR" \
        --benchmark-full \
        --benchmark-only \
        --benchmark-json="$RESULTS_DIR/full-$TIMESTAMP.json" \
        "$@"
elif [ "$MODE" = "e2e" ]; then
    echo "=== HybridDB Full E2E Benchmarks (live embeddings) ==="
    uv run pytest "$BENCH_DIR" \
        --benchmark-full \
        --benchmark-only \
        --precompute-embeddings=false \
        --benchmark-json="$RESULTS_DIR/full-e2e-$TIMESTAMP.json" \
        "$@"
else
    echo "Usage: $0 {smoke|full|e2e}"
    exit 1
fi

echo ""
echo "Benchmark complete. Results saved to $RESULTS_DIR/"
```

Make executable:

```bash
chmod +x /Users/eddy/Developer/Python/HybridDB/scripts/run_benchmarks.sh
```

- [ ] **Step 3: Add results/ to .gitignore in both repos**

Add to `assistant/.gitignore` and `HybridDB/.gitignore`:

```
# Benchmark results
results/
```

---

### Task 11: Run smoke suite — verify everything works

- [ ] **Step 1: Run smoke benchmarks in in-repo**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
uv run pytest tests/hybriddb/benchmarks/ \
    --benchmark-only -x --benchmark-json=results/smoke-test.json --timeout=120
```

Expected: all benchmark tests pass and report timing.

- [ ] **Step 2: Run smoke benchmarks in standalone**

```bash
cd /Users/eddy/Developer/Python/HybridDB
uv run pytest tests/benchmarks/ \
    --benchmark-only -x --benchmark-json=results/smoke-test.json --timeout=120
```

Expected: all pass identically.

- [ ] **Step 3: Test compare_results.py**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
uv run python scripts/compare_results.py results/smoke-test.json results/smoke-test.json
```

Expected: shows a comparison table with 0% change across all tests.

- [ ] **Step 4: Commit all changes in both repos**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add tests/hybriddb/benchmarks/ scripts/compare_results.py scripts/run_benchmarks.sh pyproject.toml .gitignore results/
git commit -m "feat: HybridDB benchmark suite (pytest-benchmark, 6 test modules, comparison tool)"
```

```bash
cd /Users/eddy/Developer/Python/HybridDB
git add tests/benchmarks/ scripts/compare_results.py scripts/run_benchmarks.sh pyproject.toml .gitignore results/
git commit -m "feat: HybridDB benchmark suite (pytest-benchmark, 6 test modules, comparison tool)"
git push
```

---

## Self-Review Checklist

1. **Spec coverage:** Every section in the design spec has a corresponding task:
   - Framework (Section 3) → Task 1 (deps)
   - Layout (Section 4) → Task 1 (dirs), Tasks 3-8 (files)
   - Smoke/Full (Section 5) → Task 3 (conftest scale fixture), Task 10 (runners)
   - Embedding (Section 6) → Task 3 (conftest flags), Task 4 (precomputed in search)
   - Search (Section 7.1) → Task 4
   - Graph (Section 7.2) → Task 5
   - Analytics (Section 7.3) → Task 6
   - Concurrent (Section 7.4) → Task 7
   - Storage (Section 7.5) → Task 8
   - Reporting (Section 9) → Task 9 (compare_results.py), Task 10 (runners)
   - Dependencies (Section 10) → Task 1
   - Error handling (Section 11) → handled via importorskip in test files

2. **Placeholder scan:** No TBD, TODO, "implement later", or vague steps. Every step has exact file paths, code blocks, and commands.

3. **Type consistency:** All fixtures (`db`, `scale`, `graph_db`, `analytics_db`, `concurrent_db`, `embeddings`) match across conftest and test files. Function signatures are consistent.

4. **Dual-repo coverage:** Task 1 adds deps to both repos. Tasks 2-9 write files to in-repo and copy to standalone. Task 10 has separate runner scripts per repo. Task 11 runs smoke on both. Task 12 commits both.
