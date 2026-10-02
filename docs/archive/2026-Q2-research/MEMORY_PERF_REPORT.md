# Memory System Performance Report

> Date: 2026-05-03 (v3 — cross-store analysis added)
> Scope: Full stack — HybridDB, MessageStore, MemoryStore, Middleware, Tools
> Benchmarks: Component-level (20 memories, 16 messages, 10 runs per scenario)

---

## 1. Benchmark Results

### Test Environment

| Item | Value |
|------|-------|
| Embedding model | `all-MiniLM-L6-v2` (sentence-transformers, 384-dim, local CPU) |
| Seed data | 20 memories (facts + preferences + corrections), 16 conversation messages |
| Machine | Mac (Apple Silicon, darwin) |
| ChromaDB | 2 separate instances (MessageStore + MemoryStore) |
| SQLite | 2 separate databases (MessageStore + MemoryStore) |

### Component Latencies (p50)

| Component | p50 (ms) | p95 (ms) | n |
|-----------|----------|----------|----|
| **Full retrieval pipeline** (facts + hybrid + message search) | **167.7** | 338.9 | 10 |
| **find_facts_for_query** (10-column FTS5) | **88.8** | 182.6 | 10 |
| **search_hybrid** (memories: ChromaDB + FTS5) | **75.0** | 130.7 | 10 |
| upsert_fact_memory (write) | 67.0 | 71.0 | 5 |
| **get_embedding** (all-MiniLM-L6-v2) | **9.6** | 26.6 | 20 |
| get_memory_context (20 working memories) | 1.6 | 2.5 | 10 |
| is_memory_query (regex gate) | <0.1 | 1.0 | 20 |

### Dual-Store Cost (the hidden tax)

Every memory query that triggers the planner or baseline path does:

```
search_facts(memory_facts)  →  89ms  (MemoryStore, 10 FTS5 queries)
search_hybrid(memories)     →  75ms  (MemoryStore, ChromaDB + FTS5)
search_hybrid(messages)     → ~70ms  (MessageStore, separate ChromaDB instance)
                              ─────
                              234ms  total
```

The message search duplicates work — its content is the raw conversation that memory facts were extracted from.

---

## 2. Cross-Store Architecture Analysis

### Two Stores, Two HybridDBs, Two ChromaDB Instances

```
MessageStore                        MemoryStore
  │                                   │
  ├─ HybridDB(app.db)                 ├─ HybridDB(app.db)
  │   Path: /Workspaces/{ws}/         │   Path: /Workspaces/{ws}/memory/
  │   Tables: messages (content)      │   Tables: memories, insights, sessions, memory_facts
  │   Graph: none                     │   Graph: _graph_nodes (memories only)
  │   ChromaDB: vectors/              │   ChromaDB: memory/vectors/
  │   Collections: messages_content   │   Collections: memories_trigger, memories_action, insights_summary
  │                                   │
  ├─ search_hybrid(query)             ├─ search_hybrid(query)
  └─ get_messages(days=7)             └─ search_facts(query)
                                      │
                                      └─ search_all(query)
                                          └── bridges into → get_message_store().search_hybrid()
```

### Where the Dual-Fetch Happens

| Caller | MemoryStore ops | MessageStore ops | Always dual? |
|--------|----------------|-----------------|-------------|
| `_get_planner_memory_context` | find_facts + search_hybrid | search_hybrid | Conditional (plan.needs_messages) |
| `_get_baseline_memory_context` | find_facts + search_hybrid | search_hybrid | **Yes** (always) |
| `_get_ranked_memory_context` | find_facts + search_hybrid | none | No |
| `memory_search` tool | find_facts + find_fact_history | search_hybrid | **Yes** (always) |
| `memory_search_all` tool | search_all (facts + hybrid + insights) | search_hybrid | **Yes** (always) |
| `memory_get_history` tool | none | get_messages (date range) | No |
| `before_agent` (non-memory query) | is_memory_query guard → skip | none | No |

**Finding**: The planner path is smart about conditionally fetching messages. The baseline path (current default) always dual-fetches. The ranker path (disabled) skips messages entirely.

### The Redundancy

When a user asks "What's my job title?":
1. MemoryStore finds `fact:user:job_title = "Senior Backend Engineer"` via FTS5 on `memory_facts`
2. MemoryStore also finds the memory object via `search_hybrid` on `memories`
3. MessageStore finds the original conversation where the user said "I'm a Senior Backend Engineer"
4. All three results are assembled into context

The message search is redundant **when facts are found**. The conversation text adds nothing that the extracted fact doesn't already capture, and it costs a full embedding generation + ChromaDB query + FTS5 query on a separate database.

