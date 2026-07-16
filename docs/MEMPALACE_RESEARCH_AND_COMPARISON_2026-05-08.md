# MemPalace Research & EA Retrieval Comparison

## 1. How MemPalace Achieves High LongMemEval Scores

### Core Architecture

MemPalace's breakthrough finding is counterintuitive: **raw verbatim text + good embeddings beats LLM-based fact extraction**. No LLM decides what to remember. No summarization. No fact extraction. Every conversation message is stored verbatim in ChromaDB and retrieved by cosine similarity.

| Metric | Value | Method |
|--------|-------|--------|
| R@5 (raw) | **96.6%** | Verbatim ChromaDB + cosine similarity, no LLM |
| R@5 (v4 held-out) | **98.4%** | +keyword overlap, temporal boost, preference regex |
| R@5 (v4+Haiku tuned) | **100%** | +LLM rerank on 3 tuned questions |

### Progression: How They Got to 100%

| Stage | R@5 | Technique Added |
|-------|-----|----------------|
| Raw ChromaDB | 96.6% | Baseline — verbatim, cosine similarity |
| Hybrid v1 | 97.8% | Keyword overlap: `fused = embedding * (1 + kw_weight * overlap)` |
| Hybrid v2 | 98.4% | Temporal boost: sessions near reference date get up to 40% distance reduction |
| Hybrid v2 + Haiku | 98.8% | LLM rerank top-K for relevance (~$0.001/query) |
| Hybrid v3 + Haiku | 99.4% | 16 regex preference patterns → synthetic docs at index time |
| Hybrid v4 + Haiku/Sonnet | 100% | Quoted phrase boost (60%), person name boost (40%), nostalgia patterns |

### Key Techniques

1. **Keyword overlap scoring** — exact query terms get a mild multiplicative boost over embedding similarity. Rescues vocabulary-mismatch cases.

2. **Temporal proximity boost** — sessions temporally near the question's reference date get reduced distance. Breaks ties for time-anchored questions.

3. **Preference regex extraction (16 patterns)** — creates synthetic documents like "User has mentioned: prefers PostgreSQL" from phrases like "I find Postgres more reliable". Bridges vocabulary gap between query words and natural conversation.

4. **Person name boost** — capitalized proper noun matches get 40% distance reduction.

5. **Quoted phrase boost** — exact phrases in single-quotes get 60% distance reduction.

6. **LLM rerank (Haiku/Sonnet)** — optional post-retrieval relevance re-rank.

7. **Held-out validation** — 98.4% R@5 on 450 unseen questions, proving fixes generalize.

### The Palace Structure (Conceptual Organization)

- **Wings**: people/projects (top-level)
- **Rooms**: specific topics within wings
- **Halls**: conceptual categories (facts, events, discoveries, preferences, advice)
- **Drawers**: the actual verbatim text chunks
- **Tunnels**: cross-wing connections via shared room names

Query-time wing/room scoping acts as metadata filtering — narrow search to the right scope before vector scoring.

---

## 2. Assistant's LongMemEval Benchmark Infrastructure

### Two Evaluation Systems

| Aspect | `tests/evaluation/evaluate.py` | `tests/benchmarks/longmemeval/eval.py` |
|--------|-------------------------------|----------------------------------------|
| What it tests | Agent style/personality adaptation | Long-term factual recall |
| Prior context | None — queries fire cold | 40+ historical sessions loaded |
| Ground truth | None — checks response is non-empty | Exact answers scored by GPT-4o |
| Scoring | `accuracy = 1.0 if success else 0.0` (line 133) | Semantic equivalence vs ground truth |
| Memory involvement | Zero | Relies on memory tools + fact store |

### Benchmark Modes (`tests/benchmarks/longmemeval/eval.py`)

```
--mode retrieval_only    # R@5/R@10 retrieval recall (comparable to MemPalace)
--mode qa_only           # QA accuracy via HTTP server
--mode qa_direct         # QA via in-process AgentLoop (no HTTP server)
--mode both              # Both retrieval + QA
```

**Status: Never run.** Zero result files exist in `data/benchmarks/results/`.

### `retrieval_only` Path (`evaluate_retrieval()`, line 133)

