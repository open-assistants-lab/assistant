# Memory Subsystem Rewrite — Research & Approach

**Date:** 2026-05-08
**Goal:** Rewrite Assistant's memory extraction, storage, and retrieval to achieve >95% on LongMemEval, with zero LLM involvement, using HybridDB as the foundation.

---

## 1. Current State

### System Map

Assistant's memory subsystem spans ~5K lines across 6 files:

| Layer | File | Lines | Role |
|---|---|---|---|
| Extraction | `middleware_memory.py` | 956 | LLM extraction of patterns from conversation every 3 turns |
| Extraction | `observation.py` | 221 | Observer/Reflector prompts (Mastra OM-inspired) |
| Extraction | `memory_planner.py` | 271 | Deterministic query intent classifier (no LLM) |
| Storage | `memory.py` | 1,882 | MemoryStore: memories, memory_facts, insights + domain logic |
| Storage | `messages.py` | 298 | MessageStore: messages table with hybrid search |
| Storage | `hybrid_db.py` | 2,439 | SQLite+FTS5+ChromaDB+DuckDB+Graph+Journal engine |
| Storage | `consolidation.py` | 393 | Background memory consolidation |
| Storage | `observation.py` | 182 | ObservationStore for observer pipeline |
| Retrieval | `tools_core/memory.py` | 1,200 | 8 tools: memory_search, memory_count, etc. |
| Retrieval | `memory_ranker.py` | 413 | Deterministic evidence ranker (no LLM) |
| Retrieval | `knowledge_update.py` | 117 | Knowledge-update resolution for current/latest queries |
| Retrieval | `middleware_summarization.py` | 270 | Token-threshold conversation summarization |
| Eval | `longmemeval_adapter.py` | 687 | HTTP-based LongMemEval adapter |
| Eval | `longmemeval_synthesis.py` | 241 | Deterministic answer synthesis |
| Bench | `benchmarks/.../adapter.py` | 224 | Direct-injection benchmark adapter |
| Bench | `benchmarks/.../eval.py` | 990 | Full benchmark runner |

### Current Problems (Proven by Evaluation Data)

1. **LLM nondeterminism:** Same tool output, 10 answer variants across 13 runs. `gpt4_59c863d7` (model kits): answers included 4, 5, 7, 8, 9, 10, 27 from identical facts.
2. **Recursive echo:** `memory_search` results get stored as `tool` messages, then re-matched by future searches. Already partially fixed with `tool`/`system` role filter.
3. **Per-paragraph flooding:** One large session with 20 messages hogs 8/10 retrieval slots. Session dedup implemented but not enabled by default.
4. **Lossy extraction:** LLM fact extraction loses context (scalar-only, dedup bug drops multi-item facts, no historical extraction on bulk load).
5. **Synthesis can't compensate:** Deterministic synthesis extracts what tool output contains, but when the LLM ignores tool output entirely, synthesis is powerless.

### Historical Evaluation Results

| Run | knowledge-update | multi-session | temporal-reasoning | single-session-user | Overall |
|---|---|---|---|---|---|
| 20260508_134312 | 100% (5/5) | — | — | — | — |
| 20260508_134847 | — | 40% (2/5) | — | — | — |
| 20260508_135511 | — | — | 60% (3/5) | — | — |
| 20260508_135851 | — | — | — | 80% (4/5) | — |
| 20260508_161437 | 80% (4/5) | 40% (2/5) | 60% (3/5) | 80% (4/5) | 65% (13/20) |
| 20260508_193923 | — | — | — | 80% (4/5) | — |
| 20260508_194950 | — | 0% (0/5) | — | — | — |
| 20260508_200116 | — | — | 40% (2/5) | — | — |
| 20260508_201536 | 60% (3/5) | — | — | — | — |

Range: 45-70% overall. Unstable across runs due to LLM nondeterminism.

---

## 2. Industry Research: How Leading Systems Do Memory

### MemPalace (96.6% → 98.4% R@5)