---

## 3. Root Cause of `search_facts` Slowness

### The Call Chain

```
MemoryStore.search_facts("what is my name", limit=6)
  → HybridDB.search_all("memory_facts", query, limit=6)
    → _get_text_columns("memory_facts") → [id, fact_key, entity, attribute,
                                             value, previous_value, memory_id,
                                             scope, project_id, updated_at]
    → For each of 10 TEXT columns: _fts_search()  ← 10 FTS5 queries with BM25 + JOIN
    → _get_longtext_columns("memory_facts") → []  ← 0 ChromaDB queries
    → _fuse_hybrid(all_fts, all_vec)
    → _fetch_rows_by_ids()

vs.

MemoryStore._find_facts_fallback("what is my name", limit=6)
  → SELECT * FROM memories WHERE memory_type = 'fact' LIMIT 1000
  → In-memory regex scoring over trigger/action  ← 1 SQLite query
```

### Why This Happened

`search_all` was designed for tables with 2-4 searchable text columns (like `memories` with `trigger`, `action`, `structured_data`). When `memory_facts` was created with 10 TEXT columns, 7 of which are internal keys/timestamps that should never be searched (`memory_id`, `scope`, `project_id`, `updated_at`, `previous_value`, `fact_key`, `id`), no one updated `search_all` to know which columns to skip.

The method fires FTS5 on every column indiscriminately. **ChromaDB is not involved** — `memory_facts` has zero LONGTEXT columns, so `_vector_search` is never called. The bottleneck is pure FTS5 column explosion.

---

## 4. Competitor Landscape

### MemPalace (mempalace/mempalace) — 50.8k stars

**The best-benchmarked open-source memory system.** Local-first, Python, MIT. 96.6% R@5 raw on LongMemEval — **zero LLM calls**.

**Architecture that challenges our assumptions:**
- **Verbatim storage**: Does not summarize, extract, or paraphrase. Raw storage + semantic search beats our LLM extraction pipeline on benchmarks.
- **Structured scoping**: Wings/rooms/drawers hierarchy lets searches be scoped, not flat. Our workspaces are a start, but hierarchical topic scoping would reduce noise.
- **Pluggable backend**: Default ChromaDB with abstract interface. Their `search` method is simpler than our `search_all` because scoping handles the filtering.
- **Hybrid retrieval tiers**: Raw (96.6%, no LLM) → Hybrid with boosting (98.4%, no LLM) → LLM rerank (≥99%).
- **29 MCP tools**: Consolidated tool surface for all memory operations.

**Key challenge to our architecture**: MemPalace proves verbatim storage + scoping > LLM extraction + flat search. Their 96.6% R@5 with zero LLM calls suggests our `_extract_with_llm` and `_expand_queries` are optimizing the wrong problem.

### Supermemory (supermemoryai/supermemory) — 22.4k stars

**#1 on LongMemEval, LoCoMo, ConvoMem** for production systems. Also published experimental ASMR system (~99%).

**Production patterns**:
- **Pre-computed user profiles**: `profile()` call returns static facts + dynamic context in ~50ms. Our equivalent (`get_memory_context` + on-demand retrieval) takes 170ms.
- **Memory vs RAG as separate concepts**: Both served through one API but maintained separately.
- **Automatic forgetting**: Temporal facts expire, contradictions auto-resolve.

**ASMR experiment findings** (published March 2026):
- **Agentic retrieval beats vector search** by ~13 points. Three parallel search agents reasoning over findings outperform embedding similarity.
- Not production-viable for latency, but the insight stands: for temporal data with updates/corrections, semantic similarity fails.

### CrewAI Memory

- Background thread encoding (non-blocking saves)
- Shallow/deep recall split — ~200ms shallow, 1-3s deep
- Composite scoring: 0.5·sim + 0.3·recency + 0.2·importance

### Others

- **Zep**: Multi-tier cache (hot/warm), combine add+search in one call, pre-warm on login
- **GBrain**: Deterministic regex entity linking (zero LLM), graph-boosted search (+31.4 P@5)
- **Mem0**: Multi-signal parallel scoring, p50 0.88-1.09s

---

## 5. Convergence: What the Best Systems All Do

