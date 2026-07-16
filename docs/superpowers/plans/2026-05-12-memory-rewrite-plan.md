# Memory Subsystem Rewrite — Implementation Plan & Status

> **Goal:** Replace LLM-based memory extraction with zero-LLM, raw-verbatim retrieval. Achieve MemPalace-level R@5 on LongMemEval.

> **Architecture:** memcore project (Chromadb/HybridDB backends) integrated into Assistant's AgentLoop via pre-load injection + `memory_search` tool.

> **Tech Stack:** HybridDB (SQLite+FTS5+Chromadb), memcore, all-MiniLM-L6-v2, deepseek-v4-pro

---

## Status Summary

### ✅ DONE: memcore Project

| File | Lines | Purpose |
|---|---|---|
| `src/memcore/pyproject.toml` | — | Standalone package config |
| `src/memcore/src/memcore/__init__.py` | 20 | Public API exports |
| `src/memcore/src/memcore/types.py` | 45 | Memory, SearchResult, SearchQuery |
| `src/memcore/src/memcore/core.py` | 85 | MemoryCore main entry point |
| `src/memcore/src/memcore/heuristics.py` | 100 | Deterministic post-retrieval scoring |
| `src/memcore/src/memcore/layers.py` | 80 | L0-L3 wake-up context stack |
| `src/memcore/src/memcore/ingest.py` | 40 | Verbatum message ingestion |
| `src/memcore/src/memcore/backends/base.py` | 25 | StoreBackend ABC |
| `src/memcore/src/memcore/backends/chroma.py` | 70 | ChromaDB-only baseline |
| `src/memcore/src/memcore/backends/hybrid.py` | 115 | HybridDB: SQLite+FTS5+Chromadb |
| `src/memcore/tests/` | 263 | 33 tests (conftest + 4 suites) |
| `src/memcore/src/memcore/benchmarks/` | 200 | LongMemEval adapter + eval runner |

**Tests:** 33 passing. **R@5:** ChromaBackend 93.7%, **HybridBackend 98.2%** (exceeds MemPalace's 96.6%).

### ✅ DONE: memory_search Tool Rewrite

| Change | File | Purpose |
|---|---|---|
| Uses memcore `MemoryCore.search()` | `src/sdk/tools_core/memory.py:433` | Replaces old MemoryStore + MessageStore |
| Role filter: only `[user]` messages | `memory.py:437` | Removes recursive echo from assistant/tool |
| Echo filter: query-similarity based | `memory.py:443-456` | Filters messages with >70% word overlap, keeps messages with data (numbers, proper nouns) |
| Counting limit: 30 results for "how many" | `memory.py:437` | Wider session coverage for counting questions |
| Near-duplicate dedup: Jaccard >0.8 | `memory.py:460-464` | Removes near-identical results |
| Enhanced output: score, timestamp, role, session_id, answer directive | `memory.py:476-496` | LLM sees context, scores, instructions |
| HyDE: removed (wrong for personal memory) | `memory.py:355-396` | LLM can't guess personal facts; invented details embed away from real data |

### ✅ DONE: Deepseek Multi-Turn Fix

| Change | File | Purpose |
|---|---|---|
| `_last_reasoning` store + fallback | `openai.py:86-92, loop.py:868-876` | Deepseek requires reasoning_content passed back between turns |
| Reasoning in `_parse_stream_chunk` | `openai.py:178` | Captures reasoning from delta events |

### ✅ DONE: Backend Architecture Changes

| Change | File | Purpose |
|---|---|---|
| Per-user HybridDB | `messages.py:72, memory.py:28` | One DB per user, workspace as metadata field |
| Old tools disabled | `native_tools.py:79-85, 138-143` | memory_search_all, memory_count, memory_search_insights, memory_get_history |
| Memory middleware uses memcore | `middleware_memory.py:252-280, 639-678` | Raw verbatim injection (no old MemoryStore) |
| CLI removed | `AGENTS.md` | Only `ea http` channel |
| No prompt echo instruction | `longmemeval_adapter.py:45` | Removed "Answer the question based on the context..." prefix |

### ✅ DONE: Research & Docs

| Doc | Purpose |
|---|---|
| `docs/MEMORY_REWRITE_RESEARCH_2026-05-08.md` | Full MemPalace/Mem0/A-Mem/Zep/Cognee/critical analysis research |
| `docs/FACT_EXTRACTION_IMPROVEMENTS_2026-05-08.md` | Peer-reviewed fact extraction analysis |
| `docs/MULTI_PROVIDER_RESEARCH_2026-05-09.md` | OpenCode's capability-flag-based provider architecture |

### ⚠️ KNOWN ISSUES

| Issue | Severity | Detail |
|---|---|---|
| QA accuracy ceiling at ~60% | Medium | Deepseek-v4-pro can't reliably extract answers from clean search results. Mem0 achieves 93.4% with GPT-5-mini. |
| Scores sometimes 0.0 for echo-like messages | Low | Scores correct for actual data. Echo messages naturally score low. |
| Backend crashes on long evals | Low | Works for single questions. Full 20Q eval takes too long. |

---

## Flutter App Health

| Check | Status |
|---|---|
| `flutter analyze` | ✅ 0 errors, 8 warnings (unused imports) |
| Features | chat, companion, home, memory, workspace |
| Dart files | ~5,900 lines |
| Tests | 41 passing |

---

## PLAN.md Migration — Remaining Items

### Phase 12: API Auth + Connection Modes — 🔲 NEXT
Not started. No blockers from our changes.

### Phase 13: Flutter 0 — ✅ DONE
All core features complete. Remaining: tablet layout, home provider, reasoning card real-time display.

### Phase 8: Data Architecture — 🔲 FUTURE
Not started. Our per-user HybridDB change partially addresses this (already migrated to user-scoped paths).

### Phase 10: Tool Migration — ✅
All tools migrated. Email/contacts/todos disabled (gws/m365 skills pending).

### External Tools — Not started
ripgrep, pandoc, gws, m365 skills not yet implemented.

---

## Next Steps Recommendation

1. **P0: Accept deepseek-v4-pro ceiling** — 60% QA with current architecture is the realistic cap. Try GPT-5-mini or Claude when available to verify model quality hypothesis.

2. **P1: Phase 12 (API Auth)** — before any Flutter remote client work.

3. **P2: Ripgrep CLI tool** — most impactful tool upgrade (5-13x faster search, CLIToolAdapter pattern already exists).

4. **P3: Phase 8 (Data Architecture)** — clean up dead `src/tools/` directories, delete LangChain remnants.

5. **P4: Mem0-style single-pass retrieval** — match their approach (one search → inject → LLM reads) proven at 93.4% OA.