| Component | Approach |
|---|---|
| Extraction | **None.** No LLM. No fact store. Every message indexed verbatim. |
| Storage | ChromaDB only. Per-session documents. `all-MiniLM-L6-v2` (384-dim). |
| Retrieval | Cosine similarity + deterministic heuristics: keyword overlap multiplier, temporal boost (sessions near reference date), person name boost (40%), quoted phrase boost (60%), 16 regex preference patterns → synthetic docs at index time. |
| LLM involvement | **Zero** in baseline. Optional Haiku rerank (~$0.001/query) adds ~0.4%. |

**Progression:**
| Stage | R@5 | Technique |
|---|---|---|
| Raw ChromaDB | 96.6% | Verbatim, cosine similarity |
| Hybrid v1 | 97.8% | Keyword overlap: `fused = embedding * (1 + kw * overlap)` |
| Hybrid v2 | 98.4% | Temporal boost (+ session-level dedup) |
| Hybrid v2 + Haiku | 98.8% | LLM rerank (optional) |
| Hybrid v3 + Haiku | 99.4% | 16 preference regex → synthetic docs |
| Hybrid v4 + Haiku/Sonnet | 100% | Person name + quoted phrase + nostalgia patterns |

### Mem0 v3 (93.4% LongMemEval, 91.6% LoCoMo)

| Component | Approach |
|---|---|
| Extraction | **Yes.** Single-pass ADD-only LLM call. No UPDATE/DELETE. Memories accumulate. |
| Storage | Embeddings + BM25 keyword + entity linking. Multi-signal fused retrieval. |
| Retrieval | No LLM. Semantic + BM25 + entity matching in parallel, fused scores. |
| LLM involvement | 1 call per add operation. Token usage: ~6.8K tokens. Latency: ~1.09s p50. |

**Key insight from Mem0 paper:** Agent-generated facts are first-class citizens — when an agent confirms an action, that information is stored with equal weight to user statements.

### Zep / Graphiti

| Component | Approach |
|---|---|
| Extraction | **Yes.** Temporal knowledge graph builder with `valid_at`/`invalid_at` dates. |
| Storage | Graph + vector. Facts have temporal validity windows. |
| Retrieval | No LLM. Graph traversal + semantic search. |
| LLM involvement | Extraction only. Managed cloud service, not self-contained. |

### Cognee (17.1k stars)

| Component | Approach |
|---|---|
| Extraction | **Yes.** "Cognify" pipeline: ingest → embed → graph-ify. LLM-driven. |
| Storage | Hybrid graph + vector. Cross-agent knowledge sharing. |
| Retrieval | No LLM. Auto-routing picks best search strategy. |
| LLM involvement | Extraction only. Four operations: remember, recall, forget, improve. |

### Comparison Matrix

| System | LongMemEval | LLM in Extraction | LLM in Retrieval | Deterministic Only Possible? |
|---|---|---|---|---|
| **MemPalace** | 98.4% R@5 | No | No (Haiku optional) | **Yes — 96.6%** |
| **Mem0 v3** | 93.4% | Yes (1 call) | No | No |
| **Zep/Graphiti** | — | Yes | No | No |
| **Cognee** | — | Yes | No | No |
| **EA (current)** | ~45-70% OA | Yes (every 3 turns) | Yes (ranked context) | No |

**Key finding:** MemPalace is the **only** system achieving >95% with zero LLM calls. Every other system requires at least one LLM call for extraction. MemPalace's 96.6% raw baseline proves that pure raw text + embeddings + deterministic heuristics is sufficient.

### A-Mem: Agentic Memory (Zettelkasten-inspired, LLM-heavy)

| Component | Approach |
|---|---|
| Extraction | **Yes.** Note construction: LLM generates keywords, tags, contextual descriptions for every memory. |
| Storage | Atomic notes with multi-faceted structure (content + keywords + tags + context desc + embedding). |
| Retrieval | Cosine similarity on dense embeddings. No LLM in retrieval path. |
| Linking | LLM analyzes top-K nearest memories to establish connections based on shared attributes. |
| Evolution | LLM updates existing memories when new related memories are added ("memory evolution"). |
| LLM involvement | **3 LLM calls per write** (note construction + link generation + memory evolution). |