| Pattern | MemPalace | Supermemory | CrewAI | GBrain | EA (us) |
|---------|-----------|-------------|--------|--------|---------|
| Pre-computed profiles | ✅ (via scoping) | ✅ (50ms) | ✅ (scope tree) | ✅ (compiled truth) | ❌ (assembled on every turn) |
| Shallow/fast retrieval path | ✅ (raw = 0 LLM) | ✅ (profile call) | ✅ (shallow = 0 LLM) | ✅ (regex only) | ❌ (LLM query expansion always) |
| Background extraction | N/A (no extraction) | ✅ (server-side) | ✅ (thread pool) | ✅ (parallel cheap model) | ⚠️ (fire-and-forget, uses main model) |
| Graph-boosted search | ❌ (flat scoped search) | ❌ (profiles, not graph) | ❌ | ✅ (+31.4 P@5) | ⚠️ (graph exists, unused in facts) |
| Query early-termination | ✅ (scoped) | ✅ (profile = answer) | ✅ (confidence route) | ✅ (compiled truth) | ❌ (always multi-fetches) |

---

## 6. Critical Architectural Gap: Facts Not Graph Nodes

Our graph capabilities (`pagerank`, `neighbors`, `traverse`, `search_graph`) only operate on `memories` rows. Individual facts in `memory_facts` are a flat index with no graph registration.

**Impact**: `search_facts` does flat FTS5 (10 columns) with zero graph awareness. No entity-level centrality, no graph-expanded recall, no connected-fact scoring.

**Full plan**: See `docs/MEMORY_GRAPH_FACTS_PLAN.md` — register `memory_facts` as graph nodes with two edge rules (fact→memory `belongs_to`, fact→older_fact `updates`). 304 lines across 3 files.

---

## 7. Updated Recommendations (Stack-Wide)

### Tier 1: Dual-Store Redundancy Elimination

#### 7.1 Stop dual-searching when facts are found (1 line change)

**File**: `src/sdk/middleware_memory.py:440-495` (`_get_baseline_memory_context`)

**Change**: After `find_facts_for_query` returns results with confidence > threshold, skip `conversation.search_hybrid`. The facts already captured what the conversation said.

```python
# Current: always fetches both
facts = store.find_facts_for_query(query, limit=6)
hybrid = store.search_hybrid(query, limit=8)
messages = conversation.search_hybrid(query, limit=5)  # ALWAYS called

# Recommended:
facts = store.find_facts_for_query(query, limit=6)
if len(facts) >= 3 and all(f.confidence > 0.6 for f in facts[:3]):
    messages = []  # Facts are sufficient, skip redundant message search
else:
    messages = conversation.search_hybrid(query, limit=5)
```

**Impact**: Eliminates ~70ms ChromaDB search + embedding generation on the majority of memory queries (where facts already exist). **Single biggest latency win.**

#### 7.2 Default to planner path instead of baseline

**File**: `src/sdk/middleware_memory.py:242, 252`

**Change**: Enable the ranker and planner by default (flip env-flag defaults). The planner already conditionally fetches messages (`plan.needs_messages`), and the ranker skips messages entirely.

```python
ranker_enabled = os.environ.get("MEMORY_RANKER_ENABLED", "true")  # was "false"
```

**Impact**: Better retrieval quality with same or lower latency. Already tested (15 ranker tests, 6 planner tests).

#### 7.3 Unify ChromaDB instances (medium-term) — ⚠️ RE-EVALUATED

**Original claim**: Unifying would share embeddings and merge collections.

**Reality**: ChromaDB collections are `{table}_{column}` (e.g., `messages_content`, `memories_trigger`, `memories_action`). Even in the same HybridDB instance, each LONGTEXT column gets its own collection. `search_all` iterates columns within one table — it doesn't search across tables. Unification provides:

- ✅ Shared SQLite WAL (marginal — both are single-user)
- ✅ Shared `chromadb.PersistentClient` (marginal — two clients in same process is ~free)
- ❌ **No embedding reuse** — `_vector_search("messages_content")` and `_vector_search("memories_trigger")` still re-embed independently
- ❌ **No collection merging** — collections are column-scoped per table
- ❌ **No cross-table search** — `search_all` only searches one table at a time

