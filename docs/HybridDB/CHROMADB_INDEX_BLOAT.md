# ChromaDB HNSW Index Bloat — Investigation & Proposed Fix

## Problem Summary

`~/Assistant/Workspaces/personal/vectors/` consumed **76.95 GB** of disk space for what should require ~**0.6 GB** — a **127× blow-up**. The root cause is hnswlib's incremental resize + fallocate behavior inside ChromaDB 1.5.0.

## Discovery Context

Encountered on 2026-05-01 after noticing that `~/Assistant/Workspaces/personal` took over 90 GB. Investigation revealed a single ChromaDB collection (`messages_content`) with its HNSW index file consuming most of the space.

## Detailed Measurements

### Collection State

| Metric | Value |
|--------|-------|
| Collection name | `messages_content` |
| Embedding dimension | 384 (all-MiniLM-L6-v2) |
| Total embeddings | 231,744 |
| Role: assistant | 116,556 |
| Role: user | 114,722 |
| Role: tool | 466 |

### Date Distribution of Messages

| Period | Count | Source |
|--------|-------|--------|
| Dec 2022 – 2023 | ~4,000 | Historical/conversation data |
| Apr 2026 | 67,848 | Evaluation runs |
| **May 2026** | **159,944** | Evaluation runs |

The massive spike in Apr–May 2026 aligns with long-memory evaluation runs (visible as `lme_eval_user_*` and `lme_qa_*` subdirectories under `data/users/`). Messages were added in rapid succession (100+ calls/second) during automated persona-based testing.

### File-Level Breakdown

| File | Size | Expected | Notes |
|------|------|----------|-------|
| `link_lists.bin` | **74.88 GB** | ~30 MB | HNSW neighbor graph. 2,496× expected |
| `chroma.sqlite3` | **1.74 GB** | ~250 MB | Metadata, embeddings, FTS index |
| `data_level0.bin` | **371.5 MB** | ~356 MB | Raw float32 vectors. ✓ Expected |
| `index_metadata.pickle` | 6.8 MB | — | ChromaDB metadata |
| `length.bin` | 908 KB | — | HNSW per-node level data |
| `header.bin` | 100 bytes | — | HNSW config |

### HNSW Header Analysis

Parsed from `header.bin` at the HNSW segment level:

```
offsetLevel0:        1
max_elements:        1,125,899,906,842,624  ← CORRUPTED (should be ~262K)
cur_element_count:   998,343,673,118,720    ← CORRUPTED (should be ~232K)
size_data_per_element: 7,198,365,188,096    ← CORRUPTED (should be 1536)
label_offset:        7,164,005,449,728      ← CORRUPTED
offsetData:          566,935,683,072        ← CORRUPTED
M:                   0                      ← CORRUPTED (should be 16)
maxM:                5                      ← low (should be 16–32)
maxM0:               170,269                ← CORRUPTED (should be 16–64)
ml:                  0.0                    ← likely OK (1/ln(M)≈0.36)
```

The `data_level0.bin` stores correctly-sized data (232K × 1536 bytes = 356 MB), confirming the actual vector data is intact. The header corruption is confined to the link structure metadata.

### Physical Allocation Check

```
link_lists.bin logical size:  74.88 GB
link_lists.bin physical size: 74.88 GB  (100% allocated, NOT sparse)
```
- First 2 MB: contains non-zero data
- Remaining 74.88 GB: all zero bytes, but **physically allocated** on disk

## Root Cause

### What Happened