1. Creates an isolated `LongMemEvalAdapter` per question (isolated HybridDB at `data/benchmarks/longmemeval/users/`)
2. Injects all haystack sessions with batch embeddings (`all-MiniLM-L6-v2`, 384-dim)
3. Uses hybrid search (ChromaDB vector + FTS5 keyword) to retrieve top-10
4. Extracts `session_id` from each hit's ChromaDB metadata
5. Checks if any answer session appears in top-5 (R@5) and top-10 (R@10)
6. Has a brute-force fallback (lines 175-188) when no session IDs come back from metadata

### Embedding Model

Both EA and MemPalace use `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions).

---

## 3. Index Granularity: Per-Message vs Per-Session

### Assistant's Production Design: Per-Message Indexing

Every individual message turn is a separate ChromaDB document. This is **not** a benchmark quirk — it's the production design:

**Production path** (`MessageStore.add_message()` at `src/storage/messages.py:97`):
```
add_message(role, content)
  → db.insert("messages", {...}, sync=True)           # SQLite row
    → INSERT INTO _journal (op='add', data=content)    # journal entry
    → _process_journal()                               # immediate flush
      → _get_embedding(doc)                            # embed content
      → chroma.upsert(ids=[row_id], embeddings=[x], documents=[content])
```
Result: **1 message = 1 SQLite row + 1 ChromaDB document.**

**Benchmark path** (`LongMemEvalAdapter.inject_sessions()` at `adapter.py:79`):
```python
for session in sessions:
    for turn in session:              # ← per turn
        texts.append(turn["content"])
        metas.append({"session_id": sid, ...})

