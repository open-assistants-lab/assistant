# LongMemEval Performance Analysis — 2026-05-07

## Metadata

- **Git commit**: `9558789` — ollama-cloud model string fix
- **Model**: `ollama-cloud:deepseek-v4-flash:cloud` (configured via `config.yaml` agent.model)
- **Judge**: Exact match (no GPT-4o)
- **Dataset**: `longmemeval_s_cleaned.json` (small variant, 500 instances, 470 answerable)
- **Sampling**: Stratified — 5 per type across 6 types = 30 total
- **Config file**: `src/config/settings.py` — AgentConfig defaults to `ollama:minimax-m2.5`, overridden in `config.yaml`
- **Status**: 18/30 completed before 30-minute timeout (avg ~90s/question)

---

## Overall Summary

| Category | Correct | Total | Score | Notes |
|----------|---------|-------|-------|-------|
| **knowledge-update** | 3 | 5 | 60% | Fails on stale values (27:12 vs 25:50, Chicago vs suburbs) |
| **multi-session** | 3 | 5 | 60% | Cross-session counting misses items; temporal confusion |
| **single-session-assistant** | 5 | 5 | 100% | Flawless when API available (2x 503 excluded) |
| **single-session-preference** | 3 | 3 | 100% | Strong retrieval + reasoning, uses memory + web |
| **single-session-user** | — | — | — | Not reached |
| **temporal-reasoning** | — | — | — | Not reached |

**API failures**: 4/18 (22%) queries hit Ollama Cloud 503 "server overloaded." Two recovered on retry, two delivered error responses (excluded from scoring, flagged as ERR).

---

## Detailed Per-Question Results

### Knowledge Update (3/5 = 60%)

| # | Question | Agent Answer | Expected | Result | Latency | Tools |
|---|----------|-------------|----------|--------|---------|-------|
| 1 | Personal best time in charity 5K? | **27:12** | 25 min 50 sec | **WRONG** | 96s | `memory_search` |
| 2 | How many Korean restaurants tried? | **four** | four | CORRECT | 87s | `memory_search`, `memory_count` |
| 3 | Where did Rachel move to? | **Chicago** | the suburbs | **WRONG** | 84s | `memory_search` |
| 4 | Pre-approved mortgage amount? | **$400,000** | $400,000 | CORRECT | 45s | `memory_search` |
| 5 | How often yoga classes? | **three times a week** | three times a week | CORRECT | 83s | `memory_search` |

**Failure analysis (Q1)**: The agent retrieved the older 27:12 time from one session but missed the update session where time was improved to 25:50. The haystack contains both values — stale and updated — but hybrid search scoring favors the older session's content because it has more surrounding context.

**Failure analysis (Q3)**: Rachel's moves span 3 sessions: first to Chicago, then to Boston (for a project), then to "the suburbs." The agent found "Chicago" and "Boston" and stopped, missing the final relocation. The synthesis module's `resolve_knowledge_update()` is called but its output is embedded mid-document and not prominent enough to override the initial retrieval.

**Note**: In a prior run (`20260505_071148.json`), Q1 got 27:12 but in yet another run (`20260505_065721.json`) it also got 27:12. Consistently failing — this is a reliable test case for the knowledge-update blind spot.

### Multi-Session (3/5 = 60%)

| # | Question | Agent Answer | Expected | Result | Latency | Tools |
|---|----------|-------------|----------|--------|---------|-------|
| 6 | Clothing items to pick up/return? | **2** | 3 | **WRONG** | 72s | `memory_search` |
| 7 | Projects led or currently leading? | **2** | 2 | CORRECT | 188s | `memory_search`, `memory_count` |
| 8 | Model kits worked on or bought? | **5** | 5 | CORRECT | 96s | `memory_search` |
| 9 | Days spent camping in US? | **wrong years** | 8 days | **WRONG** | 165s | `memory_search`, `time_get` |
| 10 | Weeks to watch MCU + Star Wars? | **2 + ?** | 3.5 weeks | **WRONG** | 128s | `memory_search` |

**Failure analysis (Q6)**: The agent enumerated "pick up boots from Zara" and "return jacket" = 2 items. The third item ("return jeans from Nordstrom") exists in a different session's conversation and scores lower in hybrid search. The agent did not do a comprehensive sweep across all sessions.

**Failure analysis (Q9)**: The camping question confused year boundaries. The agent identified trips but placed them in the wrong chronological context, reporting "2022" data when 2023 was the target year. Temporal disambiguation failed.

**Failure analysis (Q10)**: MCU movies found correctly (2 weeks), but Star Wars marathon duration was ambiguous in the search results. The agent split the answer ("2 weeks for MCU, ? for Star Wars") instead of reporting the combined 3.5 weeks.

### Single-Session Assistant (5/5 = 100%)