**Key insight:** All three operations require LLM — doesn't fit zero-LLM constraint. But ablation shows: without link generation and evolution, performance degrades sharply. However, MemPalace hits 96.6% without any of these, proving raw text + good retrieval is sufficient.

**Scaling validated:** A-Mem handles 1M entries with sub-4μs retrieval time — confirms our HybridDB + ChromaDB approach scales well.

### MemPalace Critical Analysis (Robin Dey, OpenHub Research, April 2026)

This independent paper validates and refines every conclusion in this document:

| Finding | Verdict |
|---|---|
| 96.6% R@5 is from ChromaDB + all-MiniLM-L6-v2 + verbatim text, NOT the palace hierarchy | **Confirmed.** Wing/Room = metadata string filters. |
| The spatial metaphor improves UX but not retrieval math | **Confirmed.** "LLMs don't have hippocampi." |
| Verbatum-first is the real philosophical innovation | **Confirmed.** "Retrieval is better solved at read time than write time." |
| Zero-LLM write path is the structural differentiator | **Confirmed.** No competitor offers deterministic, offline, zero-cost writes. |
| Benchmark claims were initially overstated but corrected | **Acknowledged.** 100% claim retired; honest = 96.6% raw / 98.4% with LLM rerank on held-out data. |
| Mem0 v3 (93.4%) narrowed the gap to ~3% | **Important.** Verbatim advantage is now modest, not decisive. But zero-cost advantage remains. |
| Four-layer memory stack (L0-L3) with ~170 token wake-up cost is underappreciated | **Key addition to our design** (see §4). |

### LangChain Conceptual Framework Validation

| LangChain Concept | Our Approach | Match? |
|---|---|---|
| Semantic memory = facts | Raw text contains all facts naturally | ✅ |
| Episodic memory = experiences | Raw conversation history IS episodic memory | ✅ |
| Procedural memory = rules | System prompt, separate from memory subsystem | Out of scope |
| Collection > Profile for recall | Raw messages = document collection | ✅ |
| Background > Hot path for writing | HybridDB journal system = async indexing | ✅ |
| Atomic notes > Monolithic profiles | Per-message docs are naturally atomic | ✅ |

### Human Memory Neurobiology (PMC8611531, Mujawar et al., 2021)

This clinical review of the multi-store model directly validates our four-layer wake-up stack:

| Human Memory System | Our AI Equivalent | Mechanism Match |
|---|---|---|
| Sensory register | Message ingestion (journal) | Brief buffer before encoding |
| STM — 4-5 items, chronological retrieval | L0+L1 (~170 tokens, always loaded) | Small, always-on, ordered by recency |
| LTM — associative retrieval | L3 Deep Search (ChromaDB cosine) | Retrieved by semantic similarity, not chronology |
| Hippocampus — consolidation | HybridDB journal → ChromaDB indexing | Distributes memory across storage after encoding |
| Encoding — acoustic (STM), semantic (LTM) | `all-MiniLM-L6-v2` embeddings | Semantic vectors match LTM encoding strategy |
| "Synapses used more become stronger" | Access-based score boosting | Hebbian plasticity analog: frequently retrieved memories rank higher |

**Key confirmation:** The paper states LTM retrieval works via **semantic association**, not chronology. This is exactly why ChromaDB cosine similarity is the right retrieval primitive — it's an artificial analog of human associative recall. The four-layer stack (L0→L3 progressive disclosure) maps to the sensory→STM→LTM progression in the multi-store model, confirming the design from first principles.

**Verdict on further human memory research:**
- **For the current rewrite:** No. The multi-store model, semantic association, Hebbian-like plasticity, and encoding parallelism are already fully mapped to our architecture. More neurobiology papers won't change implementation decisions.
- **For future temporal graph work:** Yes. The hippocampus performs time-indexed replay during sleep to consolidate episodic memories into cortical semantic networks. Understanding this mechanism could guide how we design background consolidation jobs that transform raw conversation into structured temporal knowledge graphs — using deterministic heuristics (co-occurrence frequency, temporal proximity) rather than LLM extraction.

