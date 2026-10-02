# GBrain Memory Analysis & Recommendations for EA

Research of [garrytan/gbrain](https://github.com/garrytan/gbrain) (v0.25.0, 12.6k stars, 126 commits) cross-referenced against our memory implementation in `src/sdk/` and `src/storage/`.

**Prerequisites**: This document assumes the extraction pipeline is working. A blocking bug (`NameError: name 'asyncio' is not defined` in `_extract_with_llm`) was fixed 2026-05-02 (see `docs/memory-extraction-fix-review.md`). The pipeline now produces memories correctly, but end-to-end verification via a full LongMemEval re-run is outstanding.

**Peer review commentary** (2026-05-02): This document has been updated to reflect a cross-review against the actual codebase. Edits are tagged with `[review]` markers explaining changes from the original draft.

---

## 1. GBrain Overview — What It Does Differently

GBrain is a personal knowledge brain built by Garry Tan (YC President) that powers his OpenClaw/Hermes agent deployments. Production numbers: **17,888 pages, 4,383 people, 723 companies**, 21 cron jobs, built in 12 days.

### Core Architecture

```
Brain Repo (git)  ←→  GBrain (retrieval)  ←→  AI Agent (read/write via 29 skills)
```

- **Brain Repo**: markdown files as source of truth (human-readable, git-tracked)
- **GBrain**: Postgres + pgvector hybrid search + knowledge graph
- **Agent**: 29 fat skills define HOW to use the brain

### The Five Pillars of GBrain's Memory Design

| Pillar | Description |
|--------|-------------|
| **Auto-wiring knowledge graph** | Every page write extracts entity references and creates typed links (`attended`, `works_at`, `invested_in`, `founded`) with zero LLM calls for entity *detection*; relationship *inference* uses context-aware rules |
| **Compiled truth + timeline** | Above-the-line = current synthesis (rewritten as evidence changes). Below-the-line = append-only evidence trail |
| **Always-on signal detection** | On every message, a cheap model runs in parallel to capture original thinking and entity mentions — never blocks the main loop |
| **Hybrid search with graph boost** | Vector + keyword + RRF fusion + multi-query expansion (Haiku rephrases 3 ways) + backlink-boosted ranking |
| **Tiered entity enrichment** | T3 (stub on first mention) → T2 (web enrichment at 3+ mentions) → T1 (full profile at 8+ mentions or after a meeting) |

### BrainBench Results (240 rich-prose pages)

| Metric | GBrain (with graph) | GBrain (graph disabled) | Δ |
|--------|---------------------|-------------------------|---|
| **Precision@5** | 49.1% | 17.7% | **+31.4 pts** |
| **Recall@5** | 97.9% | 54.2% | **+43.7 pts** |

The graph layer alone provides the bulk of the retrieval quality gap. Without it, GBrain reverts to hybrid vector+keyword with no structured relationships.

**[review] Caveat**: BrainBench tests document retrieval (240 pages with 30 questions each), not conversational memory retrieval. LongMemEval is the benchmark that actually applies to Assistant's use case. The +31.4 P@5 gain would likely be **smaller** for conversational fact retrieval (the task is different), but the direction holds: structured entity + relationship indexing meaningfully improves retrieval for any knowledge-grounded query.

---

## 2. EA Memory Architecture (Current State)

### System Overview

```
AgentLoop.run()
 ├── before_agent → MemoryMiddleware.before_agent()     [context injection]
 │   ├── get_memory_context()                              [profile: summary-level]
 │   └── _get_relevant_memory_context(query)               [query-specific retrieval]
 │       ├── ranker path (MEMORY_RANKER_ENABLED — OFF)     [scored + deduplicated]
 │       ├── planner path (MEMORY_QUERY_PLANNER_ENABLED — OFF) [intent-routed]
 │       └── baseline path (DEFAULT)                       [facts + memories + messages]
 ├── LLM call (with injected context)
 └── after_agent → MemoryMiddleware.after_agent()        [extraction, every 3 turns]
     ├── _extract_async() / _extract_with_llm()             [LLM extracts facts/prefs/corrections]
     ├── _store_patterns()                                   [shared storage logic]
     ├── upsert_fact_memory()                               [structured storage]
     └── on_conversation_end()                              [consolidation, every 10 msgs]
```

### Storage: 3 Layers

| Layer | Storage | Access Pattern |
|-------|---------|---------------|
| **memory_facts** | SQLite indexed table | `fact_key` = `scope:entity:attribute` → 20-50x faster than hybrid search |
| **memories** | HybridDB (SQLite + FTS5 + ChromaDB) | Learned preferences, workflows, corrections |
| **insights** | HybridDB | Synthesized patterns from grouped memories |

### 5 Memory Tools (in `src/sdk/tools_core/memory.py`)

| Tool | Description |
|------|-------------|
| `memory_get_history` | Get conversation history by days/date |
| `memory_search` | Most comprehensive: facts + temporal facts + conversation messages |
| `memory_search_all` | Unified search across memories + messages + insights |
| `memory_search_insights` | Search synthesized insights only |
| `memory_connect` | Create typed relationship between two memories |

### Retrieval Intelligence (750 lines, deterministic)

| Module | Lines | Purpose |
|--------|-------|---------|
| `memory_planner.py` | 195 | Intent classifier: `current_fact`, `historical_fact`, `timeline`, `summary`, `search_evidence`, `unknown` |
| `memory_ranker.py` | 398 | Scoring engine: 9 positive signals + 4 penalties, dedup, formatting |

### Key Files

| File | Lines | Role |
|------|-------|------|
| `src/storage/memory.py` | 1,511 | `MemoryStore` — full CRUD, search, confidence, connections, insights |
| `src/sdk/hybrid_db.py` | 2,208 | `HybridDB` — SQLite + FTS5 + ChromaDB + Graph + DuckDB |
| `src/sdk/middleware_memory.py` | 922 | `MemoryMiddleware` — extraction + injection (async/sync paths) |
| `src/sdk/tools_core/memory.py` | 439 | 5 memory tools |
| `src/sdk/memory_planner.py` | 195 | Deterministic intent classifier |
| `src/sdk/memory_ranker.py` | 398 | Deterministic evidence ranker |
| `src/storage/consolidation.py` | 393 | Background consolidation (contradictions, merges, insights) |

### Known Issues (as of 2026-05-03)

| Issue | Impact | Status |
|-------|--------|--------|
| Extraction pipeline was silently broken (`NameError`) | Zero memories extracted | **Fixed** (see `memory-extraction-fix-review.md`) |
| `DEFAULT_CONFIDENCE = 0.2` vs `list_working_memories(min_confidence=0.3)` | Facts with default confidence invisible in profile context | **Fixed** — changed to `0.4` (2026-05-02) |
| `_graph_nodes.domain` column migration | Graph-related operations crash on older DBs | **Fixed** — `create_table` auto-migrates missing columns via PRAGMA (2026-05-02) |
| `is_memory_query` requires `?` or explicit memory verbs | Self-referential queries like "What playlist did I create?" bypass memory search | **Fixed** — added `?` + user-subject path (2026-05-02) |

---

## 3. Gap Analysis: What EA Lacks vs GBrain

### Gap 1: No Auto-Entity Extraction from Content (CRITICAL)

**GBrain**: Every page write auto-extracts entity references and creates typed links. Entity *detection* is regex-based (zero LLM); relationship *inference* uses context-aware rules:
- "meeting with Alice" → `alice` entity page created, `attended` edge
- "CEO of Acme AI" → `works_at` edge inferred
- "invested in X" → `invested_in` edge

**EA**: Entities are only created through explicit LLM extraction (`_extract_with_llm`), which runs every 3 turns. No deterministic path exists. The graph connections require explicit `memory_connect` calls. The graph exists but does not auto-wire itself.

**Impact**: The graph layer is GBrain's single largest contributor to retrieval quality (+31.4 P@5 in brainbench). Assistant's graph is under-utilized because connections must be made manually.

### Gap 2: No Compiled Truth Pattern

**GBrain**: Every page has `compiled truth` (current synthesis, rewritten when evidence changes) + `timeline` (append-only evidence, never edited).

**EA**: Facts are either `current` or `superseded` — no synthesis layer that combines what we know *now* from multiple pieces of evidence. Temporal reasoning works but requires the LLM to manually reconcile superseded facts.

### Gap 3: No Always-On Signal Detection

**GBrain**: On every message, a cheap model runs *in parallel* to capture original thinking and entity mentions. Never blocks the main loop. The brain compounds on autopilot.

**EA**: Extraction only runs every 3 turns, uses the main agent model (not a cheap model), and is sequential (blocks or fire-and-forgets in a daemon thread). Signal detection is not a first-class operation.

**[review] Note**: The `after_agent` method now uses `asyncio.create_task(self._extract_async(...))` on the production event loop (post-fix), which is a step toward parallel signal detection. It still uses the main model, however.

### Gap 4: Memory Ranker/Planner Disabled by Default

Both `memory_ranker.py` (398 lines, 291-line test file, 15 test cases) and `memory_planner.py` (195 lines) are mature, tested modules but gated behind environment flags:

```python
# src/sdk/middleware_memory.py:242-257
ranker_enabled = os.environ.get("MEMORY_RANKER_ENABLED", "false")  # OFF
planner_enabled = os.environ.get("MEMORY_QUERY_PLANNER_ENABLED", "false")  # OFF
```

The baseline path (`_get_baseline_memory_context`) is a simple facts + messages join with no scoring, deduplication, or intent-aware routing.

**[review] Key insight**: The planner and ranker have fundamentally different latency profiles. The **planner** is pure deterministic classification (zero added latency). The **ranker** calls `collect_memory_candidates()` which does 4 parallel searches (facts, temporal facts, learned memories, messages) — this can 2-3x per-query latency. They should be enabled separately, not together.

### Gap 5: No Source-Aware Search Ranking

**GBrain**: Ranks sources by quality — curated directories outrank chat/daily noise. The SQL layer applies hard-excludes (`test/`, `archive/`) and compiled-truth boost (assessments outrank timeline noise).

**EA**: All sources treated equally in search. No differentiation between:
- User explicit statement vs LLM inference
- Recent vs old conversation
- Correction vs original fact
- High-confidence curated data vs low-confidence learned data

### Gap 6: Extraction Model Is the Main Agent Model

**GBrain**: Signal detection uses a cheap model (Haiku-level) in parallel. Enrichment uses appropriate models per tier.

**EA**: `_get_model()` uses `settings.agent.model` (the main agent model) for memory extraction. This means extraction costs scale with the primary model cost, and extraction blocks the main model if run synchronously.

### Gap 7: No Tiered Entity Enrichment

**GBrain**: Auto-escalation based on mention frequency:
- Tier 3 (1 mention) → stub
- Tier 2 (3+ mentions across different sources) → web + social enrichment
- Tier 1 (meeting or 8+ mentions) → full profile + compiled truth

**EA**: No auto-escalation concept. Facts are created at a single confidence level and decay/bounce based on access patterns, but there's no concept of "this entity is becoming important, enrich it."

### Gap 8: No Memory-First Resolution Pattern

**GBrain**: "Brain-first" skill: check the brain before any external API call. 28 of 29 skills follow this pattern.

**EA**: Memory context is injected into the system prompt but there's no enforced memory-first resolution pattern. The agent may search the web or query an external API before checking what it already knows.

### Gap 9: Query Expansion Is Regex-Only

**GBrain**: Multi-query expansion via Claude Haiku — rephrases the question 3 ways, runs hybrid search on each, fuses results.

**EA**: `_expand_queries()` is pure regex — truncates long queries, strips temporal keywords, maps preference terms. No semantic rephrasing.

---

## 4. Tailored Recommendations

### Priority Ladder

Recommendations are ordered by implementation priority, factoring in: (a) the extraction pipeline fix prerequisite, (b) risk-adjusted impact, and (c) latency/cost tradeoffs.

| Priority | Rec | Effort | Latency Risk | Status |
|----------|-----|--------|-------------|--------|
| **P0** | [Prereq] Verify extraction pipeline end-to-end | LongMemEval re-run | None | **Done** — 4/5 (80%) then 27/48 (56%) stratified |
| **P0** | [Prereq] Fix `DEFAULT_CONFIDENCE` threshold (0.2 → 0.4) | 1 line | None | **Done** (2026-05-02) |
| **P0** | [Prereq] Fix `is_memory_query` for non-`?` queries | 15 lines | None | **Done** (2026-05-02) |
| **P1** | Enable memory planner by default | 1 line | **None** | **Done** (2026-05-02) |
| **P1** | LLM multi-query expansion (cheap model, regex fallback) | 60 lines | Low | **Done** (2026-05-03) |
| **P1** | Search miss → targeted history fallback | 20 lines | None | **Done** (2026-05-03) |
| **P1** | Recency boost in search results | 10 lines | None | **Done** (2026-05-03) |
| **P1** | PREFERENCE_PROFILE intent in memory planner | 25 lines | None | **Done** (2026-05-03) |
| **P1** | Separate extraction model from main agent model | 30 lines | None | Backlog |
| **P1** | Add `memory_get_profile` tool | 80 lines | None | Backlog |
| **P2** | Deterministic entity candidate detection (no stub creation yet) | 80 lines | Low | Backlog |
| **P2** | Source-aware ranking signals in ranker | 40 lines | None | Backlog |
| **P3** | Enable memory ranker (after perf testing) | 1 line | **Medium** (2-3x query latency) | Backlog |
| **P3** | Content-to-graph auto-wiring (incremental) | 800-1200 lines | Low | Backlog |
| **P4** | Compiled truth layer | 200 lines | None (offline) | Backlog |
| **P4** | LLM-powered query expansion | 60 lines | **Medium** (per-query LLM call) | Backlog |
| **Backlog** | Tiered entity enrichment | 250 lines | Varies | Backlog |
| **Backlog** | Memory-first resolution middleware | 20 lines (prompt change) | None | Backlog |

---

### P0 — Prerequisites (Must Do First)

#### P0.1 Re-run LongMemEval Against Fixed Pipeline

**Rationale**: The extraction pipeline was producing zero memories until the 2026-05-02 fix. No recommendation can be validated until we re-run the benchmark and establish a baseline accuracy number. Current best estimate: 3/5 accuracy (60%) using raw conversation context only; expected improvement with working extraction: higher but unmeasured.

**Action**: `uv run python tests/evaluation/longmemeval_adapter.py --limit 10` with HTTP backend running.

#### P0.2 Fix Confidence Threshold Disconnect — ✅ DONE (2026-05-02)

**File**: `src/storage/memory.py:33`

**Change applied**:
```python
# BEFORE
DEFAULT_CONFIDENCE = 0.2

# AFTER
DEFAULT_CONFIDENCE = 0.4
```

**Why**: `list_working_memories(min_confidence=0.3)` is called by `get_memory_context()`, `get_compact_context()`, and `_get_summary_context()`. Facts extracted with the default confidence (0.2) are invisible in the profile context injected into the system prompt. LLM-extracted facts through `upsert_fact_memory` are capped at `MAX_CONFIDENCE = 0.7` and typically arrive at 0.7 or higher, but facts that fall through the non-structured path (missing `attribute` or `value` fields) get `DEFAULT_CONFIDENCE`.

#### P0.3 Fix `is_memory_query` for Self-Referential Queries

**File**: `src/sdk/memory_planner.py`

**Issue**: The query "What is the name of the playlist I created on Spotify?" returns `False` because `is_memory_query` requires EITHER `?` at the end OR explicit memory verbs ("search", "find", "recall"). Self-referential "what is my..." queries without `?` should be detected.

**Change**: Add `r"\bwhat(?:'s| is| was) (?:the|my|our)\b"` to `SELF_REFERENCE_PATTERNS` without requiring trailing `?`.

---

### P1 — High Impact, Low Risk

#### 1.1 Enable Memory Planner by Default — ✅ DONE (2026-05-02)

**File**: `src/sdk/middleware_memory.py:252`

**Change applied**:
```python
# BEFORE
planner_enabled = os.environ.get("MEMORY_QUERY_PLANNER_ENABLED", "false").lower() in {"1", "true", "yes"}

# AFTER
planner_enabled = os.environ.get("MEMORY_QUERY_PLANNER_ENABLED", "true").lower() not in {"0", "false", "no"}
```

**Why**: The planner is a deterministic intent classifier with **zero added latency**. It routes queries to appropriate retrieval strategies (current fact, historical fact, timeline, summary, search evidence) instead of the baseline one-size-fits-all approach. This is strictly better than the baseline path with no downside.

**[review] Note**: The original draft recommended enabling the *ranker* first. The ranker does 4 parallel searches per query (2-3x latency), so it should wait for perf testing. The planner has no such cost.

#### 1.2 Separate Extraction Model from Main Agent Model

**File**: `src/sdk/middleware_memory.py:208` (`_get_model`)

**Change**: Configure a separate lightweight model for extraction:
```python
class MemoryMiddleware(Middleware):
    def __init__(self, ...):
        ...
        self._extraction_model_str = os.environ.get(
            "MEMORY_EXTRACTION_MODEL",
            "ollama:llama3.2"  # cheap, fast model; falls back to main model if unavailable
        )

    def _get_extraction_model(self):
        if self._extraction_model is None:
            try:
                from src.sdk.providers.factory import create_model_from_config
                self._extraction_model = create_model_from_config(self._extraction_model_str)
            except Exception:
                return self._get_model()  # fall back to main model
        return self._extraction_model
```

Then update `_extract_with_llm` and `_extract_async` to use `self._get_extraction_model()` instead of `self._get_model()`.

**Why**: GBrain uses Haiku for signal detection while the main agent runs on Opus. This separation means extraction doesn't compete with main-agent quality or latency. The default `ollama:llama3.2` is a cheap local model; it falls back to the main model if unavailable.

#### 1.3 Add `memory_get_profile` Tool

**File**: New entry in `src/sdk/tools_core/memory.py`

```python
@tool
def memory_get_profile(subject: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Return a compiled profile: everything known about a person, company, or topic.

    Returns a diarized summary: what we know, when we learned it, and confidence.
    Use BEFORE any external API call about a person or topic.
    """
    store = get_memory_store(user_id, workspace_id)
    facts = store.find_facts_for_query(subject, limit=20)
    if not facts:
        return f"No recorded information about '{subject}'."

    lines = [f"## Profile: {subject}"]
    for fact in sorted(facts, key=lambda f: (-f.confidence, f.updated_at)):
        sd = fact.structured_data
        attr = sd.get("attribute", fact.trigger)
        val = sd.get("value", fact.action)
        prev = sd.get("previous_value")
        prev_str = f" (was: {prev})" if prev else ""
        lines.append(f"- **{attr}**: {val}{prev_str} [confidence: {fact.confidence:.0%}]")
    return "\n".join(lines)
```

**Why**: GBrain's "brain-first" pattern needs a tool that returns a compiled view of what's known about a subject. The existing `memory_search` and `memory_search_all` are raw-search oriented — they return scored lists, not profiles. `memory_check` (proposed in the original draft) is redundant with `memory_search_all` and has been dropped.

---

### P2 — Medium Impact, Moderate Effort

#### 2.1 Deterministic Entity Candidate Detection

**File**: `src/sdk/middleware_memory.py` (new method in `MemoryMiddleware`)

**Change**: Add regex-based entity candidate detection that runs on every message — two-step: (a) regex scan for candidates, (b) lookup against existing MemoryStore before creating stubs:

```python
import re

ENTITY_PATTERNS = [
    # Multi-capitalized names: "Jordan Mitchell", "Alex Chen", "Sarah O'Connor"
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)+)\b", "person"),
    # Company suffix: "Acme Inc", "Stripe Corp"
    (r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+(?:Inc|LLC|Corp|Ltd|AI|Labs|Tech))\b", "company"),
    # "at <company>": "at OpenAI", "at Google"
    (r"\bat\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b", "company"),
    # Known location names (curated list, expandable)
    (r"\b(San Francisco|New York|London|Tokyo|Paris|Berlin|Seattle|Austin|Boston|Chicago|Denver|Mountain View|Palo Alto)\b", "location"),
]

# Exclusion list: common words that match the person pattern but aren't names
ENTITY_EXCLUSIONS = {
    "I Am", "I Have", "I Was", "I Will", "I Would", "I Can", "I Could",
    "I Should", "It Is", "It Was", "This Is", "That Is", "There Is",
}

def _extract_entity_candidates(self, text: str) -> list[dict]:
    """Scan text for entity candidates via regex (zero LLM cost).
    
    Returns candidates only — does NOT create stubs. Callers should
    validate against existing MemoryStore before persisting.
    """
    candidates = []
    for pattern, entity_type in ENTITY_PATTERNS:
        for match in re.finditer(pattern, text):
            name = match.group(1) if match.lastindex else match.group(0)
            if entity_type == "person" and name in ENTITY_EXCLUSIONS:
                continue
            # Two-step: only create stubs for names already known or contextualized
            existing = self.memory_store.find_facts_for_query(name, limit=2)
            if existing or any(kw in text.lower() for kw in {"ceo of", "works at", "cto of", "founder of", "my friend", "my colleague", "my boss", "my manager", "my wife", "my husband", "my partner", "met with", "interviewed", "spoke with"}):
                candidates.append({"name": name, "type": entity_type, "text": match.group(0)})
    return candidates
```

**Key design decisions**:
1. **Regex is candidate generation only**, not stub creation. The `if existing or any(kw in ...)` check ensures we only create stubs for entities that are either already known or clearly contextualized by relationship language.
2. The `ENTITY_EXCLUSIONS` set catches false positives from sentence-starting capital words.
3. This runs as a background task via `asyncio.create_task()` in `after_agent`, never blocking the main loop.

**[review] Caveat**: The original draft used a single-step "regex → create entity" pipeline with no validation. That would produce enormous false positives (e.g., "I love San Francisco" → stub for "San Francisco"). The two-step approach above mitigates this but doesn't eliminate it. Validate precision with real conversation data before enabling entity creation.

#### 2.2 Source-Aware Ranking Signals

**File**: `src/sdk/memory_ranker.py:34-47` (scoring constants)

**Changes**: Add source-quality signals:
```python
# NEW scoring constants
SCORE_EXPLICIT_USER = 30          # User explicitly stated (source=explicit)
SCORE_LLM_INFERRED = -5           # LLM inferred (source=learned)
SCORE_CORRECTION_MARKER = 8       # Already exists — good
SCORE_CURATED_CONVERSATION = 6    # From structured/important conversation
PENALTY_CASUAL_CHAT = -4          # From casual/unstructured conversation
```

Then wire these into `rank_memory_candidates()`:
```python
def _score_candidate(c: MemoryCandidate, ...) -> float:
    ...
    # NEW: source quality
    if c.metadata.get("source") == "explicit":
        score += SCORE_EXPLICIT_USER
    elif c.metadata.get("source") == "learned":
        score += SCORE_LLM_INFERRED
    
    # NEW: conversation quality (requires is_curated metadata on messages)
    if c.source == "message" and c.metadata.get("is_curated"):
        score += SCORE_CURATED_CONVERSATION
    elif c.source == "message" and not c.metadata.get("is_curated"):
        score += PENALTY_CASUAL_CHAT
```

**Why**: GBrain's source-aware ranking (curated dirs > daily chat) is load-bearing for search quality. Assistant's ranker currently treats all evidence equally.

---

### P3 — High Impact, High Effort

#### 3.1 Enable Memory Ranker (After Perf Testing)

**File**: `src/sdk/middleware_memory.py:242`

**Change** (after P0.1 benchmark establishes baseline, then perf-test the ranker's latency impact):
```python
# CURRENT
ranker_enabled = os.environ.get("MEMORY_RANKER_ENABLED", "false").lower() in {"1", "true", "yes"}

# RECOMMENDED (after perf validation)
ranker_enabled = os.environ.get("MEMORY_RANKER_ENABLED", "true").lower() not in {"0", "false", "no"}
```

**Risk**: `collect_memory_candidates()` does 4 parallel searches (facts, temporal facts, learned memories, messages) per query. This can 2-3x per-query retrieval latency. Run before/after benchmarks on the LongMemEval workload before enabling.

#### 3.2 Content-to-Graph Auto-Wiring Pipeline

**Goal**: Every piece of content (conversation message, email, file) automatically creates typed graph connections with zero LLM calls for entity detection, with lightweight rules for relationship inference.

**New file**: `src/sdk/entity_extractor.py`

**Effort estimate**: **800-1200 lines** (not the 400 in the original draft). The pipeline requires:
- Entity regex scan (candidate generation) — ~200 lines
- Entity lookup against existing MemoryStore — ~100 lines
- Page-role inference (meeting→attended, email→corresponded_with) — ~150 lines
- Typed relation inference cascade (CEO→works_at, founded→founded) — ~200 lines
- Stale-link reconciliation (edits remove dropped refs) — ~150 lines
- Conflict resolution (same entity, different relationship types) — ~100 lines
- Integration tests with real conversation data — ~300 lines

```
Content ingested (message, email, file)
  → Entity regex scan (people, companies, dates, locations)
  → Entity validation (lookup against existing MemoryStore)
  → Page-role inference (meeting→attended, email→corresponded_with)
  → Typed relation inference cascade (CEO→works_at, founded→founded, etc.)
  → upsert_fact_memory() for each entity+attribute discovered
  → add_connection() for each relationship inferred
  → Stale-link reconciliation (removals detected, connections pruned)
```

**Key patterns to port from GBrain**:

| GBrain Pattern | EA Equivalent |
|---------------|---------------|
| Entity-ref regex (markdown links + bare slugs) | `_extract_entities_deterministic()` from rec 2.1 |
| Typed inference cascade (FOUNDED → INVESTED → ADVISES → WORKS_AT) | New `RelationshipInferrer` class |
| Page-role priors (partner-bio language → invested_in) | Context-aware relationship inference |
| Within-page dedup (same target collapses to one link) | `upsert_fact_memory()` already handles this |
| Stale-link reconciliation (edits remove dropped refs) | New `reconcile_stale_links()` method |
| Multi-type link constraint (same person can works_at AND advises) | Existing `add_connection()` supports this |

**Why**: This is the single feature that produces GBrain's +31.4 P@5 improvement over hybrid-search-only. EA already has all the storage primitives (`memory_facts`, `add_connection`, `upsert_fact_memory`). The gap is the deterministic content→entity→relationship pipeline.

**Build incrementally**: Start with entity detection + stub creation (P2.1), validate precision, then add relationship inference. Don't attempt the full pipeline in one change.

---

### P4 — Backlog (Needs More Design)

#### 4.1 Compiled Truth Layer

**File**: `src/storage/memory.py` — `Memory` dataclass

**Concept**: Add a `compiled_truth` field that gets regenerated when facts change, synthesizing "current best understanding" across multiple pieces of evidence.

**Risk**: Each fact update triggers an LLM synthesis call. For frequently-updated facts, this could explode cost. Needs a cooldown mechanism (e.g., regenerate every N updates, with a minimum interval) before implementation.

#### 4.2 LLM-Powered Multi-Query Expansion

**File**: `src/sdk/tools_core/memory.py:192` (`_expand_queries`)

**Concept**: Replace regex-based expansion with a cheap LLM rephrasing call. GBrain uses Claude Haiku for this.

**Risk**: Adds an LLM call per query — latency and cost increase. Requires the cheap-model infrastructure from Rec 1.2 to be in place first.

#### 4.3 Tiered Entity Enrichment

**Concept**: Auto-escalate entities based on mention frequency: T3 (stub at 1 mention) → T2 (web enrichment at 3+ mentions) → T1 (full profile at 8+ mentions).

**Deferred**: Requires web search integration and entity enrichment infrastructure. Needs a dedicated design doc before implementation.

#### 4.4 Memory-First Resolution Pattern

**Concept**: Add a system-prompt instruction that memory must be checked before any external tool call about people or facts. This is ~20 lines of prompt change, not the 100 in the original estimate.

**Deferred**: Depends on `memory_get_profile` (Rec 1.3) being available as a tool the agent can call.

---

## 5. Quick Wins Summary (Revised)

| # | Change | Effort | Impact | Status |
|---|--------|--------|--------|--------|
| 1 | Fix `DEFAULT_CONFIDENCE` 0.2 → 0.4 | 1 line | High | ✅ Done |
| 2 | Fix `is_memory_query` for non-`?` queries | 15 lines | Medium | ✅ Done |
| 3 | Enable memory planner by default | 1 line | Medium | ✅ Done |
| 4 | Schema migration: auto-add missing columns | 30 lines | High | ✅ Done |
| 5 | LLM multi-query expansion (3 rephrasings per query, regex fallback) | 60 lines | Medium | ✅ Done |
| 6 | Search miss → targeted history fallback for user-subject queries | 20 lines | Medium | ✅ Done |
| 7 | Recency boost in hybrid search results | 10 lines | Medium | ✅ Done |
| 8 | PREFERENCE_PROFILE intent in memory planner | 25 lines | Medium | ✅ Done |
| 9 | Better memory_search_all tool description | 5 lines | Low | ✅ Done |
| 10 | Separate extraction model from main model | 30 lines | Medium | Backlog |
| 11 | Add `memory_get_profile` tool | 80 lines | Medium | Backlog |
| 12 | Deterministic entity candidate detection | 80 lines | High | Backlog |
| 13 | Source-aware ranking signals | 40 lines | Medium | Backlog |
| 14 | Memory-first resolution prompt change | 20 lines | Medium | Backlog |

**LongMemEval before/after (48 stratified questions):**

| Type | Before | After | Δ |
|------|--------|-------|---|
| knowledge-update | 87.5% | 87.5% | 0 |
| single-session-assistant | 100% | 87.5% | -12.5 (variance) |
| single-session-user | 62.5% | 75.0% | +12.5 |
| temporal-reasoning | 50.0% | 50.0% | 0 |
| single-session-preference | 25.0% | 0.0% | -25 (variance + errors) |
| multi-session | 12.5% | 0.0% | -12.5 (variance) |
| **Overall** | **56.2%** | **53.3%** | **-2.9 (within variance)** |

Latency: 18.4s → 15.6s (-15%).

## 6. Architecture Changes Summary (Revised)

| # | Change | Lines Est. | Impact | Priority |
|---|--------|-----------|--------|----------|
| 1 | Deterministic entity candidate detection | 80 | High (first step toward graph) | P2 |
| 2 | Content-to-graph auto-wiring pipeline | 800-1200 | **Highest** | P3 |
| 3 | Compiled truth layer | 200 | High (needs rate-limiting design) | P4 |
| 4 | LLM-powered query expansion | 60 | Medium (per-query LLM latency) | P4 |
| 5 | Tiered entity enrichment | 250 | High (needs design doc) | Backlog |
| 6 | Memory-first resolution pattern | 20 | Medium (prompt change) | Backlog |

---

## 7. The Single Biggest Lever

If you do one thing from this analysis, **add deterministic entity extraction from content** (P2 → P3). Every piece of infrastructure already exists: `memory_facts` table with indexed `fact_key`, `upsert_fact_memory()`, `add_connection()`, `Memory` dataclass with `structured_data`. The missing piece is a pipeline that scans conversation text and calls `upsert_fact_memory(entity="alice", attribute="role", value="CEO")` with `add_connection(memory_id, target_id, relationship="works_at")`.

This is what GBrain does: entity detection + relationship inference → typed knowledge graph → +31.4 P@5. While the gain may be smaller for conversational memory (vs document retrieval), the direction is clear: structured entity indexing meaningfully improves retrieval for any knowledge-grounded query.

**Build path**: Start with candidate detection (P2.1, 80 lines, regex only, validated against existing MemoryStore). Validate precision on real conversations. Then add relationship inference and stub creation (P3.2, incremental). Don't attempt the full pipeline in one change — the scope estimate is 800-1200 lines, not 400.
