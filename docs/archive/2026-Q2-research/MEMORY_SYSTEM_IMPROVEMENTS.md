# HybridDB — Improvement Roadmap for Agent-Purpose Storage

> Date: 2026-04-23  
> Context: Reviewing the current HybridDB (SQLite + FTS5 + ChromaDB) and recommending improvements for a purpose-built agent memory database.

---

## Current State

HybridDB is a solid foundation. It already has:

- **SQLite + FTS5** for keyword search (0.17ms, 10k msgs)
- **ChromaDB** for vector search (0.5ms, 10k; 0.4ms, 1M)
- **Hybrid scoring**: `relevance × 0.7 + recency × 0.3`
- **Self-healing journal** — operation log that reconciles SQLite/FTS5/ChromaDB inconsistencies
- **Per-user isolation** — each user gets their own `app.db` + `vectors/`
- **WAL mode** for concurrent reads
- **Confidence scoring** with boost/decay
- **Supersession** — old memories marked as superseded, not deleted
- **Progressive disclosure** — compact/summary/full context levels
- **Connection graph** — memories linked with typed relationships

The architecture is good. But it was designed for *search*, not for *agent cognition*. Here's what an agent-purpose database needs that HybridDB doesn't have yet.

---

## The Problem: Agent Memory ≠ Search Engine

Current HybridDB treats all data as **documents to be searched**. But an agent's memory is more like a **knowledge graph with temporal awareness**. The agent needs to:

1. **Recall** — "What did the user say about X?" (current: ✅ working)
2. **Reason** — "Given what I know about X and Y, what should I conclude?" (current: ❌ missing)
3. **Evolve** — "My understanding of X changed; update all related memories" (current: ⚠️ partial)
4. **Forget** — "This is stale; deprioritize without deleting" (current: ⚠️ crude)
5. **Consolidate** — "These 5 memories say the same thing; merge them" (current: ⚠️ LLM-dependent, fragile)

---

## Recommendations (Priority Order)

### 1. Temporal Awareness — Time-Weighted Recency with Decay Curves
**Priority: HIGH | Complexity: LOW**

Current scoring: `recency = 1.0 / (1 + days_ago / 30)` — a single fixed decay.

**Improvement**: Make decay domain-aware and configurable:

```python
DECAY_PROFILES = {
    "preference": 180,    # Preferences decay slowly (6-month half-life)
    "fact": 90,            # Facts decay moderately (3-month half-life)
    "workflow": 365,       # Workflows persist long (1-year half-life)
    "correction": 30,     # Corrections decay fast (1-month half-life)
    "lesson": 60,         # Lessons moderate (2-month half-life)
}

def recency_score(days_ago: float, memory_type: str) -> float:
    half_life = DECAY_PROFILES.get(memory_type, 90)
    return 0.5 ** (days_ago / half_life)  # Exponential decay
```

**Why**: A preference ("I prefer dark mode") should persist for months. A correction ("not Tuesday, Wednesday") should decay quickly unless reinforced. The current `1/(1+days/30)` treats everything identically.

### 2. Episodic Memory — Conversation Context Beyond Search
**Priority: HIGH | Complexity: MEDIUM**

Current: Messages are stored as flat rows with `ts`, `role`, `content`, `metadata`. No session grouping, no conversation threading, no "what happened when" narrative.

**Improvement**: Add session markers and conversation summaries to the messages table:

```sql
ALTER TABLE messages ADD COLUMN session_id TEXT;
ALTER TABLE messages ADD COLUMN turn_number INTEGER;

-- New table: conversation sessions
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    message_count INTEGER,
    summary LONGTEXT,           -- LLM-generated summary
    key_topics JSON,            -- ["email", "scheduling", "project X"]
    emotional_tone TEXT,        -- "neutral", "frustrated", "excited"
    outcome TEXT                -- "resolved", "deferred", "unclear"
);
```

**Why**: When the agent says "last time we discussed this, you said X", it needs to know *which conversation* that was, not just find a keyword match. Sessions let the agent reconstruct narrative context — "On Tuesday you were frustrated about email, and we decided to set up filters."

### 3. Memory Graph — Typed Edges Between Memories
**Priority: MEDIUM | Complexity: MEDIUM**

Current: `linked_to` is a JSON column with `[{target_id, relationship, strength}]`. This is a graph stored as denormalized JSON — no graph queries, no traversal, no path finding.

**Improvement**: Add a proper edge table:

```sql
CREATE TABLE memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship TEXT NOT NULL,    -- 'relates_to', 'contradicts', 'updates', 'causes', 'depends_on'
    strength REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES memories(id),
    FOREIGN KEY (target_id) REFERENCES memories(id)
);

CREATE INDEX idx_edges_source ON memory_edges(source_id);
CREATE INDEX idx_edges_target ON memory_edges(target_id);
CREATE INDEX idx_edges_rel ON memory_edges(relationship);
```