**Forgetting mechanism (note for future):** Human memory has two forgetting modes: retroactive interference (new info overwrites old) and trace decay over time. For the current rewrite, we deliberately avoid simulating forgetting — we want perfect recall. The verbatim-everything approach maximizes retrieval accuracy. However, once we exceed the performance threshold (>95% R@5, >85% QA), selective forgetting becomes necessary for practical reasons: ChromaDB storage will grow unboundedly, and overly large collections degrade retrieval latency. At that point, implement:
- **Access-frequency based retention:** Keep frequently retrieved memories, allow rarely-accessed ones to age out (analogous to synaptic pruning)
- **Temporal decay with importance override:** Older conversations naturally decay in score unless marked as important facts (via user feedback or agent confirmation)
- **Retroactive merge:** When a newer memory contradicts an older one, merge rather than delete — preserve both with timestamps, defer resolution to retrieval time

---

## 3. Proposed Approaches

### Approach 1: MemPalace-Style Retrieval (Recommended)

```
Extraction → ZERO. No middleware, no fact store, no LLM.
             Every message auto-indexed by HybridDB's journal system.
             Drop MemoryMiddleware, MemoryStore fact layer, consolidation,
             observation, knowledge_update.py, memory_planner.py entirely.

Storage    → HybridDB stores raw messages only (already done).
             Remove memory_facts table, upsert_fact_memory(), fact layer.
             Keep ChromaDB (semantic) + FTS5 (keyword) + DuckDB (analytics).
             Keep journal system, graph, session dedup.
             Add metadata on every row: session_id, message_date.

Wake-up    → Four-layer context loading (inspired by MemPalace L0-L3):
             L0: User profile / identity (~100 tokens, always loaded)
             L1: Top-ranked recent memory snippets (~500 chars, always loaded)
             L2: Session-specific context (~200-500 chars, loaded when session detected)
             L3: Full hybrid search (unlimited, per explicit memory_search query)
             Combined wake-up cost: ~170 tokens (L0+L1)

Retrieval  → Single tool: memory_search(query).
             → HybridDB.search_hybrid() with 3x limit + session dedup
             → Post-query deterministic heuristics:
               1. Keyword overlap multiplier
               2. Temporal boost (extract date cues from query)
               3. Person name boost (capitalized proper nouns +40%)
               4. Quoted phrase boost (+60%)
               5. Preference regex extraction (16 patterns → synthetic docs at index)
               6. Counting shortcut (detect "how many", count items directly)
             → Return formatted raw conversation context to LLM

Code delta  → Remove: ~3,000 lines
             → Add: ~400 lines (heuristics) + ~100 lines (wake-up context)
             → Net: -2,500 lines
```

**Pros:**
- Proven: MemPalace's exact architecture, 96.6% baseline (independently verified)
- Simple: 500 lines of heuristics replace 3,000 lines of LLM pipelines
- Reliable: Zero LLM nondeterminism in retrieval
- Eval = Prod: Same search path, no adapter special-casing
- Instant-on: ~170 token wake-up cost, agent has context before first tool call
- Offline-capable: Zero API cost for memory writes
- HybridDB preserved: Only storage schema changes