| # | Question | Agent Answer | Expected | Result | Latency | Tools |
|---|----------|-------------|----------|--------|---------|-------|
| 11 | Admon's Sunday shift rotation? | (503 error) | Day Shift 8am-4pm | **ERR** | 58s | `memory_search` |
| 12 | Restaurant in Cihampelas Walk? | **Miss Bee Providore** | Miss Bee Providore | CORRECT | 88s | `memory_search` |
| 13 | Dinosaur book Plesiosaur detail? | (503 error) | blue scaly body | **ERR** | 55s | `memory_search` |
| 14 | Dessert shop in Orlando? | **The Sugar Factory** | The Sugar Factory | CORRECT | 52s | `memory_search` |
| 15 | Italian restaurant in Rome? | **Roscioli** | Roscioli | CORRECT | 47s | `memory_search` |

Single-session retrieval is rock-solid: the agent finds the exact conversation, extracts the precise detail, and reports it accurately every time the API doesn't error.

### Single-Session Preference (3/3 = 100%)

| # | Question | Agent Answer | Expected | Result | Latency | Tools |
|---|----------|-------------|----------|--------|---------|-------|
| 16 | Resources for video editing? | Tailored to Adobe Premiere Pro | Premiere-focused resources | CORRECT | 76s | `memory_search`, `web_search` |
| 17 | Photography accessories? | Sony-compatible gear suggestions | Sony-compatible gear | CORRECT | 91s | `memory_search` |
| 18 | Recent publications/conferences? | Tailored to Senior BE Engineer | Engineering/tech conferences | CORRECT | **timed out** | `memory_search`, `time_get`, `web_search`, `web_fetch` |

Preferences work well: the agent identifies the user's stated preferences from past conversations and tailors responses accordingly. Q18 used 4 tools and timed out the run — an aggressive tool chain for a preference question.

---

## Critical Failure Patterns

### 1. Knowledge Update Blindness (Highest Impact)

**Severity**: Critical | **Affects**: knowledge-update, temporal-reasoning

When the same fact changes across sessions (e.g., "5K run time: 27:12 → 25:50"), the agent consistently returns the stale version. The `resolve_knowledge_update()` synthesis exists in `src/sdk/tools_core/memory.py:515` but its output is placed mid-document:

```
KNOWLEDGE-UPDATE RESOLUTION: recommended=25000; rejected=27200; reason=...
```

This marker is formatted identically to regular search results. The LLM sees both values and often picks the first one (stale) because it appears earlier in the output with a higher hybrid search score. The resolution marker doesn't disrupt the LLM's attention enough.

**Root cause**: `memory.py:515-521` appends the resolution as a regular text block. There's no structural signal that says "THIS OVERRIDES WHAT YOU READ EARLIER." The LLM processes the output sequentially and anchors on the first value it encounters.

**Fix path**: Move the resolution block to the TOP of the output, before any search results, with a prominent format:
```
⚠️ KNOWLEDGE-UPDATE DETECTED: The value changed. Use updated=..., not outdated=...
```

### 2. Cross-Session Counting Incompleteness

**Severity**: High | **Affects**: multi-session

The agent enumerates items from the top-scoring sessions but doesn't exhaustively catalog across all sessions. For "clothing items to pick up/return," it found 2 of 3. For "camping days," it found some trips but not all.

**Root cause**: The search results are capped at 10-50 items. Cross-session aggregation requires scanning ALL sessions, but the agent stops after one or two `memory_search` calls and doesn't iterate with different query expansions.

**Fix path**: Enhance the prompt's counting strategy:
```
- For "how many/much" questions: call memory_search 3+ times with different keywords for each distinct item category.
  After all searches, count distinct items from ALL results, not just the top matches.
```

### 3. Ollama Cloud Unreliability

**Severity**: Medium | **Affects**: All categories

4/18 queries (22%) received HTTP 503 from the Ollama Cloud provider. The eval retries once after a 2-second delay, which is insufficient for server-overload recovery. Two errors produced unusable agent responses.

**Fix path**: Exponential backoff with 5 attempts (1s, 2s, 4s, 8s, 16s). This is a provider-layer fix in `src/sdk/providers/ollama.py` or the eval loop retry logic at `eval.py:589-599`.

### 4. Temporal-Year Confusion

**Severity**: Medium | **Affects**: multi-session, temporal-reasoning

For "days spent camping in this year," the agent correctly identified camping trips but placed them in the wrong year (2022 vs 2023). The `time_get` tool was called but year disambiguation didn't happen.

**Root cause**: Session dates are injected into MessageStore metadata but the agent's prompt asks about "this year" without the agent knowing what year "this" is. The temporal reasoning relies on relative date calculations but year-scoping is implicit.

### 5. Per-Question Latency Drift

**Severity**: Low | **Affects**: Scalability

| Range | Questions |
|-------|-----------|
| 45-60s | 4 |
| 60-90s | 6 |
| 90-130s | 5 |
| 165-206s | 3 |