**Why**: This enables:
- **Path queries**: "What chain of events led to this preference?" (`WITH RECURSIVE` CTE)
- **Contradiction detection**: "Does the user have conflicting preferences?" (`WHERE relationship = 'contradicts'`)
- **Causal reasoning**: "Given X caused Y, should I also update Z?" (transitive closure)
- **Graph visualization**: For the Flutter app, render memory connections visually

### 4. Structured Extraction — Schema-Enforced Memory Fields
**Priority: MEDIUM | Complexity: MEDIUM**

Current: `structured_data` is a LONGTEXT (JSON blob) with no schema enforcement. The agent can store anything, but can't query it reliably.

**Improvement**: Define memory type schemas:

```python
MEMORY_SCHEMAS = {
    "preference": {
        "preference_key": "TEXT",     # e.g., "theme", "email_mode"
        "preference_value": "TEXT",   # e.g., "dark", "batch"
        "confidence": "REAL",
        "source": "TEXT",            # "explicit" or "learned"
    },
    "contact_fact": {
        "person": "TEXT",
        "attribute": "TEXT",         # "birthday", "company", "role"
        "value": "TEXT",
    },
    "schedule": {
        "event_name": "TEXT",
        "date": "TEXT",
        "recurrence": "TEXT",         # "weekly", "monthly", "one-time"
        "time": "TEXT",
    },
    "decision": {
        "topic": "TEXT",
        "decision": "TEXT",
        "rationale": "TEXT",
        "made_at": "TEXT",
    },
}
```

**Why**: Structured queries like "What are all the user's email preferences?" or "What decisions has the user made about project X?" become trivial SQL instead of JSON path queries. It also makes the Flutter app's API cleaner.

### 5. Vector Search Without ChromaDB — sqlite-vec
**Priority: MEDIUM | Complexity: MEDIUM**

Current: ChromaDB is a separate process/directory with its own client library. It adds:
- Startup time (load collections)
- Embedding computation cost per query
- Two-system consistency risk (ChromaDB can drift from SQLite)