embeddings = get_batch_embeddings(texts)
collection.add(ids=[...], embeddings=embeddings, documents=texts)
```
Result: **N turns = N ChromaDB documents.** Same net architecture.

### MemPalace: Per-Session Indexing

MemPalace embeds **entire sessions** as single documents. A session with 12 turns = 1 ChromaDB document.

### Impact on R@5

**Assistant's per-message indexing causes a structural problem for R@5 calculation:**

If session "session_42" has 20 turns, the hybrid search top-10 could return 8 messages from `session_42` and only 2 other sessions. The `set()` around `retrieved_ids` at `eval.py:169` deduplicates by session, but the top-10 slots were already consumed — other sessions never got a chance to be retrieved.

For multi-session questions that require hits across 3 different sessions, this structurally caps R@5 below what a session-level embedding would achieve.

The `search_hybrid()` at `messages.py:174` has no session-level aggregation — it returns individual message rows with scores.

---

## 4. Feature Comparison

### Shared

| Component | EA | MemPalace |
|-----------|-----|-----------|
| Embedding model | `all-MiniLM-L6-v2` (384-dim) | `all-MiniLM-L6-v2` (384-dim) |
| Vector store | ChromaDB (via HybridDB) | ChromaDB |
| Storage format | Verbatim text | Verbatim text |

### What MemPalace Has That EA Doesn't

| Technique | Impact | Complexity |
|-----------|--------|------------|
| Keyword overlap multiplier (`fused = emb * (1 + kw * overlap)`) | +1.2% R@5 | Small |
| Temporal boost (sessions near reference date) | +0.6% R@5 | Medium |
| Preference regex (16 patterns → synthetic docs) | +0.6% R@5 | Medium |
| Person name boost (capitalized proper nouns, 40%) | Edge cases | Small |
| Quoted phrase boost (exact phrase match, 60%) | Edge cases | Small |
| Nostalgia patterns ("I used to X", "growing up X") | Edge cases | Small |
| LLM rerank (Haiku, ~$0.001/query) | +0.4-1% R@5 | Small |
| Held-out validation split (450q) | Publishing metric | Small |
| Session-level indexing | Structural advantage | Medium |

### What EA Has That MemPalace Doesn't

| Feature | Notes |
|---------|-------|
| FTS5 full-text search | Built into HybridDB — exact word matches integrated |
| Recency scoring via timestamps | HybridDB recency weight parameter |
| Production agent integration | MemoryMiddleware + `memory_search` tool live in loop |
| Memory graph | Connections, contradictions between memories |
| Cross-modal storage | SQLite FTS + ChromaDB, not ChromaDB-only |
| `qa_direct` mode | Run AgentLoop in-process without HTTP server |

---

## 5. Critical Gaps Likely Lowering Assistant's Score

### G1: Temporal Blindness

Assistant's `recency_weight=0.3` blends recency linearly into the search score. It doesn't know the question's temporal context. "What did I do last month?" gets the same search as "What did I do yesterday?". MemPalace boosts sessions near the question's reference date.

**Fix**: Extract temporal cues from the question (dates, "last week", "yesterday") and use them to weight session timestamps in retrieval.

### G2: Per-Message Flooding

Top-10 can be consumed by messages from a single large session. The `set()` dedup at `eval.py:169` prevents double-counting but can't recover slots already spent on one session.

**Fix**: Implement session-level dedup *during* retrieval (limit to 1 result per session_id), or aggregate at session level.

### G3: Trigger+Domain Dedup Bug (from FACT_EXTRACTION_IMPROVEMENTS doc)

At `src/sdk/middleware_memory.py:787-790`, facts sharing the same `(trigger, domain)` pair are silently dropped regardless of differing `attribute`/`value`.

### G4: Scalar-Only Extraction (from FACT_EXTRACTION_IMPROVEMENTS doc)

The `EXTRACTION_PROMPT` has only scalar examples. `_store_patterns()` at line 795 casts all values via `str()`, making list-valued facts unparseable.

### G5: No Preference Bridging

"What does the user prefer for databases?" won't match "I find Postgres more reliable" via pure embedding/FTS5.

### G6: No Named Entity Weighting

Sentence transformers under-weight person names. MemPalace's person name boost fixes this.

---

## 6. Estimated R@5 Trajectory

```
Current (estimated, never measured):    ~85-90%   (raw per-message hybrid + metadata fallback)
├── Fix: session-level dedup             → ~92%    (stops flooding, proper retrieval)
├── Add: keyword overlap multiplier      → ~94%    
├── Add: temporal context boost          → ~94.5%  
├── Add: preference regex extraction     → ~95%    
├── Add: LLM rerank (Haiku)             → ~95.5%  
└── Add: person name + quoted phrase     → ~96%    (matches raw MemPalace)
```

The 96.6% raw MemPalace number is the target. EA can match it by fixing the structural per-message flooding issue and adding 2-3 lightweight retrieval techniques. Getting to 98-100% requires the full suite plus LLM rerank — and careful validation to distinguish genuine improvement from overfitting.

---

## 7. Implementation Priority

| Priority | Task | Location | Effort | Expected Gain |
|----------|------|----------|--------|---------------|
| **P0** | Run the benchmark first — get a real baseline | `uv run python tests/benchmarks/longmemeval/eval.py --mode retrieval_only` | Immediate | Foundation |
| **P0** | Session-level dedup in retrieval | `search_with_session_ids()` or `search_hybrid()` | Small | +3-5% R@5 |
| **P0** | Fix trigger+domain dedup | `middleware_memory.py:787-790` | Tiny | Stops data loss |
| **P1** | Keyword overlap multiplier | `HybridDB.search_hybrid()` or post-processing | Small | +1-2% R@5 |
| **P1** | Temporal context extraction from questions | Retrieval layer | Medium | +0.5-1% R@5 |
| **P1** | Fix list-valued extraction | `middleware_memory.py` prompt + `_store_patterns()` | Medium | Enables counting Qs |
| **P2** | Preference regex extraction (16 patterns) | `LongMemEvalAdapter.inject_sessions()` | Medium | +0.5-1% R@5 |
| **P2** | LLM rerank (Haiku) | Post-retrieval pipeline | Small | +0.5-1% R@5 |
| **P3** | Person name + quoted phrase boost | Retrieval post-processing | Small | Edge cases ~0.5% |
| **P3** | Held-out validation split | Benchmark harness | Small | Clean publishing |

---

## 8. Why This Matters for EA

Assistant's current memory pipeline does LLM fact extraction → `upsert_fact_memory`. MemPalace's research shows this is the wrong direction — you lose raw conversational context in exchange for lossy extracts that miss relationships, alternatives considered, and tradeoffs discussed.

The most actionable finding: **96.6% R@5 is achievable with nothing but raw message storage + ChromaDB + all-MiniLM-L6-v2**. EA already has all three. The gap is that EA routes through lossy LLM extraction instead of searching raw messages directly for memory queries.

Assistant's `memory_search` tool already does raw message search — the fact extraction middleware is actively discarding information (via the dedup bug, scalar-only extraction, and missing historical extraction). The `mempalace` extraction mode in `evaluate_qa_direct()` (line 534) is already wired up and just needs the `LME_EXTRACTION_MODE=mempalace` env var to test verbatim-only retrieval.

**The path to 96%+ is shorter than it looks** — the infrastructure exists, it just needs 3-4 focused fixes and the benchmark to actually be run.