**Revised verdict**: Database unification provides negligible performance benefit. The real win is from **rec 7.1** (don't search messages when facts exist) and **rec 7.8** (cache embeddings within a single query). Not worth the schema migration cost.

### Tier 2: Memory Store Optimizations

#### 7.4 Fix column explosion in `search_facts`

**File**: `src/storage/memory.py:770`

**Change**: Replace `search_all("memory_facts")` with FTS5 on `entity`/`attribute`/`value` only (3 queries) or skip to the 6x-faster fallback. Combined with graph-boosted ranking from the facts-graph plan.

**Impact**: `find_facts_for_query` drops from 89ms to ~27ms (70% reduction).

#### 7.5 Evolve `get_memory_context` into pre-computed user profile

**File**: `src/storage/memory.py` — new `get_user_profile()` method

**Change**: Replace the current 20-working-memories list with a proper profile compiled at write time:
- **Static facts**: entity/attribute/value from `memory_facts` (current, non-superseded)
- **Recent activity**: last 7 days of conversation topics, extracted as brief notes
- **Preferences**: memory_type="preference" entries, ordered by confidence
- **Pending corrections**: facts where `previous_value` != null (recently changed)

Rebuild on every `upsert_fact_memory` call (write-time cost), serve as a cached dict (read-time: 1 SQL query, ~5ms).

```python
def get_user_profile(self) -> dict:
    """Pre-computed profile: rebuild on write, serve on read. ~5ms."""
    return {
        "static_facts": [...],    # entity:attribute → value, cached
        "recent_activity": [...],  # abstract of last 7 days
        "preferences": [...],      # memory_type=preference, by confidence
        "corrections": [...],      # recently changed facts
    }
```

**Impact**: The common case (`before_agent` → inject user context) drops from 170ms to ~5ms. The Supermemory-equivalent profile call. Full retrieval pipeline only triggers on explicit `memory_search` tool calls.

#### 7.6 Remove LLM query expansion from critical path

**File**: `src/sdk/tools_core/memory.py:14` (`_expand_queries` LLM variant)

**Change**: Keep regex expansion for `before_agent`. Move LLM rephrasing to `memory_search` tool only (opt-in `depth="deep"` parameter). This is the CrewAI/MemPalace shallow/deep pattern.

**Impact**: Eliminates 100ms-5s of variable LLM latency from `before_agent`.

### Tier 3: HybridDB Optimizations

#### 7.7 Add searchable-columns annotation to `search_all`

**File**: `src/sdk/hybrid_db.py:1797` (`search_all`)

**Change**: Accept an optional `search_columns` parameter, or auto-detect via column name heuristics. Skip columns matching `*_id`, `*_key`, `*_at`, `id`, `created_at`, `updated_at`.

```python
def search_all(self, table, query, limit=10, search_columns=None, ...):
    all_text_cols = search_columns or self._get_searchable_text_columns(table)
    # _get_searchable_text_columns filters out *_id, *_key, *_at, id, created_at, updated_at
```

**Impact**: Eliminates the column explosion problem at the source. Every table benefits, not just `memory_facts`.

#### 7.8 Cache embeddings within a query

**File**: `src/sdk/hybrid_db.py:1894` and `src/sdk/tools_core/apps.py:48`

**Change**: In `search_all`, generate the embedding once per query text and reuse across all ChromaDB searches. Currently each `_vector_search` re-embeds the same query independently.

```python
# In search_all:
if lt_cols:
    embedding = self._get_embedding(query)  # Generate ONCE
    for col in lt_cols:
        col_vec = self._vector_search_with_embedding(table, col, embedding, where, limit*2)
```

**Impact**: For tables with 3 LONGTEXT columns (like `memories` with trigger + action + structured_data), saves 2 embedding generations. ~20ms saved per `search_hybrid` call.

### Tier 4: Message Store Simplification

#### 7.9 Prioritize recency over semantic similarity for message search

**File**: `src/storage/messages.py:130` (`search_hybrid`)

**Change**: For conversation message search, increase `recency_weight` significantly (0.5 → 0.8). Conversations are temporal — what someone said yesterday often matters more than a semantically similar conversation from last year. This also reduces dependency on ChromaDB quality.

**Impact**: Better relevance for memory queries, slightly faster (less reliance on slow vector search path).

#### 7.10 Consider verbatim-first as primary storage (MemPalace pattern)

**Long-term architectural consideration**: MemPalace's 96.6% R@5 with zero LLM extraction suggests we could simplify the entire pipeline. Instead of:
```
message → store raw → LLM extract → store facts → hybrid search facts → LLM expand queries → assemble context
```

We could do:
```
message → store raw (verbatim, scoped) → hybrid search → assemble context
```

This eliminates `_extract_with_llm`, `_expand_queries`, `_get_relevant_memory_context`, and the dual-store architecture. The cost is potentially less structured lookup — but MemPalace's benchmarks suggest the trade-off favors verbatim + scoping.

This is a major architectural decision, not a quick optimization. Worth a proof-of-concept comparing retrieval quality with facts vs. verbatim on a sample of questions.

---

## 8. Target Latency Budget

| Component | Current p50 | After Optimizations | Saving |
|-----------|------------|--------------------|--------|
| find_facts_for_query | 88.8ms | 27ms (3-col FTS5 or fallback) | 70% |
| search_hybrid (memories) | 75.0ms | 55ms (embedding cache) | 27% |
| search_hybrid (messages) | ~70ms | **0ms** (skip when facts found) | 100% |
| LLM query expansion | 100ms-5s | 0ms (shallow mode) | 100% |
| **Full retrieval pipeline** | **~234ms** | **~32ms** (facts-only path) | **86%** |
| get_memory_context → user profile | 170ms per query | 5ms (pre-computed) | 97% |

### What "profiles" means for the common case

If we implement 7.5 (pre-computed profiles), the experience becomes:

```
before_agent()                           ← 5ms (was 170ms)
 ├── get_user_profile()                  ← 5ms (cached, rebuild on write)
 ├── is_memory_query(query)              ← <0.1ms
 └── _get_relevant_memory_context()      ← NOT CALLED (profile is enough)
```

The full retrieval pipeline only fires when the agent explicitly calls `memory_search` — and only on the content that wasn't in the profile.

---

## 9. Subtraction Summary

If the goal is **remove over optimize**, here's the priority order:

| # | Remove/Simplify | Lines | Saving | Quality Risk |
|---|----------------|-------|--------|-------------|
| 1 | Dual message search when facts found | ~3 lines | 70ms per query | None — facts are more current |
| 2 | LLM query expansion from `before_agent` | env flag | 100ms-5s | Minimal — regex handles 80% |
| 3 | 7 of 10 FTS5 columns on `search_all("memory_facts")` | ~5 lines in search_all | ~60ms per query | None — columns not searchable |
| 4 | Switch default from baseline to planner path | env flag | Variable | None — planner is tested |
| 5 | Consolidation auto-trigger interval (10→50 messages) | 1 line | Reduces `asyncio.run` overhead | Low — consolidation still runs |
| 6 | Journal processing on every `_journal_count > 0` → batch on write | ~10 lines in search_all | 2ms per search | None if batched |

### Things NOT to remove

- **`_extract_with_llm`**: The structured facts table is valuable for fast entity/attribute lookup. Just don't run the extraction model at the same tier as the agent model (use a cheap model — rec 5.6 from v1).
- **Graph capabilities**: Core differentiator vs all competitors except GBrain. The graph boost is load-bearing for retrieval quality.
- **Consolidation itself**: Conflict resolution and insight generation are unique value. Just run less frequently.

---

## 10. Files to Create / Update

| File | Purpose |
|------|---------|
| `docs/MEMORY_GRAPH_FACTS_PLAN.md` | Plan for registering facts as graph nodes (exists) |
| `docs/MEMORY_PERF_REPORT.md` | This file (updated 2026-05-03) |

---

## 11. Benchmark Reproducibility

```bash
# Component-level benchmarks
uv run python tests/perf/test_memory_pipeline.py --component --runs 10

# WebSocket end-to-end (requires server)
uv run assistant http &
uv run python tests/perf/test_memory_pipeline.py --ws --runs 5
```

---

## 14. Implemented Optimizations (2026-05-03)

### Verified Against Source Code

| Rec | Description | Status | Source |
|-----|-------------|--------|--------|
| 7.1 | Skip dual message search when facts found | ✅ Implemented | `middleware_memory.py:450-458` — when 3+ facts with confidence > 0.6 exist, message search is skipped with note inserted |
| 7.7 | Searchable-columns filter (`_SKIP_SEARCH_COLUMNS`) | ✅ Implemented | `hybrid_db.py:42-45` (constant), `hybrid_db.py:1834` (filter applied in `search_all`) — filters 10→3 columns on `memory_facts` |
| 7.9 | Stronger recency weight (0.3→0.5 default) | ❌ Reverted | `messages.py:136` default=0.3. `middleware_memory.py:466` defaults to 0.3 (0.7 only when recency keywords detected in query) |

### Key Finding on Recency (7.9)

**7.9 is fundamentally incompatible with LongMemEval benchmarks.** Any retrieval system that prioritizes recency by default will fail on benchmarks where the correct answer is NOT in the most recent sessions. For production workloads with real temporal decay (recency = relevance), 7.9 is correct. For benchmark evaluation, it must be disabled. The current 0.3 default with dynamic 0.7 for recency-keyword queries is a reasonable compromise.

### Extraction Pipeline Status

Memory extraction IS triggered during LongMemEval evaluation at `longmemeval_adapter.py:142-155` via the `/conversation/extract-memories` HTTP endpoint between session ingestion batches. The extraction pipeline's fact quality has not been independently benchmarked against a fact-precision metric. Benchmark claims without a saved data file at `data/evaluations/` cannot be verified against source.