**Improvement**: Replace ChromaDB with [sqlite-vec](https://github.com/asg017/sqlite-vec) — a SQLite extension for vector search:

```sql
CREATE VIRTUAL TABLE vec_messages USING vec0(
    embedding float[384]
);

-- Then query like:
SELECT m.id, m.content, v.distance
FROM messages m
JOIN vec_messages v ON m.id = v.rowid
WHERE v.embedding MATCH ?
ORDER BY v.distance
LIMIT 10;
```

**Why**: 
- **One system instead of two** — no consistency risk, no reconciliation needed
- **Simpler deployment** — no ChromaDB dependency, no `vectors/` directory
- **Faster for medium-scale** — sqlite-vec is competitive with ChromaDB under 1M vectors
- **Per-user isolation stays clean** — each `app.db` contains both relational and vector data
- **Current benchmark gap**: ChromaDB beats sqlite-vec at 1M+ vectors, but sqlite-vec is faster under 100k (which is where most users will be)

**Migration path**: Keep ChromaDB as optional backend for users who hit 1M+ vectors. Default to sqlite-vec.

### 6. Automatic Embedding — Lazy + Batch
**Priority: MEDIUM | Complexity: LOW**

Current: Embeddings are generated on every `search_semantic()` and `search_hybrid()` call. If a user searches 5 times, the query embedding is computed 5 times.

**Improvement**: Cache query embeddings and use batch insertion:

```python
class HybridDB:
    def __init__(self, ...):
        self._embedding_cache: dict[str, list[float]] = {}
        self._embedding_lru_max = 1000
        self._pending_inserts: list[tuple[str, str, list[float]]] = []
        self._batch_size = 50
        self._batch_timer_seconds = 5.0
```

**Why**: Embedding computation is the #1 latency cost in search. Caching query embeddings eliminates repeated computation. Batch inserts reduce ChromaDB round-trips from N to N/batch_size.

### 7. Importance Scoring — Multi-Factor Ranking
**Priority: LOW-MEDIUM | Complexity: LOW**

Current: Final score = `relevance × 0.7 + recency × 0.3`. Only two factors, static weights.

**Improvement**: Add multi-factor scoring:

```python
def compute_score(
    relevance: float,      # keyword + vector similarity
    recency: float,        # time-based decay
    confidence: float,      # from memory.confidence
    access_count: int,      # from memory.access_count
    importance: float,      # from memory.importance (user-assigned 1-10)
    source: str,           # "explicit" or "learned"
) -> float:
    source_weight = 1.5 if source == "explicit" else 1.0
    access_weight = min(1.0 + access_count * 0.05, 1.3)  # Boosted by access, capped at 1.3
    
    return (
        relevance * 0.40 +
        recency * 0.25 +
        confidence * 0.15 +
        (importance / 10.0) * 0.10 +
        access_weight * 0.10
    ) * source_weight
```

**Why**: The current weighting is fine for search, but for agents, the scoring should reflect *how important the agent considers this memory*. Explicit preferences should outrank learned ones. Frequently accessed memories should rank higher. User-assigned importance should matter.

### 8. Memory Versioning — Edit History
**Priority: LOW | Complexity: LOW**

Current: When a memory is updated, the old version is overwritten. No history.

**Improvement**: Add a version table:

```sql
CREATE TABLE memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    trigger TEXT,
    action TEXT,
    confidence REAL,
    changed_at TEXT NOT NULL,
    changed_by TEXT,      -- "user" or "agent" or "consolidation"
    reason TEXT,          -- "user_correction", "observation_boost", "supersession"
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
```

**Why**: An agent should be able to say "You used to prefer X, but changed to Y last week." Without version history, the agent can only see the current state. This is also useful for debugging — when a memory goes wrong, you need to know how it evolved.

### 9. Context Window Budget — Token-Aware Injection
**Priority: HIGH for agents | Complexity: MEDIUM**

Current: `get_compact_context()` returns up to N memories. No consideration for token budget.

**Improvement**: Add token-aware context building:

```python
def get_context_within_budget(
    self,
    token_budget: int = 2000,
    detail_level: str = "summary",
    query: str | None = None,
) -> str:
    """Build context that fits within a token budget, prioritizing relevant memories."""
    
    # 1. If query, get relevant memories first (semantic search)
    # 2. Fill remaining budget with working memories sorted by confidence
    # 3. Use compact format if budget is tight, summary if moderate, full if generous
    # 4. Always include: user name, key preferences, active corrections
    # 5. Never exceed token_budget
```

**Why**: This is the **single highest-impact improvement for agent performance**. An agent that injects 5,000 tokens of memory context into every LLM call is wasting money and degrading output quality. The agent should dynamically adjust context based on:
- How much context is already in the conversation
- How complex the current task is
- Which memories are relevant to the current query
- The model's context window size

This is what Hermes Agent calls "context rot" — too much irrelevant memory crammed into every turn. Token-aware injection prevents it.

### 10. Consolidation Without LLM — Deterministic Merge Rules
**Priority: LOW | Complexity: LOW**

Current: `consolidation.py` calls an LLM to find contradictions and generate insights. This is fragile (LLM hallucination, cost, latency) and can't run in the background cheaply.

**Improvement**: Add deterministic consolidation rules that run without an LLM:

```python
DETERMINISTIC_RULES = [
    # Same trigger, different actions → supersede the older one
    SameTriggerRule(),
    
    # Opposite preferences → flag for user clarification
    ContradictionRule(opposites=[
        ("like", "dislike"), ("prefer", "avoid"), ("want", "don't want"),
    ]),
    
    # Low confidence + old + never accessed → deprioritize
    StaleRule(max_age_days=90, min_accesses=0, max_confidence=0.3),
    
    # High confidence + frequently accessed + recent → promote to working memory
    PromoteRule(min_confidence=0.5, min_accesses=3, max_age_days=30),
    
    # Same domain, overlapping content → merge
    MergeRule(similarity_threshold=0.8),
]
```

**Why**: Deterministic rules are fast, free, and repeatable. LLM-based consolidation should be a *fallback* for ambiguous cases, not the primary mechanism. This also enables background consolidation on every write instead of batch processing.

---

## Implementation Priority

| Priority | Improvement | Impact | Effort |
|----------|-------------|--------|--------|
| **P0** | Context Window Budget (token-aware injection) | 🚀 Highest agent perf gain | Medium |
| **P0** | Temporal Awareness (domain-aware decay) | High relevance improvement | Low |
| **P1** | Episodic Memory (sessions + summaries) | Enables "last time we talked about..." | Medium |
| **P1** | Automatic Embedding (cache + batch) | 3-5x search latency reduction | Low |
| **P2** | Memory Graph (edge table) | Enables reasoning and traversal | Medium |
| **P2** | Structured Extraction (memory schemas) | Cleaner API, reliable queries | Medium |
| **P2** | Importance Scoring (multi-factor) | Better ranking than 2-factor | Low |
| **P3** | sqlite-vec (replace ChromaDB) | Simplified deployment | Medium |
| **P3** | Memory Versioning (edit history) | Debugging + "you changed from X to Y" | Low |
| **P3** | Deterministic Consolidation | Free, fast background processing | Low |

---

## The One Recommendation That Matters Most

If I had to pick one: **Context Window Budget**.

Every other improvement is incremental. Token-aware context injection is transformational. It's the difference between:

```
[5,000 tokens of all memories, unsorted] → "The user has mentioned 127 things..."
```

and:

```
[1,200 tokens of relevant, recent, high-confidence memories] → "The user prefers dark mode, 
works in PST, and is currently frustrated with their email inbox."
```

This is what makes an agent feel *intelligent* vs. *searchy*. The agent doesn't need to know everything the user ever said — it needs the right 10 things at the right time.

The implementation is straightforward:
1. Calculate token count for each memory (rough: `len(trigger + action) / 4`)
2. Sort by relevance to current query, then confidence, then recency
3. Fill from top until budget is exhausted
4. Always include "protected" memories (explicit preferences, active corrections)
5. Return the formatted string

This directly reduces LLM cost (fewer input tokens), improves response quality (less noise), and makes the Flutter app feel snappier (less context to process).