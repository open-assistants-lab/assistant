# Assistant - Architecture & Storage

## Overview

Assistant is a multi-channel AI assistant (CLI, HTTP) with long-term memory using **SQLite + FTS5 + ChromaDB** for hybrid search.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Channels (CLI/HTTP)                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    SDK Agent Loop (ReAct)                  │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Tools: memory_get_history, memory_search      │   │
│  │        filesystem, shell, web, email, etc.      │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              MessageStore (Hybrid Search)            │
│  ┌──────────────────┐    ┌──────────────────────────┐    │
│  │ SQLite + FTS5   │    │    ChromaDB           │    │
│  │ (keyword search)│    │    (vector search)    │    │
│  └──────────────────┘    └──────────────────────────┘    │
│              │                         │                  │
│              └───────────┬───────────┘                  │
│                          ▼                              │
│              ┌─────────────────────┐                   │
│              │  Hybrid Scorer     │                   │
│              │  (relevance 70%)  │                   │
│              │  (recency 30%)    │                   │
│              └─────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 Storage (Per User)                          │
│  data/users/{user_id}/.conversation/                     │
│  ├── messages.db        # SQLite with FTS5              │
│  └── vectors/          # ChromaDB embeddings          │
└─────────────────────────────────────────────────────────────┘
```

## Why SQLite + FTS5 + ChromaDB?

### Benchmark Results

| Metric | SQLite+vec | ChromaDB | Winner |
|--------|------------|----------|--------|
| Single Insert | 0.18ms | 2.5ms | **SQLite 13x faster** |
| 1 Year Insert | 2 sec | 20 sec | **SQLite 10x faster** |
| Keyword Search | 0.17ms | 0.16ms | ~Tie |
| Vector Search (10k) | 78ms | 0.5ms | **ChromaDB 156x faster** |
| Vector Search (1M) | 7,942ms | 0.4ms | **ChromaDB 20kx faster** |
| Accuracy | 100% | 100% | Tie |

**ChromaDB wins for vector search at scale** due to ANN indexing.

### Why This Stack?

1. **Fast inserts** - SQLite is 10-20x faster for writes
2. **Single file storage** - SQLite is simpler (backup/migrate one file)
3. **Fast search at scale** - ChromaDB ANN index handles 1M+ messages
4. **Hybrid search** - Combine keyword + vector for best results
5. **Recency boost** - Newer messages rank higher

## Storage Details

### Messages Table (SQLite + FTS5)

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,        -- ISO timestamp
    role TEXT NOT NULL,      -- 'user' or 'assistant'
    content TEXT NOT NULL,   -- Message content
    metadata JSON            -- Optional metadata
);

CREATE VIRTUAL TABLE messages_fts USING fts5(content);
-- FTS5 triggers keep index in sync
```

### Vectors (ChromaDB)

- Persistent storage in `vectors/` directory
- 384-dimensional embeddings
- Cosine similarity search

### Search Scoring

```
final_score = (relevance × 0.7) + (recency × 0.3)

relevance = keyword_score + vector_similarity
recency = 1.0 / (1 + days_ago / 30)
```

## Usage

### CLI

```bash
uv run assistant cli
```

### HTTP API

```bash
uv run assistant http
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGES_PATH` | `data/users/{user_id}/.conversation/messages.db` | SQLite DB path |

## Benchmarks

See `docs/benchmarks/` for detailed benchmarks:

- `test_storage_benchmark.py` - SQLite+vec vs ChromaDB comparison
- `test_1year_production.py` - 1 year simulation (10,950 messages)
- `test_search_recency.py` - Recency scoring impact

### 1 Year Production Test Results

| Metric | Result |
|--------|--------|
| Messages | 10,950 |
| Insert Time | 2 seconds |
| Keyword Search | 0.07ms |
| Vector Search | 1.6ms |
| Hybrid Search | 0.6ms |
| Storage | 31 MB |

## Files

```
src/
├── sdk/
│   ├── loop.py              # AgentLoop (ReAct)
│   ├── tools_core/          # SDK-native tool implementations
│   │   ├── memory.py        # Memory tools
│   │   ├── filesystem.py    # Filesystem tools
│   │   └── ...
│   └── providers/           # LLM providers
├── cli/main.py              # CLI interface
└── http/main.py             # HTTP API
```