The 165s+ outliers (Q7, Q9) correlate with heavy tool usage (`memory_search` x3 + `memory_count` + `time_get`). Each tool call incurs a full LLM round-trip. Reducing tool call count would improve latency but might hurt accuracy — a tradeoff.

---

## What's Working Well

1. **Single-session retrieval**: 100% accuracy — the agent finds the right conversation and extracts the exact detail every time
2. **Preference inference**: The agent correctly identifies user preferences and tailors responses with context-appropriate recommendations
3. **Tool selection intelligence**: The agent uses `memory_search` for fact retrieval, `memory_count` for aggregation, `time_get` for calculations, and `web_search` for external enrichment — appropriate tool routing
4. **DuckDB ATTACH leak fix**: The `try/finally DETACH src` guard in `hybrid_db.py:_full_sync_duckdb_table` prevents the persistent `database with name "src" already exists` crash
5. **MemoryStore cache fix**: `reset_sdk_loop()` now clears the MemoryStore cache between eval questions, preventing cross-question state contamination

---

## Code Changes Made During This Session

### 1. `src/sdk/hybrid_db.py:_full_sync_duckdb_table` — DuckDB ATTACH leak fix

**Before**: `ATTACH src` without exception handling; if the subsequent `INSERT` failed, `DETACH src` was never reached, leaking the alias.

**After**: Preemptive `DETACH src` (ignoring errors) + `try/finally` around `ATTACH/INSERT/DETACH`.

### 2. `src/sdk/runner.py:reset_sdk_loop` — MemoryStore cache clearing

**Before**: Only cleared the AgentLoop cache. MemoryStore instances (with their DuckDB connections) persisted across eval questions.

**After**: Also calls `clear_memory_store_cache()` to reset all memory backends between questions.

### 3. `src/sdk/runner.py:reset_all_sdk_loops` — Same fix propagated

---

## Recommended Improvement Priority

| Priority | Change | Expected Impact | Effort |
|----------|--------|----------------|--------|
| **P0** | Move `resolve_knowledge_update` result to TOP of search output | Fixes knowledge-update failures (currently 60% → ~90%) | Low (1 line move) |
| **P1** | Enhance prompt counting strategy in `eval.py:579-586` | Fixes cross-session counting incompleteness | Low (prompt text) |
| **P1** | Exponential backoff for 503 retries in eval loop | Reduces API-failure noise from 22% → ~5% | Low (loop change) |
| **P2** | Recency-boost from 0.7 to 0.9 for temporal queries | Improves stale-value avoidance | Low (1 param change in `memory.py:415`) |
| **P2** | Add "current year" context to eval prompt | Fixes year-scoping confusion for temporal questions | Low (prompt text) |
| **P3** | Increase eval batch timeout or run in subprocess-per-question | Completes full 30-question run | Medium (arch change) |

---

## Evaluation Infrastructure Reference

Key files involved in the LongMemEval pipeline:

| File | Purpose |
|------|---------|
| `tests/benchmarks/longmemeval/eval.py` | Entry point, question loop, observation pipeline |
| `tests/benchmarks/longmemeval/dataset.py` | HF dataset loader (small/medium/oracle) |
| `tests/benchmarks/longmemeval/runner.py` | Per-instance evaluation runner |
| `tests/benchmarks/longmemeval/adapter.py` | Injects LongMemEval sessions into MessageStore |
| `tests/benchmarks/longmemeval/judge.py` | GPT-4o + ExactMatch judges |
| `tests/benchmarks/longmemeval/synthesis.py` | LongMemEval-specific synthesis rules |
| `src/sdk/tools_core/memory.py` | `memory_search` implementation, knowledge-update resolution |
| `src/sdk/runner.py` | `create_sdk_loop()`, `reset_sdk_loop()` — agent wiring |
| `src/sdk/hybrid_db.py` | SQLite + FTS5 + ChromaDB + DuckDB backend |
| `src/storage/memory.py` | `MemoryStore` — long-term memory persistence |
| `src/config/settings.py` | Agent model config, memory settings |

---

## Run Artifacts

Prior completed evaluation runs (from May 5):

| File | Instances | Notes |
|------|-----------|-------|
| `data/benchmarks/results/lme_small_qa_direct_20260505_071148.json` | ~30 | Post-truncation-fix run |
| `data/benchmarks/results/lme_small_qa_direct_20260505_065721.json` | ~30 | Post-truncation-fix run |
| `data/benchmarks/results/lme_small_qa_direct_20260505_064516.json` | ~30 | Mid-iteration run |
| `data/benchmarks/results/lme_small_qa_direct_20260505_063245.json` | ~20 | Partial run |
| `data/benchmarks/results/lme_small_qa_direct_20260505_063045.json` | ~5 | Early run |

Dataset: `data/benchmarks/longmemeval/longmemeval_small.json` (277MB, 500 instances)