1. **ChromaDB 1.5.0 uses hnswlib** internally for HNSW vector indexing.
2. hnswlib's `addPoint()` calls `resizeIndex()` when the index reaches capacity. `resizeIndex()` doubles the capacity (or increases by a fixed amount), then calls `fallocate()` to pre-allocate the larger `link_lists.bin` file.
3. **APFS on macOS** does not reliably create sparse holes for `fallocate()` — it physically writes zeroed pages to disk. On Linux with proper sparse file support, the same operation would appear as a 75 GB file consuming only ~2 MB of actual disk blocks.
4. During the evaluation's rapid-fire adds (231K messages in hours), hnswlib resized the index **multiple times** (typical doubling: 4096 → 8192 → … → 524,288), each time calling `fallocate()` on APFS. Each resize physically allocated zeros for the new capacity.
5. The final resize to ~1 billion elements corrupted the header fields (`max_elements`, `maxM0`, etc.) because the internal bookkeeping didn't handle the rapid resize cascade correctly.
6. After ChromaDB persisted the final state, that 74.88 GB zeroed file was committed to disk permanently.

### Why 127× Not 260×

The earlier 260× figure included some estimate errors. The corrected calculation:

- Expected data: 232K × 1536 bytes = 356 MB ✓ (matches `data_level0.bin`)
- Expected links: 232K × 32 (maxM0) × 4 bytes = 30 MB for uint32 links
- Expected SQLite: ~250 MB (embeddings + metadata + FTS)
- Expected total: ~636 MB
- Actual: 76,950 MB
- Blow-up: **121×** (range 120–130× depending on link encoding assumptions)

### Why Not Detected Sooner

- The evaluation framework (`tests/evaluation/evaluate.py`) runs via HTTP against the agent — it doesn't monitor disk usage.
- There is no periodic health check on the ChromaDB index files in `HybridDB` (see `src/sdk/hybrid_db.py`).
- The app continued to function normally — vector search works correctly on the first 232K vectors despite the bloated file, because hnswlib reads only the first 2 MB (the actual link data) and ignores the zero-filled tail.

## Impact

| Concern | Assessment |
|---------|-----------|
| Disk usage | **Critical** — 75 GB wasted for data that should be 0.6 GB |
| Functional correctness | None — search operates on intact data in first 2 MB |
| Performance | Slight — file open time for 75 GB file adds minor latency |
| Reproducibility | **Likely in production** — any sustained batch of adds (import, sync, eval) on macOS/Windows (neither supports proper fallocate sparseness) will trigger the same cascade |
| Data loss risk | None — affected files can be rebuilt from SQLite + re-embedding |

## Proposed Fix

### 1. HealthCheck + Auto-Rebuild on Startup

Add to `HybridDB.__init__()` (after `_init_chroma()`):

```python
self._check_index_health()
```

The `_check_index_health()` method scans all HNSW segment directories under `vectors/`, reads `header.bin` for each, and flags any index where:

- `link_lists.bin` size exceeds a configurable threshold (default: 5 GB, or computed as `cur_count × maxM0 × 8 × 2` ceiling)
- Header fields are obviously corrupted (e.g., `max_elements` > 10 × actual count, `maxM0` > 256)

When a bloated/corrupted index is detected, it triggers `_rebuild_chroma_index()`:

```python
def _rebuild_chroma_index(self) -> None:
    """Rebuild ChromaDB index from scratch at a temp path, then swap."""
    import shutil
    import tempfile

    old_path = Path(self._vector_path)
    temp_path = Path(tempfile.mkdtemp(prefix="chroma_rebuild_"))
    try:
        new_client = chromadb.PersistentClient(
            path=str(temp_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        old_client = self._chroma

        # Copy each collection: get all → add all in bulk
        collections = old_client.list_collections()
        for coll_name in collections:
            old = old_client.get_collection(coll_name)
            new = new_client.create_collection(name=coll_name, metadata=old.metadata)
            # Fetch data in batches, add to new collection
            batch_size = 5000
            offset = 0
            while offset < old.count():
                result = old.get(limit=batch_size, offset=offset, include=["embeddings", "documents", "metadatas"])
                if not result["ids"]:
                    break
                new.add(
                    ids=result["ids"],
                    embeddings=result["embeddings"],
                    documents=result["documents"],
                    metadatas=result["metadatas"],
                )
                offset += batch_size

        # Atomic swap: close old, move old aside, move new in place
        backup_path = Path(str(old_path) + ".backup_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S"))
        shutil.move(str(old_path), str(backup_path))
        shutil.move(str(temp_path), str(old_path))

        # Update our client reference
        self._chroma = chromadb.PersistentClient(
            path=str(old_path / "vectors") if "vectors" not in str(old_path) else str(old_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    finally:
        # Cleanup temp if still exists
        if temp_path.exists():
            shutil.rmtree(str(temp_path), ignore_errors=True)
```