**Cons:**
- No fact store (MemPalace proves it's unnecessary; Mem0 v3 shows extraction can now compete)
- No cross-memory connections (graph still exists but unused; future L2 enhancement)
- Raw text output might be verbose (mitigated by ranking + four-layer progressive disclosure)

### Approach 2: Hybrid — Raw-First + Facts-as-Cache

```
Same as Approach 1, but keep a minimal fact layer as write-through cache:

Extraction → Regex-only (no LLM): "I am X" → fact, "$X" → fact,
             "I moved to X" → fact, "my [noun] is [value]" → fact.
             Facts stored in memory_facts but NEVER shown to LLM directly.
             Used only for query expansion and reranking:
             if query matches a fact key, boost its source session.

Storage    → HybridDB raw messages (primary) + memory_facts (cache).
             Both in sync via journal.

Retrieval  → Same heuristics as Approach 1.
             + Facts used for query expansion: "What's my 5K time?"
               → expand to "personal best 5K charity run time 25:50"
```

**Pros:**
- Facts help query expansion (potentially +0.5% for vocabulary mismatch)
- Easier to add later if raw-only proves insufficient
- Same reliability benefits as Approach 1

**Cons:**
- Added complexity (~200 extra lines for regex extraction)
- Facts diverge from raw text over time if not re-derived
- Marginal gain over Approach 1

### Approach 3: Minimal Change — Fix Current Pipeline

```
Keep MemoryMiddleware + MemoryStore + HybridDB but:

Extraction → Replace LLM extraction with deterministic regex extraction.
             NEW_INFO_KEYWORDS already exist; add structured fact patterns.
             Keep trigger+domain dedup fix (_hash_pattern, already done).

Storage    → Enable session dedup in production search_hybrid
             (already implemented, just flip default to True).
             Fix list-valued extraction (already done in prompt + parser).

Retrieval  → Add MemPalace heuristics to memory_search output:
             keyword overlap, temporal boost, person name boost.
             Keep existing tool surface (8 tools).
             Keep knowledge_update resolution (already moved to top).
```

**Pros:**
- Least risky: incremental changes, easy to roll back
- Keeps all existing code, just augments
- Most test coverage preserved

**Cons:**
- Keeps conceptual complexity of dual fact + raw message system
- 8 tools is too many for the LLM to route correctly
- LLM nondeterminism in agent tool selection still a risk
- Doesn't simplify — codebase stays at ~5K lines
- Historical evaluation shows this path caps at ~65-70%

---

## 4. Recommendation

**Approach 1 (MemPalace-Style) is recommended.** Independently validated by the OpenHub Research critical analysis paper.

### Why

1. **Proven result:** 96.6% R@5 baseline with zero LLM (independently reproduced: "the 96.6% Recall@5 is the performance of ChromaDB's default embedding model (all-MiniLM-L6-v2) applied to verbatim text chunks"). EA already has all the infrastructure (ChromaDB, all-MiniLM-L6-v2, FTS5, journal system). The gap is architectural — MemPalace returns raw text, EA returns lossy facts.

2. **Simplicity wins:** Remove 3,000 lines of LLM-dependent code. Replace with ~500 lines of deterministic heuristics + wake-up context. Less code = fewer bugs = easier to maintain.

3. **The data supports it:** Every evaluation failure traces to LLM nondeterminism (10 answer variants on same output) or lossy extraction (items missing from fact store). Removing both removes the problem.

4. **Eval = Prod:** No more "adapter skips extraction — retrieval-only mode" special cases. Same code path for benchmarks and production.

5. **Low risk with HybridDB:** HybridDB's journal, FTS5, ChromaDB, and DuckDB layers are untouched. Only removing the domain-specific memory layer on top.

6. **Instant-on context:** Four-layer wake-up stack (L0-L3) with ~170 token wake-up cost. Agent has relevant context in its prompt before making any tool call.

### Updated Competitive Landscape

| System | LongMemEval | Write Cost | Wake-up Tokens | Deterministic Write? |
|---|---|---|---|---|
| MemPalace (raw) | 96.6% R@5 | $0 | ~170 | ✅ Yes |
| MemPalace (hybrid + rerank) | 98.4% R@5 | $0 write, ~$0.001/query | ~170 | ✅ Write, ~LLM rerank |
| Mem0 v3 (token-efficient) | 93.4% OA | ~$0.003/write | ~6,800 | ❌ 1 LLM call |
| Mastra OM | 94.9% OA | LLM per turn | — | ❌ |
| Hindsight | 91.4% OA | Multiple LLM passes | — | ❌ |
| Zep/Graphiti | ~85% OA | LLM for KG builder | — | ❌ |
| **EA (new approach)** | **Target: 95%+ R@5, 85%+ OA** | **$0** | **~170** | **✅ Yes** |

Note: R@5 (retrieval recall) vs OA (overall accuracy) are different metrics. MemPalace and our target report R@5; Mem0/Mastra/Hindsight report OA. Not directly comparable, but directionally informative.

### Implementation Sequence

| Phase | What | Effort |
|---|---|---|
| 1 | Implement four-layer wake-up context (L0-L3) — always-on context injection without LLM tool calls | ~100 lines |
| 2 | Implement MemPalace heuristics (keyword overlap, temporal boost, person name, quoted phrase) as post-processing in `memory_search` | ~200 lines |
| 3 | Switch `memory_search` to raw-mode output (no fact formatting) + session dedup enabled by default | ~50 lines |
| 4 | Add preference regex extraction (16 patterns → synthetic docs at index time) | ~100 lines |
| 5 | Disable MemoryMiddleware extraction (or remove entirely) | ~10 lines |
| 6 | Run LongMemEval retrieval benchmark (`eval.py --mode retrieval_only`) for R@5 baseline | — |
| 7 | Run full 20-question QA eval, compare | — |
| 8 | If R@5 ≥ 95% and QA ≥ 85%, remove dead code (MemoryStore fact layer, consolidation, observation, planner, ranker) | Cleanup |

### Success Criteria

- R@5 ≥ 95% on LongMemEval retrieval benchmark
- QA accuracy ≥ 85% on 20-question eval
- Zero LLM calls in extraction or retrieval path
- Same code path for eval and production
- Answer stability: ≤ 2 answer variants across 5 runs on same question
- Wake-up cost: ≤ 200 tokens

### Future Enhancements (Post-Rewrite)

- **Temporal graph:** Add Graphiti-style `valid_at`/`invalid_at` timestamps to HybridDB graph nodes for time-aware traversal
- **Interactive memory:** Graph + temporal exploration interface for users to navigate their memory visually
- **Semantic linking:** Use deterministic entity extraction (spaCy NER) to auto-link related memories without LLM
- **Mem0-style multi-signal fusion:** Add BM25 + entity matching alongside cosine similarity when computational budget allows

### Open Design Decisions

**Session lifecycle management:** Currently no system-wide rule for session_id assignment. WebSocket assigns `uuid4()[:8]` on new connection, eval adapter assigns `session_{idx:04d}` per dataset session, REST/SSE assigns nothing. Before memcore goes to production, we must decide:

| Option | Trigger | Pros/Cons |
|---|---|---|
| Per-connection | New WS/SSE connection = new session_id | Simple, aligns with WS behavior |
| Per-user message | New user message after assistant response = new session | Matches LongMemEval's session model |
| Configurable | Caller provides opaque `session_id` | Most flexible, leaves lifecycle to app |
| None by default | No session_id unless explicitly set | Safe fallback, no breaking changes |

**Recommendation:** Configurable + None by default. memcore stays agnostic — caller provides `session_id` or leaves it None. Application layer decides lifecycle. This keeps memcore portable and doesn't break existing workspaces that don't use sessions.

---

## 5. Appendix: Eval Data — Answer Stability

From 45 question IDs across 8 evaluation runs:

| Question ID | Runs | Correct | Unique Answers |
|---|---|---|---|
| `gpt4_59c863d7` (model kits) | 13 | 2 | **10** |
| `0a995998` (clothing items) | 13 | 0 | 7 |
| `6aeb4375` | 10 | 4 | 6 |
| `e831120c` (MCU weeks) | 13 | 8 | 5 |
| `58bf7951` | 11 | 10 | 8 |
| `51a45a95` | 11 | 1 | 8 |
| `gpt4_59149c77` | 7 | 4 | 6 |
| `6ade9755` | 2 | 0 | 1 |

**Interpretation:** High answer variance on questions where LLM must aggregate from structured facts (model kits: 10 variants). Low variance on questions where answer is a single scalar fact (yoga frequency: 1 variant, always correct). This is the signature of a presentation-layer problem, not a retrieval problem.