### 2. Defensive Configuration

- Add `max_chroma_index_gb` setting (default 5 GB) to the config system
- Log warning at 50% threshold, auto-rebuild at 100% threshold
- Expose forced rebuild as a CLI command / API endpoint for operational use

### 3. Evaluation Framework Awareness

- Add disk usage monitoring to `tests/evaluation/evaluate.py`
- Emit warning when `vectors/` directory exceeds expected size
- Optionally run `reconcile()` + index rebuild between evaluation batches

### 4. Upstream Dependency Upgrade

- Track ChromaDB releases for fixes to hnswlib's resize strategy
- Consider pinning hnswlib version once a sparsity-aware resize is available
- Alternative: switch to `chromadb.EphemeralClient` for evaluation contexts and `PersistentClient` only for production

## Files Affected

| File | Change |
|------|--------|
| `src/sdk/hybrid_db.py` | Add `_check_index_health()`, `_rebuild_chroma_index()`, call from `__init__` |
| `src/config/settings.py` | Add `max_chroma_index_gb` config field |
| `src/sdk/tools_core/apps.py` | Expose index health via `apps_health` tool (optional) |
| `tests/evaluation/evaluate.py` | Add disk usage monitoring |
| `tests/sdk/test_hybrid_db.py` | Add tests for index health check + rebuild |

## To Reproduce (for Testing the Fix)

```python
import chromadb
from chromadb.config import Settings
import os, shutil

# Create 250K embeddings in rapid succession
test_path = "/tmp/chroma_bloat_test"
shutil.rmtree(test_path, ignore_errors=True)

client = chromadb.PersistentClient(
    path=test_path,
    settings=Settings(anonymized_telemetry=False),
)
coll = client.get_or_create_collection("test", metadata={"hnsw:space": "cosine"})

# Add vectors rapidly to trigger resize cascade
for i in range(0, 250000, 1000):
    batch_ids = [str(j) for j in range(i, min(i+1000, 250000))]
    batch_embeddings = [[float(j % 384) / 384.0 for j in range(384)] for _ in batch_ids]
    batch_docs = [f"document {j}" for j in range(i, min(i+1000, 250000))]
    coll.add(ids=batch_ids, embeddings=batch_embeddings, documents=batch_docs)

# Check disk usage
total = 0
for root, dirs, files in os.walk(test_path):
    for f in files:
        total += os.path.getsize(os.path.join(root, f))
print(f"Total disk: {total / 1024**3:.2f} GB")
```

## Open Questions for Review

1. **Rebuild vs. delete-and-recreate**: A rebuild preserves existing collection metadata (e.g., `hnsw:space`). A delete-and-recreate is simpler but loses per-collection config. Which is safer?

2. **Rebuild timing**: Should we block startup until rebuild completes? For a 232K-vector index the rebuild takes ~30–60 seconds (re-embedding + HNSW build). That blocks app startup. Should it run in the background with a "rebuilding" status exposed via health endpoint?

3. **Evaluation isolation**: Should `HybridDB` accept a flag to use `EphemeralClient` when in test/eval mode? This would prevent the issue entirely but requires plumbing through the dependency chain (HybridDB → MessageStore → MemoryMiddleware → AgentLoop).

4. **APFS sparsity**: macOS `fallocate()` behavior means any large `link_lists.bin` file will physically allocate space. Can we file a bug with ChromaDB/hnswlib to use `ftruncate` instead of `fallocate` on macOS? Or is there an APFS-specific workaround?
