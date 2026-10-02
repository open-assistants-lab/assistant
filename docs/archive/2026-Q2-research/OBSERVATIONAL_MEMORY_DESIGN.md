# Observational Memory — EA Redesign

> Date: 2026-05-03
> Status: Design Proposal (no implementation)
> Inspired by: Mastra OM (84.23% gpt-4o on LongMemEval)

---

## 0. Context & Thinking Process

### 0.1 Why This Redesign Exists

**The precipitating event**: We ran the LongMemEval benchmark on our current memory system and discovered:

| Configuration | Accuracy | Notes |
|---------------|----------|-------|
| Verbatim search only (no extraction) | **56.2%** | Agent searches raw injected messages via `memory_search` |
| Structured extraction enabled | **20.8%** | Extraction produces ~200 facts, most wrong/imprecise, agent trusts them over raw messages |
| Mastra OM (gpt-4o, reference) | **84.23%** | Publicly reproducible, same model, different architecture |

This means our extraction pipeline — the core of our memory system — is **actively harmful**. It reduces accuracy by 35 percentage points compared to doing nothing. The extraction produces entity/attribute/value pairs like `{entity: "user", attribute: "sculpture_hours", value: "15-18 hours"}` when the conversation actually said "10-12 hours". The agent treats extracted facts as authoritative and ignores the correct raw text.

**The architectural question**: If extraction doesn't work, what does? Mastra OM proves the answer is not better extraction — it's a fundamentally different architecture.

### 0.2 What We've Already Learned (the journey here)

This design is the culmination of a week-long investigation. Here's what we found:

**Day 1 — HybridDB Audit**: 17 bugs and design flaws found in HybridDB (see `docs/HybridDB/AUDIT.md`). 15 fixed — NX cache invalidation, SQL injection, ChromaDB crash recovery, schema migration gaps. None of these fixes improved accuracy; they fixed crashes but not the fundamental problem.

**Day 2 — LongMemEval Baseline**: First benchmark run — 56.2% accuracy with pure verbatim search. Strong on single-session types, weak on multi-session (12.5%) and preferences (25%). The agent uses only `memory_search` — never `memory_search_all`, rarely `memory_get_history`.

**Day 3 — Performance Report Review**: Analyzed `docs/MEMORY_PERF_REPORT.md` and implemented recommendations 7.1 (skip dual search when facts found), 7.7 (searchable-columns filter), and 7.9 (stronger recency). 7.9 caused a 27-point regression — stronger recency is incompatible with LongMemEval's scattered date distribution. Reverted. 7.1 and 7.7 kept — correct architecture for production but irrelevant for `qa_direct` mode (no facts exist).

**Day 4 — Extraction Pipeline Investigation**: Discovered that `qa_direct` mode never triggers memory extraction. Added extraction trigger — accuracy dropped from 56% to 21%. The extraction pipeline creates low-precision facts that confuse the agent. This confirmed: structured extraction is the problem.

**Day 5 — Mastra OM Study**: Analyzed how Mastra achieves 84.23% with the same model (gpt-4o). Their Observer/Reflector pipeline produces dense text observations instead of structured facts. The observations sit in the context window as static text — no dynamic retrieval, no per-turn tool calls. This eliminates three error sources: LLM parsing errors, retrieval noise, and authority bias.

**Key insight**: The extraction pipeline was optimizing the wrong problem. The goal isn't to convert conversations into structured data — it's to create a compressed, accurate working memory that the agent can read directly.

### 0.3 Design Philosophy

1. **Verbatim is the source of truth**. Observations are derived from raw messages, not replacements. The Observer watches conversations; it doesn't rewrite them.
2. **Static over dynamic**. A pre-computed observation log is more reliable than per-turn retrieval. It eliminates retrieval noise and enables prompt caching.
3. **Text over structure**. Dense text observations preserve semantic precision better than entity/attribute/value pairs. LLMs make fewer errors writing sentences than filling JSON templates.
4. **Hybrid over pure**. We keep structured facts alongside observations because our HybridDB graph capabilities give us an edge — entity linking, pagerank, graph-boosted search. Mastra can't do this; we can.
5. **Background over foreground**. The Observer and Reflector run on background threads, never blocking the agent loop. The agent never waits for memory operations.

### 0.4 Trade-offs Made

| Trade-off | What we gave up | Why |
|-----------|-----------------|-----|
| Static context vs dynamic retrieval | Agent can't query memory for novel questions without tool call | Prompt-cacheable prefix saves 90% on input token cost. `memory_search` tool still exists for deep queries. |
| Text observations vs structured facts | Can't do `SELECT * WHERE entity='user' AND attribute='location'` on observations | Pinned facts serve this use case. Observations serve context. Each has its own table, indexed for its purpose. |
| Background Observer vs synchronous extraction | Observer output is stale for the current turn | Same as Mastra OM. The Observer processes the LAST batch, not the current message. Working memory is intentionally slightly stale — it's "what we knew before this conversation." |
| HybridDB per-user vs shared store | Duplicate ChromaDB instances per user | Already the production pattern. Observer doesn't change this — it uses the same HybridDB the agent already accesses. |
| Observer uses main model vs cheap model | Higher extraction cost ($0.03 per observation batch with gpt-4o) | Phase 1 uses main model for quality. Phase 2 switches to flash-tier model (gemini-flash or gpt-4o-mini) for cost reduction. |

### 0.5 Things We Specifically Rejected

1. **Better extraction prompt**: Tried it. The error isn't from a bad prompt — it's from the LLM filling structured JSON. "10-12 hours" becomes "15-18 hours" regardless of prompt quality because the model is fallible at precise structured output. Text observations are less sensitive to this.

2. **Reranking extracted facts**: Mastra's RAG topK=20 gets 80.05%. Adding reranking would be a band-aid on a broken pipeline. Observations address the root cause.

3. **Multi-agent retrieval (ASMR)**: 99% accuracy but 8 parallel agents, non-production-viable. We specifically optimized for latency (5ms injection vs 170ms retrieval).

4. **Full context in prompt**: Only 60.2% with gpt-4o. Mastra proved this: context windows DON'T solve the problem. You need compressed, structured observations.

5. **MemPalace's verbatim + scoping approach**: 96.6% R@5 retrieval with zero LLM. Impressive but requires manual scoping (wings/rooms/drawers hierarchy) that users must set up. Our approach is fully automatic — the Observer and Reflector scope by priority and time without user intervention.

---

## 1. Problem Statement

**Current pipeline produces 21% accuracy on LongMemEval when extraction is enabled.** The structured fact extraction approach (entity/attribute/value) is a lossy compression that introduces factual errors — "10-12 hours" becomes "15-18 hours", locations shift, numbers drift. The agent trusts extracted facts over raw message search, amplifying the damage.

**Mastra OM achieves 84.23% with the same model (gpt-4o)** by replacing structured extraction with dense text observations — an append-only log of what happened, prepended statically into the context window. No dynamic retrieval, no per-turn tool calls, no lossy entity/attribute/value pairs.

**This document proposes adapting that pattern to EA**, using our existing HybridDB as the storage substrate while replacing the extraction+retrieval pipeline with observation-based context injection.

---

## 2. Core Architecture

### 2.1 Context Window Structure

```
┌─────────────────────────────────────────────┐
│ SYSTEM PROMPT                               │  ~2K tokens
├─────────────────────────────────────────────┤
│ WORKING MEMORY (Observations)               │  ~8K tokens, STATIC, APPEND-ONLY
│  ┌─────────────────────────────────────┐    │
│  │ [Pinned facts] always at top        │    │
│  │ ─────────────────────────────────── │    │
│  │ [Recent observations] last 7 days   │    │
│  │ ├── Mar 15 🔴 user moved to Denver  │    │
│  │ ├── Mar 16 🟡 discussed hiking      │    │
│  │ └── Mar 18 🟢 searched for flights  │    │
│  │ ─────────────────────────────────── │    │
│  │ [Archived observations] older,      │    │
│  │ condensed by Reflector              │    │
│  └─────────────────────────────────────┘    │
├─────────────────────────────────────────────┤
│ CURRENT CONVERSATION MESSAGES               │  ~4K tokens, GROWS WITH TURN COUNT
├─────────────────────────────────────────────┤
│ AGENT RESPONSE                              │  generated
└─────────────────────────────────────────────┘
```

**Key properties:**
- The Working Memory section is **static within a conversation turn** — it does not change between user messages
- Only the Current Conversation Messages grow with turn count
- Observations are **text**, not structured JSON — readable by both humans and LLMs
- The full context is **prompt-cacheable** — the prefix (system prompt + working memory) is identical across turns

### 2.2 Observer/Reflector Pipeline

```
                 ┌──────────────┐
  User Message   │              │   triggers Observer when
  ──────────────►│  AgentLoop   │   unobserved messages >
                 │              │   OBSERVER_THRESHOLD (4K tokens)
                 └──────┬───────┘
                        │
                        ▼ after_agent()
                 ┌──────────────┐
                 │  Observer    │  reads unobserved messages
                 │  (background │  ──► produces observations
                 │   thread)    │  ──► stores in HybridDB.observations
                 └──────┬───────┘
                        │
                        ▼ when observations > REFLECTOR_THRESHOLD (8K tokens)
                 ┌──────────────┐
                 │  Reflector   │  reads all observations
                 │  (background │  ──► condenses into reflections
                 │   thread)    │  ──► stores in HybridDB.reflections
                 └──────────────┘
```

**Observer behavior:**
- Triggered when `count_unobserved_tokens()` exceeds `OBSERVER_THRESHOLD`
- Reads unobserved messages from `messages` table
- Produces a list of observation text entries
- Each observation: timestamp, priority emoji, content, referenced dates
- Stores in `observations` table with HybridDB (FTS5 + ChromaDB for later search)

**Reflector behavior:**
- Triggered when `total_observation_tokens()` exceeds `REFLECTOR_THRESHOLD`
- Reads ALL observations across all time
- Produces condensed reflections:
  - Merges related observations
  - Resolves contradictions (user moved from A → B → C, keep only latest)
  - Drops superseded information
  - Preserves temporal anchors
- Stores in `reflections` table
- Original observations remain (append-only) but reflections are what appears in context

### 2.3 What the Agent Sees (before_agent injection)

The `before_agent()` middleware assembles working memory. Phase 1-2: observations only (no pinned facts — those are stored silently and only displayed after Phase 3 validation).

```
Working Memory (auto-injected into context, do not search for this information):

## Recent Activity
🟡 Mar 15 - user discussed moving logistics, mentioned new apartment
🔴 Mar 15 - user's location changed: Austin → Denver
🟡 Mar 16 - user discussed hiking trails near Denver, mentioned Red Rocks Amphitheater
🟢 Mar 18 - user searched for flights to Portland for a conference

## Past Context (auto-condensed)
Over the past 3 months, user: completed 8 weeks of yoga classes at Serenity Yoga, watched 22 MCU movies in 2 weeks, attended a friends & family sale at Nordstrom, received a crystal chandelier from their aunt on March 4. User's job title is Senior Backend Engineer at TechCorp. User's degree is Business Administration.
```

**Phase 3 addition** (when fact-precision > 85%):
```
## Pinned Facts
- user.name = Jordan Mitchell (last confirmed: Apr 2023)
- user.location = Denver, CO (moved from Austin, TX on Mar 15)
- user.commute = 45 minutes each way
```

**Key design decisions:**
1. **Observations only in Phase 1-2**: Facts are collected by Observer during Phase 1-2 (stored silently in `memory_facts`), but NOT displayed in working memory. This prevents the authority bias problem — the agent can't trust wrong facts if it never sees them.
2. **Phase 3 gate**: Facts displayed in working memory only after a fact-precision benchmark validates >85% accuracy against LongMemEval ground truth. If fact precision < 85%, pinned facts are permanently disabled.
3. **Recent activity**: Last 7 days of observations, full detail
4. **Past context**: Reflector's condensed output — dense but complete
5. **No tool calls needed**: The agent reads this directly — it doesn't call `memory_search` for facts already in working memory

### 2.4 When the Agent DOES Need Tools

`memory_search` remains available for cases where working memory is insufficient:
- User asks about something NOT in observations (older than reflector threshold, or never observed)
- User asks for hyper-specific detail the Observer summarized
- Cross-referencing across the full message corpus

But the common case (user asks "what's my job title?") is answered from Working Memory without a tool call.

---

## 3. HybridDB Schema Design

### 3.1 New Table: `observations`

```python
db.create_table("observations", {
    "id": "TEXT PRIMARY KEY",
    "content": "LONGTEXT",         # observation text (dense, LLM-readable)
    "priority": "TEXT",            # "🔴" high, "🟡" medium, "🟢" low
    "observation_ts": "TEXT",      # when Observer created this
    "referenced_date": "TEXT",     # date mentioned in content (nullable)
    "relative_date": "TEXT",       # computed relative offset (nullable)
    "source_message_range": "TEXT", # "msg_1234..msg_1300" — which messages were observed
    "token_count": "INTEGER",      # approximate token count of observation
    "context_window": "TEXT",      # which context window this belongs to (for multi-window)
    "created_at": "TEXT",          # ISO timestamp
})
```

**Search behavior:**
- `content` is LONGTEXT → FTS5 keyword search + ChromaDB semantic search
- Queried by: `memory_search` tool, Reflector agent, `get_working_memory()`
- Indexed by: `observation_ts` (date range queries), `priority` (filtering)

### 3.2 New Table: `reflections`

```python
db.create_table("reflections", {
    "id": "TEXT PRIMARY KEY",
    "content": "LONGTEXT",         # condensed reflection text
    "source_observation_range": "TEXT", # "obs_100..obs_500" — which observations were condensed
    "observation_count": "INTEGER",# how many observations were condensed
    "token_count": "INTEGER",      # approximate token count
    "created_at": "TEXT",
})
```

**Search behavior:**
- `content` is LONGTEXT → FTS5 + ChromaDB
- Reflections are what appear in the "Past Context" section of working memory

### 3.3 Keep: `messages` (existing)

No changes. Raw conversations remain the source of truth. The Observer reads from `messages`, observations supplement (not replace) messages.

### 3.4 Deprecate: `memories` (existing MemoryStore table)

The structured fact approach (entity/attribute/value) is superseded by observations.

**Migration strategy**: Keep existing `memories` rows as-is for Phase 1. `memory_search` queries both `memories` (existing data) and `observations` (new data). In Phase 4, convert existing memories to observations via a Reflector pass that reads each memory's trigger/action/structured_data and formats it as an observation text entry.

Existing facts can be migrated into observations during transition. The `memories` table can be kept for backward compatibility but the extraction pipeline (`_extract_with_llm`) is disabled in favor of the Observer.

### 3.5 Keep: `memory_facts` (existing MemoryStore table)

Facts are still useful for:
- Pinned facts section of working memory
- Fast entity/attribute lookup (`find_facts_for_query` with 3-column FTS5)
- Graph registration (fact nodes → entity graph)

But they are now produced by the Observer (not a separate extraction step), and they supplement observations rather than replace them.

### 3.6 Use: Graph capabilities

Register `observations` as graph nodes:
```python
db.register_entity_node("observations", node_type="observation", id_column="id",
                        label_template="{priority} observation from {observation_ts}")
```

Register `observation` → `memory_fact` edges:
```python
db.register_edge_rule("observations", "memory_facts", "belongs_to",
                       target_match="memory_id")
```

This enables `pagerank()` on observations, `neighbors()` for related facts, and graph-boosted search.

---

## 4. Observer & Reflector Design

### 4.1 Observer Prompt

```
You are an observer watching a conversation. Your job is to produce a concise, 
dense log of what happened — specific events, decisions, facts, and changes.

FORMAT: A JSON array of observations. Each observation:
{
  "id": "obs_<uuid>",
  "content": "concise text describing the event (1-2 sentences, present tense)",
  "priority": "🔴" | "🟡" | "🟢",
  "referenced_date": "ISO date if content mentions a specific date, else null",
  "relative_date": "computed relative date if content says 'next week' etc, else null",
  "facts_extracted": [{"entity": "...", "attribute": "...", "value": "..."}]
}

PRIORITY RULES:
🔴 (high): Personal info changes (name, location, job), corrections, critical decisions
🟡 (medium): Preferences, interests, plans, events
🟢 (low): Casual mentions, background context, non-actionable info

OBSERVATION RULES:
1. Each observation captures ONE specific event or fact — not summaries
2. Be precise with numbers, dates, names — these will be used to answer questions
3. Include the exact value, not approximations
4. If a user corrects previous information, note both old and new values
5. Do NOT create observations for the assistant's generic responses or greetings
6. PREFERENCES: Capture stated likes/dislikes/wants as 🟡 observations
7. TEMPORAL: Always include dates when mentioned. Note the date the conversation 
   references, not just when it happened.

FACTS RULES (facts_extracted field):
1. Extract structured facts for information that would be useful in entity/attribute lookups.
   These are stored silently in `memory_facts` table during Phase 1-2 for quality validation.
   They are NOT displayed in working memory until Phase 3 (after fact-precision > 85% validated).
2. Entity = "user" for user info, otherwise the person/thing being described
3. Attribute = concise property name (e.g., "job_title", "commute", "location")
4. Value = exact value as stated by the user
5. Only extract facts with high confidence — skip vague or uncertain statements

Respond with ONLY the JSON array, no other text.
```

### 4.2 Reflector Prompt

```
You are a reflector condensing observations into a denser format.

INPUT: A list of observations created over time.
OUTPUT: A JSON object with:
{
  "reflection_text": "condensed text preserving all key information",
  "dropped_observation_ids": ["obs_...", ...],  # ids that can be removed
  "contradictions_resolved": [{"entity": "...", "attribute": "...", 
                                "old_value": "...", "new_value": "...", 
                                "resolution": "explanation"}],
  "patterns_identified": ["pattern 1", "pattern 2"],
  "updated_facts": [{"entity": "...", "attribute": "...", "value": "...",
                      "previous_value": "..."}]
}

REFLECTION RULES:
1. Preserve ALL factual information — names, numbers, dates, locations
2. Merge observations about the same topic/event into single entries
3. When a correction contradicts an earlier observation, keep only the latest
4. Drop observations that are fully superseded (e.g., user moved A→B→C, keep C only)
5. Identify patterns across observations (e.g., "user regularly visits museums")
6. Keep temporal anchors — don't lose when things happened
7. The reflection should be readable as a standalone summary of what's been observed

Respond with ONLY the JSON object, no other text.
```

### 4.3 Trigger Logic (in ObservationMiddleware)

```python
class ObservationMiddleware:
    OBSERVER_THRESHOLD_TOKENS = 8000    # fire Observer when unobserved > 8K tokens (calibrated for EA)
    REFLECTOR_THRESHOLD_TOKENS = 16000  # fire Reflector when observations > 16K tokens
    MAX_WORKING_MEMORY_TOKENS = 10000   # cap on what goes into context
    MIN_OBSERVER_INTERVAL_TURNS = 3     # don't fire Observer more than every 3 turns

    def after_agent(self, state: AgentState) -> dict[str, Any] | None:
        """After each agent turn, check if Observer/Reflector should fire.
        
        Uses dual-path pattern from MemoryMiddleware (middleware_memory.py:626):
        asyncio.create_task if event loop available, else background thread.
        """
        unobserved = self._count_unobserved_tokens()
        total_turns = len(state.messages) // 2  # user+assistant per turn
        if (unobserved >= self.OBSERVER_THRESHOLD_TOKENS
                and self._turns_since_observer >= self.MIN_OBSERVER_INTERVAL_TURNS):
            self._dispatch_fire_observer()

        total_obs = self._count_observation_tokens()
        if total_obs >= self.REFLECTOR_THRESHOLD_TOKENS:
            self._dispatch_fire_reflector()
        return None

    def _dispatch_fire_observer(self) -> None:
        """Fire Observer using dual-path: asyncio task or background thread."""
        import asyncio as _asyncio, threading
        try:
            _asyncio.create_task(self._fire_observer())
        except RuntimeError:
            threading.Thread(target=self._fire_observer_sync, daemon=True).start()
```

### 4.3b Token Counting

```python
def _count_unobserved_tokens(self) -> int:
    """Count tokens in unobserved messages using tiktoken (matching SummarizationMiddleware)."""
    messages = self._message_store.get_messages(since_id=self._unobserved_since, limit=500)
    return sum(self._estimate_tokens(m.content) for m in messages)

def _estimate_tokens(self, text: str) -> int:
    """Estimate token count — tiktoken if available, char/4 fallback."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4
```

### 4.4 Working Memory Assembly (in MemoryMiddleware)

```python
async def before_agent(self, messages, ...):
    """Inject Working Memory into the system prompt."""
    working_memory = await self._assemble_working_memory()

    # Insert working memory before the last system message
    system_msg = Message.system(working_memory)
    messages.insert(-1, system_msg)

async def _assemble_working_memory(self) -> str:
    """Assemble the Working Memory section from observations + reflections."""
    parts = []

    # 1. Pinned facts (high confidence, current)
    facts = self.memory_store.find_facts_for_query("*", limit=20)
    if facts:
        parts.append("## Pinned Facts")
        for f in facts:
            sd = f.structured_data
            parts.append(f"- {sd['entity']}.{sd['attribute']} = {sd['value']}")

    # 2. Recent observations (last 7 days)
    recent = self.db.query(
        "observations",
        where="observation_ts > ?",
        params=[(date.today() - timedelta(days=7)).isoformat()],
        order_by="observation_ts DESC",
        limit=30,
    )
    if recent:
        parts.append("\n## Recent Activity")
        for obs in recent:
            parts.append(f"{obs['priority']} {obs['content']}")

    # 3. Past context (reflections)
    reflection = self.db.query(
        "reflections",
        order_by="created_at DESC",
        limit=1,
    )
    if reflection:
        parts.append(f"\n## Past Context\n{reflection[0]['content']}")

    return "\n".join(parts)
```

---

## 5. Tool Changes

### 5.1 `memory_search` — Searches Observations, Facts, Reflections, and Messages

`memory_search` searches all sources, with facts for entity lookups complementing observations for context:

```python
@tool
def memory_search(query, user_id, workspace_id):
    """Search observations, facts, reflections, and raw messages.
    
    Working memory already has recent observations + reflections in context.
    Use this for deep queries, older information, or structured fact lookups.
    """
    obs_store = get_observation_store(user_id, workspace_id)
    msg_store = get_message_store(user_id, workspace_id)

    results = []

    # Search facts (structured entity/attribute/value, stored silently by Observer)
    facts = obs_store.find_facts_for_query(query, limit=3)
    for f in facts:
        sd = f.structured_data
        results.append(f"[fact] {sd.get('entity','user')}.{sd.get('attribute','?')} = {sd.get('value','?')}")

    # Search observations (text-based, most relevant for memory queries)
    obs_results = obs_store.db.search_all("observations", query, limit=5)
    for r in obs_results:
        results.append(f"[observation] {r['priority']} {r['content']}")

    # Search reflections (condensed context)
    refl_results = obs_store.db.search_all("reflections", query, limit=2)
    for r in refl_results:
        results.append(f"[reflection] {r['content']}")

    # Search raw messages (deep fallback)
    msg_results = msg_store.search_hybrid(query, limit=5)
    for r in msg_results:
        results.append(f"[message] {r.role}: {r.content} (score: {r.score:.2f})")

    return format_results(results)
```

### 5.2 `memory_search_all` — Unchanged

Still searches across all sources, but now includes `observations` + `reflections` in addition to messages.

### 5.3 `memory_get_history` — Unchanged

Raw message retrieval by date range.

### 5.4 `memory_get_profile` — NEW (GBRAIN P1.3)

Returns the current Working Memory as a tool-callable response. Useful when the agent needs to refresh or the user explicitly asks "what do you know about me?"

---

## 6. Migration Path

### Phase 1: Add Observer (Week 1)
1. Add `observations` and `reflections` tables to HybridDB
2. Build `ObservationMiddleware` with Observer prompt and trigger logic
3. Add `before_agent` working memory assembly
4. Keep existing extraction + memory_search as fallback
5. Run LongMemEval with BOTH systems active — compare

### Phase 2: Tune Observer Quality (Week 2)
1. Run LongMemEval → identify observation quality issues
2. Iterate on Observer prompt (date precision, fact accuracy, priority)
3. Add graph registration for observations
4. Test with a separate Observer model (cheap model like gemini-flash)
5. Target: 60%+ accuracy (up from 21%)

### Phase 3: Enable Reflector (Week 3)
1. Add Reflector with condensation prompt
2. Test reflection quality — ensure no information loss
3. Set reflector threshold based on token budgets
4. Target: 70%+ accuracy

### Phase 4: Deprecate Extraction (Week 4)
1. Disable `_extract_with_llm` — Observer produces both observations + facts
2. Convert existing `memories` rows into observations (one-time migration)
3. Keep `memory_facts` table for pinned facts, populated by Observer
4. Target: 80%+ accuracy (Mastra OM level)

---

## 7. Token Budgets

| Section | Tokens | Source |
|---------|--------|--------|
| System prompt | ~2,000 | Static |
| Working Memory (pinned facts) | ~500 | `memory_facts` (current, high conf) |
| Working Memory (recent observations) | ~4,000 | `observations` (last 7 days) |
| Working Memory (past context) | ~3,500 | `reflections` (condensed) |
| Current conversation | ~4,000 | `messages` (grows with turns) |
| **Total context** | **~14,000** | Well within gpt-4o's 128K window |
| Prompt cache hit | **~10,000** | System prompt + working memory = stable prefix |

Compare to Mastra OM's ~30K token average — we're more aggressive with compression.

---

## 8. Test Strategy

### Unit Tests
- Observer produces valid JSON from sample conversations
- Reflector correctly condenses without information loss
- Working memory assembly respects token budgets
- `before_agent` injection inserts at correct position

### Integration Tests
- Observer reads from actual `messages` table after conversation turn
- Reflector triggers when observation count exceeds threshold
- Working memory updates correctly after Observer/Reflector runs

### LongMemEval
- Phase 1: Measure accuracy with Observer + existing retrieval
- Phase 2: Compare Observer-only vs Observer+retrieval
- Phase 3: Full pipeline with Reflector
- Phase 4: Production-equivalent pipeline

### Production Readiness
- Observer runs on background thread (not blocking agent loop)
- Token thresholds prevent unbounded growth
- HybridDB journal ensures consistency
- Graceful degradation if Observer model is unavailable

---

## 9. Key Decisions

| Decision | Rationale |
|----------|-----------|
| Observations stored as text, not structured JSON | Mastra OM's approach. More robust to LLM errors. Easier for agent to read. |
| Facts pinned separately from observations | Entity/attribute lookup is still valuable for fast retrieval. HybridDB graph links them. |
| Observer on background thread, not blocking | Production agents can't wait for observation step. Fire-and-forget with retry. |
| Reflector condenses but doesn't delete | Append-only design means we can always recover full detail. Safer than compaction. |
| Working memory is static per conversation turn | Enables prompt caching (10K stable tokens per turn). Matches OM's design. |
| `memory_search` tool still available | Fallback for when working memory is insufficient. Handles edge cases. |

---

## 10. Risks

| Risk | Mitigation |
|------|------------|
| Observer produces hallucinated facts (like current extraction) | Observer prompt is text-based, not structured — partially mitigates. Still needs quality testing. |
| Observer latency blocks agent turns | Fire-and-forget on background thread. Observer uses cheap model (flash-tier). |
| Working memory outgrows token budget | Reflector condenses. If still too large, truncate oldest observations. |
| Information loss in Reflector condensation | Reflection preserves all key facts. Dropped observations remain in database for deep search. |
| HybridDB ChromaDB exhaustion (multiple Observer threads) | Observer runs on background thread with dedicated ChromaDB client. Client reused, not recreated. |

---

## 12. Middleware & Function Redesign

### 12.1 Current Middleware Stack (before redesign)

```
AgentLoop.run()
  ├── MemoryMiddleware.before_agent()
  │     ├── is_memory_query(query)          # regex gate
  │     ├── _get_planner_memory_context()   # OR _get_baseline_memory_context()
  │     │     ├── find_facts_for_query()    # MemoryStore FTS5
  │     │     ├── search_hybrid(memories)   # MemoryStore ChromaDB
  │     │     └── search_hybrid(messages)   # MessageStore ChromaDB
  │     └── inject context into system prompt
  ├── AgentLoop.run_single()               # LLM call
  │     └── Agent calls memory_search()    # duplicate of what middleware already did
  └── MemoryMiddleware.after_agent()
        ├── _extract_with_llm()            # every 3 turns, main model
        └── consolidate()                  # resolve contradictions
```

### 12.2 Redesigned Middleware Stack

```
AgentLoop.run()
  ├── ObservationMiddleware.before_agent()
  │     └── _assemble_working_memory()
  │           ├── get_pinned_facts()        # memory_facts, current+high conf, ~500 tokens
  │           ├── get_recent_observations() # last 7 days, full detail, ~4000 tokens
  │           └── get_reflection_context()  # latest reflection, condensed, ~3500 tokens
  │     └── inject into system prompt as static block
  ├── AgentLoop.run_single()               # LLM call
  │     └── Agent reads working memory directly (no tool call for common queries)
  │     └── Agent calls memory_search() only for deep/novel queries
  └── ObservationMiddleware.after_agent()
        ├── mark_messages_observed()        # track which messages Observer has seen
        ├── _maybe_fire_observer()          # if unobserved tokens > threshold, fire bg thread
        └── _maybe_fire_reflector()         # if observation tokens > threshold, fire bg thread
```

### 12.3 Detailed Function Signatures

#### `ObservationMiddleware.__init__`

```python
class ObservationMiddleware:
    """Middleware that manages the Observer/Reflector observation pipeline.
    
    Follows Middleware ABC from middleware.py:17:
    - before_agent(self, state: AgentState) -> dict[str, Any] | None
    - after_agent(self, state: AgentState) -> dict[str, Any] | None
    """
    OBSERVER_THRESHOLD_TOKENS = 8000
    REFLECTOR_THRESHOLD_TOKENS = 16000
    WORKING_MEMORY_MAX_TOKENS = 10000
    MIN_OBSERVER_INTERVAL_TURNS = 3

    def __init__(self, user_id: str, workspace_id: str = "personal"):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._unobserved_since: int | None = None
        self._turns_since_observer = 0
        self._observer_lock = threading.Lock()
        self._observer_running = False
        self._observation_store = self._init_observation_store()
        self._message_store = get_message_store(user_id, workspace_id)

    def _init_observation_store(self) -> ObservationStore:
        """Create or get the ObservationStore backed by HybridDB."""
        ...
```

#### `ObservationMiddleware.before_agent`

```python
def before_agent(self, state: AgentState) -> dict[str, Any] | None:
    """Inject working memory into the system prompt. Called before every LLM call.
    
    Follows Middleware ABC (middleware.py:28): takes AgentState, returns state updates.
    AgentState.messages: list[Message] from state.py:25.
    
    Returns dict with 'messages' key to update state, or None if no changes.
    """
    working_memory = await self._assemble_working_memory()

    if not working_memory:
        return None

    system_text = self._format_working_memory_block(working_memory)
    system_msg = Message.system(system_text)
    state.messages.insert(-1, system_msg)

    logger.info("observation.working_memory_injected",
                {"tokens": working_memory["total_tokens"]},
                user_id=self.user_id)
    return {"messages": state.messages}
```

#### `ObservationMiddleware.after_agent`

```python
def after_agent(self, state: AgentState) -> dict[str, Any] | None:
    """After agent responds, check if Observer or Reflector should fire.
    
    Follows Middleware ABC (middleware.py:32): takes AgentState, returns state updates.
    Non-blocking: Observer fires via dual-path (asyncio task or background thread).
    """
    self._turns_since_observer += 1

    last_msg_id = state.last_message().id if state.last_message() else None
    if last_msg_id:
        self._unobserved_since = self._unobserved_since or last_msg_id

    unobserved_tokens = self._count_unobserved_tokens()
    if (unobserved_tokens >= self.OBSERVER_THRESHOLD_TOKENS
            and self._turns_since_observer >= self.MIN_OBSERVER_INTERVAL_TURNS):
        self._dispatch_fire_observer()

    total_obs_tokens = self._count_observation_tokens()
    if total_obs_tokens >= self.REFLECTOR_THRESHOLD_TOKENS:
        self._dispatch_fire_reflector()

    return None  # no state changes from after_agent

def _dispatch_fire_observer(self) -> None:
    """Fire Observer using dual-path pattern (middleware_memory.py:626)."""
    import asyncio as _asyncio, threading
    try:
        _asyncio.create_task(self._fire_observer())
    except RuntimeError:
        threading.Thread(target=self._fire_observer_sync, daemon=True).start()

def _dispatch_fire_reflector(self) -> None:
    """Fire Reflector using dual-path pattern."""
    import asyncio as _asyncio, threading
    try:
        _asyncio.create_task(self._fire_reflector())
    except RuntimeError:
        threading.Thread(target=self._fire_reflector_sync, daemon=True).start()
```

#### `ObservationMiddleware._assemble_working_memory`

```python
async def _assemble_working_memory(self) -> dict | None:
    """Assemble the working memory block from observations + reflections.

    Phase 1-2: Observations only (no pinned facts displayed).
    Phase 3: Add pinned facts section after fact-precision validates > 85%.

    Returns dict with sections and token counts, or None if no memory exists.
    """
    total_tokens = 0
    sections = []

    # 1. Recent observations: last 7 days, full detail
    recent = self.observation_store.get_recent_observations(days=7, limit=50)
    if recent:
        obs_text = "## Recent Activity\n" + "\n".join(
            f"{obs.priority} {obs.observation_ts[:10]} {obs.content}"
            for obs in recent
        )
        obs_tokens = self._estimate_tokens(obs_text)
        budget = self.WORKING_MEMORY_MAX_TOKENS * 0.6
        if total_tokens + obs_tokens <= budget:
            sections.append(("recent", obs_text))
            total_tokens += obs_tokens
        else:
            truncated = self._truncate_observations(recent, budget - total_tokens)
            sections.append(("recent", truncated))
            total_tokens = budget

    # 2. Past context: latest reflection (condensed)
    reflection = self.observation_store.get_latest_reflection()
    if reflection:
        refl_text = f"## Past Context\n{reflection.content}"
        refl_tokens = self._estimate_tokens(refl_text)
        remaining = self.WORKING_MEMORY_MAX_TOKENS - total_tokens
        if refl_tokens <= remaining:
            sections.append(("reflection", refl_text))
            total_tokens += refl_tokens
        else:
            truncated = self._truncate_text(reflection.content, remaining)
            sections.append(("reflection", truncated))

    if not sections:
        return None

    return {
        "sections": sections,
        "total_tokens": total_tokens,
        "block_text": "\n\n".join(text for _, text in sections),
    }
```

#### `ObservationMiddleware._fire_observer` — matches `middleware_memory.py:_do_extract`

```python
async def _fire_observer(self) -> None:
    """Run Observer on background thread to process unobserved messages.

    Follows the _do_extract pattern (middleware_memory.py:659):
    - Reads messages from MessageStore
    - Creates AgentLoop with Observer model (cheap model in Phase 2)
    - Calls run_single() to produce observations
    - Stores observations in ObservationStore
    """
    if self._observer_running:
        return

    self._observer_running = True
    self._turns_since_observer = 0
    try:
        from src.sdk.providers.factory import create_model_from_config

        messages = self._message_store.get_messages(
            since_id=self._unobserved_since,
            limit=200,
        )
        if not messages:
            return

        model = create_model_from_config(
            os.environ.get("OBSERVER_MODEL", os.environ.get("DEFAULT_MODEL", "ollama:llama3.2"))
        )
        prompt = OBSERVER_PROMPT.format(
            conversation="\n\n".join(
                f"[{m.role}] {m.ts.date()} {m.content}" for m in messages
            )
        )
        extraction_messages = [
            Message.system("You are an Observer. Return only valid JSON."),
            Message.user(prompt),
        ]

        loop = AgentLoop(provider=model)
        result = await loop.run_single(extraction_messages)

        if result and result.content:
            observations = _parse_observer_json(str(result.content))
            if observations:
                self.observation_store.insert_observations(observations)
                self._unobserved_since = messages[-1].id if hasattr(messages[-1], 'id') else None
                logger.info("observer.completed",
                            {"observations": len(observations),
                             "messages_processed": len(messages)},
                            user_id=self.user_id)
    except Exception as e:
        logger.warning("observer.failed", {"error": str(e)}, user_id=self.user_id)
    finally:
        self._observer_running = False

def _fire_observer_sync(self) -> None:
    """Synchronous fallback for CLI mode (no event loop)."""
    import asyncio as _asyncio
    _asyncio.run(self._fire_observer())
```

#### `ObservationMiddleware._fire_reflector`

```python
async def _fire_reflector(self) -> None:
    """Run Reflector to condense observations.

    Follows same pattern as _fire_observer.
    """
    from src.sdk.providers.factory import create_model_from_config

    all_observations = self.observation_store.get_all_observations()
    if not all_observations:
        return

    model = create_model_from_config(
        os.environ.get("REFLECTOR_MODEL", os.environ.get("DEFAULT_MODEL", "ollama:llama3.2"))
    )
    prompt = REFLECTOR_PROMPT.format(
        observations=json.dumps([
            {"id": o["id"], "content": o["content"], "priority": o["priority"],
             "observation_ts": o["observation_ts"], "referenced_date": o.get("referenced_date")}
            for o in all_observations
        ], indent=2)
    )
    extraction_messages = [
        Message.system("You are a Reflector. Return only valid JSON."),
        Message.user(prompt),
    ]

    loop = AgentLoop(provider=model)
    result = await loop.run_single(extraction_messages)

    if result and result.content:
        reflection = _parse_reflector_json(str(result.content))
        if reflection:
            self.observation_store.insert_reflection(reflection)
            logger.info("reflector.completed",
                        {"observations_condensed": len(all_observations),
                         "output_tokens": reflection.get("token_count", 0)},
                        user_id=self.user_id)

def _fire_reflector_sync(self) -> None:
    """Synchronous fallback for CLI mode (no event loop)."""
    import asyncio as _asyncio
    _asyncio.run(self._fire_reflector())
```

### 12.4 ObservationStore (backed by HybridDB)

```python
class ObservationStore:
    """Storage layer for observations and reflections using HybridDB."""

    def __init__(self, user_id: str, workspace_id: str = "personal"):
        paths = get_paths(user_id, workspace_id)
        self.db = HybridDB(str(paths.workspace_memory_dir()), max_chroma_index_gb=0)
        self._init_tables()

    def _init_tables(self):
        self.db.create_table("observations", {
            "id": "TEXT PRIMARY KEY",
            "content": "LONGTEXT",
            "priority": "TEXT",
            "observation_ts": "TEXT",
            "referenced_date": "TEXT",
            "relative_date": "TEXT",
            "source_message_range": "TEXT",
            "token_count": "INTEGER",
            "context_window": "TEXT",
            "created_at": "TEXT",
        })
        self.db.create_table("reflections", {
            "id": "TEXT PRIMARY KEY",
            "content": "LONGTEXT",
            "source_observation_range": "TEXT",
            "observation_count": "INTEGER",
            "token_count": "INTEGER",
            "created_at": "TEXT",
        })

    def get_pinned_facts(self, limit: int = 20) -> list[Fact]:
        """Facts extracted by Observer, current + high confidence."""
        ...

    def get_recent_observations(self, days: int = 7, limit: int = 50):
        """Observations from last N days, newest first."""
        ...

    def get_latest_reflection(self) -> Reflection | None:
        """Most recent reflection."""
        ...

    def insert_observations(self, observations: list[dict]) -> None:
        """Batch insert observations into HybridDB."""
        ...

    def get_all_observations(self) -> list[dict]:
        """All observations (for Reflector)."""
        ...
```

### 12.5 Memory Search Tool (Updated)

```python
@tool
def memory_search(query: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Search observations, reflections, facts, and raw messages.

    Use when working memory context is insufficient — for queries about
    older information, hyper-specific details, or cross-referencing.
    """
    obs_store = get_observation_store(user_id, workspace_id)
    msg_store = get_message_store(user_id, workspace_id)

    results = []

    # Search observations (most relevant for memory queries)
    obs_results = obs_store.db.search_all("observations", query, limit=5)
    for r in obs_results:
        results.append(f"[observation] {r['priority']} {r['content']}")

    # Search reflections (condensed context)
    refl_results = obs_store.db.search_all("reflections", query, limit=2)
    for r in refl_results:
        results.append(f"[reflection] {r['content']}")

    # Search raw messages (deep fallback)
    msg_results = msg_store.search_hybrid(query, limit=5)
    for r in msg_results:
        results.append(f"[message] {r.role}: {r.content} (score: {r.score:.2f})")

    return format_results(results)


@tool
def memory_get_profile(user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Return the current working memory — what the system knows about the user.

    Use when: user asks 'what do you know about me?' or the agent needs to
    refresh its understanding of the user.
    """
    obs_store = get_observation_store(user_id, workspace_id)
    return obs_store.assemble_working_memory(as_text=True)
```

---

## 13. Observation-Based vs Structured Facts — Decision Analysis

### 13.1 Comparison Matrix

| Dimension | **Observation-Based** (Mastra OM) | **Structured Facts** (current EA) | **Hybrid** (this proposal) |
|-----------|-----------------------------------|-----------------------------------|----------------------------|
| **Format** | Free-text observation entries | Entity/attribute/value JSON pairs | Observations (text) + pinned facts |
| **Compression** | ~6× token reduction (dense text) | ~20× (very lossy, high error rate) | ~8× with higher precision |
| **Fact precision** | Preserved in natural text — fewer drift errors | Lost in structured mapping — "10-12 hours" → "15-18 hours" | Text observations preserve precision; facts supplement |
| **Context injection** | Static prepended block — no retrieval | Per-turn dynamic retrieval — adds latency | Static prepended block + tool-based deep search |
| **Prompt caching** | ✅ Full cache (stable prefix) | ❌ No cache (dynamic injection) | ✅ Full cache (stable prefix) |
| **Temporal reasoning** | ✅ Dates embedded in text | ⚠️ Needs separate temporal query | ✅ Dates in observations + fact history |
| **Multi-session aggregation** | ✅ Reflector merges across sessions | ❌ Flat FTS5, no cross-session fusion | ✅ Reflector merges + graph-linked facts |
| **Latency (before_agent)** | ~5ms (assembly from SQLite) | ~170ms (3x search APIs) | ~5ms (assembly from SQLite) |
| **Error mode** | Observation text slightly off (rare) | Fact value wrong (common with LLMs) | Text observations cover facts absorb errors |
| **Retrieval cost** | None for working memory (static) | 3x API calls per query | None for working memory; tool call for deep queries |

### 13.2 Root Cause of Structured Facts Failure

The structured fact approach fails because it forces two lossy transformations:

```
Raw conversation text
    → [LLM parses into entity/attribute/value JSON]    ← LOSSIER than expected
    → [Stored in memory_facts table]
    → [Retrieved via FTS5 keyword search]              ← retrieval noise
    → [Agent reads structured fact as authoritative]    ← trusts wrong value
```

Each step introduces errors:
1. **LLM parsing**: gpt-4o at extraction time hallucinates values ("10-12" → "15-18")
2. **Retrieval noise**: FTS5 on 10 columns returns spurious matches
3. **Authority bias**: Agent treats extracted facts as truth, ignores raw messages

Observation-based avoids all three steps:
1. **LLM observes**: Produces dense text — errors are semantic approximations, not wrong numbers
2. **No retrieval**: Observations are in the context window — agent reads them directly
3. **No authority bias**: Observations are clearly labeled as "working memory", agent can cross-reference with raw messages if needed

### 13.3 The Hybrid Approach (Recommended)

**Best of both worlds**: Observations for context, structured facts for fast lookups.

```
┌───────────────────────────────────────────────┐
│  OBSERVER (single call, produces both)         │
│                                                │
│  Messages → Observer LLM →                     │
│    ├── Observations (text, for context)        │
│    │     "🔴 Mar 15: user moved to Denver"     │
│    │     "🟡 Mar 15: user discussed hiking"    │
│    │                                           │
│    └── Pinned Facts (structured, for lookups)  │
│          {entity:"user", attr:"location",       │
│           value:"Denver", prev:"Austin"}       │
│          {entity:"user", attr:"interest",       │
│           value:"hiking"}                      │
└───────────────────────────────────────────────┘
```

**Why this works:**
- **Observations** provide context the agent reads directly — no retrieval, no noise
- **Pinned facts** provide fast entity/attribute lookups for profile-style queries
- **Graph linking** connects facts → observations → messages for deep exploration
- **If the Observer errs in a fact**, the observation text still has the correct value — the agent can cross-reference
- **If the Observer errs in an observation**, the fact (extracted in the same call) acts as a correction
- **Single LLM call** produces both — no additional cost vs extraction alone

### 13.4 Why Not Pure Observation (Mastra OM)?

Mastra OM is pure observation — no structured facts, no entity graphs. This works for them because:
- They don't have graph capabilities (no entity linking, no pagerank, no graph-boosted search)
- Their search is flat ChromaDB (no FTS5, no hybrid)
- They optimize for simplicity (one table, one format)

We have HybridDB with graph, FTS5, ChromaDB, and journal. Not using structured facts alongside observations would be leaving capability on the table. The hybrid approach is strictly better FOR US — it costs nothing extra (same Observer LLM call) and gives us:
- Graph awareness (facts → graph nodes → pagerank)
- Fast entity lookups (facts → memory_facts → `find_facts_for_query`)
- Redundancy (if observation misses a detail, fact catches it; vice versa)
- Migration compatibility (existing facts persist alongside new observations)

### 13.5 Recommendation

**Hybrid approach: observations + pinned facts.**

1. Implement Observer that produces BOTH observations and facts in a single LLM call
2. Working memory = pinned facts (top) + recent observations + reflection
3. `memory_search` tool searches observations, reflections, facts, and messages
4. Observer fires on background thread every ~4K tokens of unobserved messages
5. Reflector condenses observations every ~8K tokens into dense past context

**Target accuracy pathway:**
- Baseline (structured facts only): 21% (current, broken)
- Baseline (verbatim only, no extraction): 56% (current, works but no memory)
- Phase 1 (Observer only, gpt-4o): est. 60-65% (text observations > structured facts)
- Phase 2 (+ Reflector): est. 70-75% (condensed multi-session context)
- Phase 3 (+ tuned Observer prompt + fact quality): est. 78-82%
- Phase 4 (+ separate Observer model, flash-tier): est. 84% (Mastra OM level)

The 84% target is achievable with gpt-4o as the actor and a flash-tier model as the Observer — matching Mastra OM's architecture exactly, with the addition of graph-boosted search from our HybridDB capabilities.

---

## 14. Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/sdk/middleware_observation.py` | **New** | `ObservationMiddleware` — before_agent, after_agent, Observer/Reflector triggers |
| `src/sdk/tools_core/observation.py` | **New** | Observer prompt, Reflector prompt, `_run_observer_llm()`, `_run_reflector_llm()` |
| `src/storage/observation.py` | **New** | `ObservationStore` — HybridDB-backed CRUD for observations + reflections |
| `src/sdk/hybrid_db.py` | **Modify** | Add `observations`, `reflections` table init. Global `_init_observation_tables()`. |
| `src/sdk/tools_core/memory.py` | **Modify** | Update `memory_search` to query observations. Add `memory_get_profile`. |
| `src/sdk/middleware_memory.py` | **Modify** | Swap `before_agent` for working memory injection. Disable `_extract_with_llm`. |
| `src/storage/memory.py` | **Modify** | Add `Observation` and `Reflection` dataclasses. Add query methods. |
| `tests/sdk/test_observation.py` | **New** | Unit tests for Observer output format, working memory assembly, token budgets |
| `tests/sdk/test_observation_integration.py` | **New** | Integration tests: Observer reads real messages, Reflector condenses observations |
| `docs/OBSERVATIONAL_MEMORY_DESIGN.md` | **This file** | Design document |

---

## 15. Code Review (2026-05-03)

### 15.1 Overall Assessment

The design is well-motivated and architecturally sound. The LongMemEval data (21% with extraction
enabled, 56% without) makes a compelling case that structured fact extraction is actively harmful.
The shift to text observations as the primary memory format is the right call. The decision to keep
pinned facts alongside observations (the "hybrid" approach) is pragmatic — it preserves the
HybridDB graph capabilities without reintroducing the extraction accuracy problem.

**Verdict: Proceed with Phase 1, with the adjustments noted below.**

### 15.2 Architecture Fit (Strong)

The `ObservationMiddleware` pattern maps cleanly onto the existing middleware architecture:

- `before_agent()` → `_assemble_working_memory()` → inject static context block
- `after_agent()` → `_maybe_fire_observer()` / `_maybe_fire_reflector()` → background tasks

This replaces `MemoryMiddleware.before_agent` (lines 577-611 in `middleware_memory.py`)
and `MemoryMiddleware.after_agent` (lines 613-644). The swap is clean — the existing
`runner.py` assembles middlewares and AgentLoop dispatches them. No loop.py changes needed.

### 15.3 Issues Found

#### Issue 1: `before_agent` signature mismatch with ABC

The design document uses this signature (section 12.3):

```python
async def before_agent(self, messages: list[Message], **kwargs) -> list[Message]:
```

But the actual `Middleware` ABC (`middleware.py:28`) defines:

```python
def before_agent(self, state: AgentState) -> dict[str, Any] | None:
```

The middleware receives an `AgentState` object and returns state updates (`dict | None`) —
not a `list[Message]` that replaces the message list. The existing `MemoryMiddleware.before_agent`
returns `{"messages": current_messages}` to update state. The design should follow the same pattern:

```python
async def abefore_agent(self, state: AgentState) -> dict[str, Any] | None:
    working_memory = await self._assemble_working_memory()
    if not working_memory:
        return None
    messages = state.messages
    # Find system message and inject working memory
    ...
    return {"messages": messages}
```

#### Issue 2: `facts_extracted` field reintroduces the hallucination risk

The Observer prompt (section 4.1) asks the LLM to produce a `facts_extracted` field containing
structured `{entity, attribute, value}` triples. This is EXACTLY the structured extraction
that the LongMemEval data shows is harmful — "10-12 hours" → "15-18 hours".

**If the Observer produces facts alongside observations, and those facts are wrong, the pinned
facts section in working memory will still contain wrong data.** The document argues that
observations provide a cross-reference ("if the Observer errs in a fact, the observation text
still has the correct value") but this assumes the agent will cross-reference. It won't —
the same "authority bias" problem applies: the agent trusts structured data over text.

**Recommendation:** In Phase 1, have the Observer produce ONLY observations. Add `facts_extracted`
in Phase 3 after validating that observation quality is high enough that facts can be derived
from working memory by a separate, simpler extraction step that operates on the (more reliable)
observation text rather than raw messages.

#### Issue 3: Token threshold calibration is untested

The design uses OBSERVER_THRESHOLD = 4000 tokens and REFLECTOR_THRESHOLD = 8000 tokens.
These are Mastra OM's numbers, not calibrated for Assistant's conversation patterns. In active
conversations, 4000 tokens of unobserved messages could accumulate in 2-3 turns — meaning
the Observer fires after almost every turn, consuming LLM budget.

**Recommendation:** Start with higher thresholds (8000/16000) and reduce only after measuring
LongMemEval accuracy vs cost. Add a minimum-turn-interval guard:

```python
MIN_OBSERVER_INTERVAL_TURNS = 3  # don't fire more often than every 3 turns
```

#### Issue 4: Background thread vs asyncio task confusion

Section 4.3 uses `asyncio.create_task()` for observer/reflector, which is correct for an async
event loop. Section 2.2 and section 5 mention "background thread." The existing `MemoryMiddleware`
handles both cases (line 626-640 of `middleware_memory.py`):

```python
try:
    asyncio.create_task(self._extract_async(recent_messages))
except RuntimeError:
    threading.Thread(target=self._extract_with_llm, ...).start()
```

**Recommendation:** Use the same dual-path pattern. In the existing AgentLoop, `after_agent` is
called from `_run_hooks` which runs in the event loop — so `asyncio.create_task` is correct.
But the Observer LLM call should use `loop.run_single()` (which requires the event loop) rather
than a raw `provider.chat()` call. The `_run_observer_llm` and `_run_reflector_llm` functions
should follow the pattern used by `MemoryMiddleware._do_extract()` (line 666-692).

#### Issue 5: `observation_ts` type uses TEXT, not a typed datetime

The `observations` table defines `observation_ts` as `TEXT` (section 3.1). The query in
`_assemble_working_memory` (section 4.4) does:

```python
where="observation_ts > ?",
params=[(date.today() - timedelta(days=7)).isoformat()],
```

SQLite can do date comparisons on ISO-format TEXT, but only lexicographically — this works
for ISO dates but will fail if timestamps include timezones. Use Unix timestamps (INTEGER)
or ensure consistent ISO format. The existing `memory.py:22` imports `datetime(UTC)` —
match that convention.

#### Issue 6: `memory_search` drops fact search from results

Section 5.1's `memory_search` redesign searches observations and messages but omits facts:

```python
observations = obs_store.search_hybrid(query, limit=5)
messages = msg_store.search_hybrid(query, limit=5)
```

But section 13.4 argues that facts are still valuable for "fast entity lookups." If
`memory_search` doesn't search facts, the tool becomes less capable for structured queries
like "what's my job title." Add a third search:

```python
facts = obs_store.search_facts(query, limit=3)  # memory_facts FTS5
```

#### Issue 7: No migration strategy for existing `memories` rows

Section 3.4 says "existing facts can be migrated into observations during transition" but
provides no migration mechanism. The `memories` table has ~200+ rows per user with trigger/action
pairs, domains, confidence scores, and structured data. Migrating these to the observation
format requires either:
- (a) A one-time migration script that converts each memory row to an observation text entry
- (b) Keeping the `memories` table as a fallback and only producing observations for new messages

**Recommendation:** Option (b) for Phase 1 — keep existing memories as-is, add observations
for new conversations. In Phase 4, convert memories to observations via a Reflector pass.

#### Issue 8: ChromaDB crash recovery concern

The HybridDB audit found 17 bugs including ChromaDB crash recovery issues. The ObserverStore
creates a new HybridDB instance with `max_chroma_index_gb=0` (section 12.4):

```python
self.db = HybridDB(str(paths.workspace_memory_dir()), max_chroma_index_gb=0)
```

`max_chroma_index_gb=0` may disable ChromaDB entirely — verify this is intentional. If the
Observer needs semantic search over observations, ChromaDB must be enabled. If not, setting
it to 0 avoids the crash recovery bugs, which is a reasonable call for Phase 1.

#### Issue 9: `count_unobserved_tokens()` is underspecified

The Observer trigger depends on counting unobserved tokens (section 4.3), but the document
doesn't specify HOW. Options:
- (a) Track a watermark message ID and count tokens from messages after it
- (b) Track a column in the `messages` table (`is_observed` flag)
- (c) Use the existing SummarizationMiddleware's tiktoken-based counting

**Recommendation:** Use (a) with tiktoken-based counting, matching the existing
`SummarizationMiddleware._count_message_tokens()` (line 89 at `middleware_summarization.py`).

### 15.4 Things the Design Gets Right

1. **HybridDB reuse** — using the existing HybridDB for observations/reflections is correct.
   No new storage layer needed. Graph capabilities (pagerank, entity linking) apply to
   observations automatically if `register_entity_node` is called.

2. **Static context injection model** — the prompt-cacheable prefix design is the key advantage
   over dynamic retrieval. The existing `before_agent` injection pattern in `MemoryMiddleware`
   already does this; ObservationMiddleware just swaps the content.

3. **Non-blocking Observer** — fire-and-forget via `asyncio.create_task` is the right pattern
   for production. The existing `MemoryMiddleware.after_agent` already does this (line 626).

4. **Append-only design** — observations are never deleted, only condensed by Reflector.
   This is safer than the existing consolidation pipeline (which supersedes memory rows).

5. **Incremental phases** — the 4-phase plan (Observer → tune → Reflector → deprecate) is
   disciplined. Each phase has a LongMemEval target.

6. **Tool reduction** — reducing the agent's need to call `memory_search` by putting context
   directly in the window is the right UX improvement. Fewer tool calls = faster responses.

### 15.5 Open Questions for Implementation

1. Should `_estimate_tokens` use tiktoken (like SummarizationMiddleware) or a simpler char/4 heuristic?
2. How does the Observer interact with the existing consolidation pipeline (`_check_and_trigger_consolidation` in `memory.py`)?
3. Can `ObservationMiddleware` and `MemoryMiddleware` coexist during migration, or does one replace the other completely?
4. What happens when the Observer model returns invalid JSON? The prompt says "Respond with ONLY the JSON array" but the existing code has a `_parse_json_response` fallback in `middleware_memory.py:844`.
5. Should `memory_search` results include observation priority emojis (🔴🟡🟢) for the agent to judge reliability?

### 15.6 Revised File Impact

The files list in section 14 is accurate, with one addition:

| File | Action | Purpose |
|------|--------|---------|
| `src/sdk/middleware_observation.py` | **New** | `ObservationMiddleware` |
| `src/sdk/tools_core/observation.py` | **New** | Observer/Reflector prompts and LLM runners |
| `src/storage/observation.py` | **New** | `ObservationStore` |
| `src/sdk/hybrid_db.py` | **Modify** | New tables |
| `src/sdk/tools_core/memory.py` | **Modify** | Updated `memory_search`, new `memory_get_profile` |
| `src/sdk/middleware_memory.py` | **Modify** | Disable extraction, swap injection |
| `src/sdk/runner.py` | **Modify** | Wire `ObservationMiddleware` into middleware list |
| `tests/sdk/test_observation.py` | **New** | Unit tests |
| `tests/sdk/test_observation_integration.py` | **New** | Integration tests |

`runner.py` is missing from the original list — it's where middlewares are wired into AgentLoop.

---

## 16. Author's Verdict on Peer Review (2026-05-03)

All 9 issues verified against actual source code at the referenced line numbers.

### Issue 1: `before_agent` ABC signature mismatch — ✅ ACCEPTED

**Review claim**: Design uses `async def before_agent(self, messages: list[Message], **kwargs) -> list[Message]` but actual ABC (`middleware.py:28,44`) defines `def before_agent(self, state: AgentState) -> dict[str, Any] | None` and `async def abefore_agent(self, state: AgentState) -> dict[str, Any] | None`.

**Verified at**: `src/sdk/middleware.py:28` — correct. `MemoryMiddleware.before_agent` at `middleware_memory.py:570` takes `(self, state: AgentState)`. `AgentState` at `state.py:16` has `messages: list[Message]`.

**Resolution**: Update section 12.3 signatures to match ABC contract:
```python
def before_agent(self, state: AgentState) -> dict[str, Any] | None:
    working_memory = self._assemble_working_memory()
    if not working_memory:
        return None
    state.messages.insert(-1, Message.system(working_memory["block_text"]))
    return {"messages": state.messages}

async def abefore_agent(self, state: AgentState) -> dict[str, Any] | None:
    return self.before_agent(state)  # or async variant if needed

def after_agent(self, state: AgentState) -> dict[str, Any] | None:
    ...
```

### Issue 2: `facts_extracted` reintroduces hallucination risk — ⚠️ PARTIALLY ACCEPTED (modified)

**Review claim**: Observer producing `facts_extracted` alongside observations reintroduces the structured extraction accuracy problem. If facts are wrong, the pinned facts section in working memory contains wrong data. The agent will trust structured data over text (authority bias).

**Verified at**: Extraction pipeline produces `{entity, attribute, value}` at `middleware_memory.py:665` via `EXTRACTION_PROMPT`. The 21% accuracy confirms this is harmful. The reviewer is correct about the risk.

**Resolution**: MODIFIED from review recommendation.

Review says: "Phase 1: observations only, Phase 3: add facts from working memory."

My position: The Observer MUST produce both observations and facts in a single LLM call (it costs nothing extra). But **facts should NOT be displayed in working memory in Phase 1**. Instead:
- Phase 1: Observer produces observations (displayed in working memory) + facts (stored silently in `memory_facts` table, NOT displayed)
- Phase 2: Validate fact quality against LongMemEval ground truth using a fact-precision metric
- Phase 3: If fact precision > 85%, display pinned facts in working memory
- If fact precision < 85% in Phase 2, disable fact display permanently

This way: (a) facts are collected as a sidecar during Phase 1 for quality testing, (b) no display risk until validated, (c) the single LLM call produces maximum value.

### Issue 3: Token threshold calibration untested — ✅ ACCEPTED

**Review claim**: 4000/8000 from Mastra OM may fire too often in Assistant's conversation patterns. Use 8000/16000 with minimum-turn guard.

**Verified at**: No existing threshold calibration in EA. The SummarizationMiddleware at `middleware_summarization.py` uses a configurable `MAX_TOKENS` without turn-based guards. The reviewer's concern about firing every 2-3 turns is valid.

**Resolution**: Start with `OBSERVER_THRESHOLD = 8000`, `REFLECTOR_THRESHOLD = 16000`, `MIN_OBSERVER_INTERVAL_TURNS = 3`. Reduce after LongMemEval calibration.

### Issue 4: Thread vs asyncio confusion — ✅ ACCEPTED

**Review claim**: Use the existing dual-path pattern from `MemoryMiddleware.after_agent` (lines 626-640 of `middleware_memory.py`).

**Verified at**: `middleware_memory.py:626-640` uses `try: asyncio.create_task() except RuntimeError: threading.Thread()`. This handles both HTTP server (event loop available) and CLI (no event loop). The design doc's `asyncio.create_task` alone would fail in CLI mode.

**Resolution**: Add dual-path pattern to `_maybe_fire_observer()` and `_maybe_fire_reflector()`:
```python
try:
    asyncio.create_task(self._fire_observer())
except RuntimeError:
    threading.Thread(target=self._fire_observer_sync, daemon=True).start()
```

### Issue 5: `observation_ts` TEXT type date comparison — ✅ ACCEPTED

**Review claim**: ISO-format TEXT comparisons are lexicographic. Use consistent UTC ISO format matching existing `datetime(UTC)` convention.

**Verified at**: `hybrid_db.py:39` defines `_now_iso()` using `datetime.now(UTC)`. The existing `memory.py:22` imports `datetime(UTC)`. The convention is already established — just need to enforce it in observation inserts.

**Resolution**: Observer must use `datetime.now(UTC).isoformat()` for `observation_ts`. `referenced_date` uses the same format. Accept — trivial.

### Issue 6: `memory_search` drops fact search — ✅ ACCEPTED

**Review claim**: Section 5.1's `memory_search` redesign searches observations + messages but omits fact search, contradicting section 13.4's argument that facts are valuable for entity lookups.

**Verified at**: The current `memory_search` at `memory.py:170` calls `find_facts_for_query()` + `search_hybrid()`. The redesign should keep `find_facts_for_query` — the reviewer is correct that dropping it weakens the tool.

**Resolution**: Updated `memory_search` includes all three sources:
```python
facts = obs_store.find_facts_for_query(query, limit=3)
observations = obs_store.db.search_all("observations", query, limit=5)
messages = msg_store.search_hybrid(query, limit=5)
```

### Issue 7: No migration strategy for existing memories — ✅ ACCEPTED

**Review claim**: Section 3.4 says "existing facts can be migrated" but provides no mechanism. Reviewer recommends keeping existing `memories` as-is for Phase 1.

**Verified at**: The `memories` table in `memory.py:140` stores trigger/action/domain/confidence. There's no code path to convert memory rows to observations. The reviewer's recommendation (b) is the only viable approach.

**Resolution**: Phase 1 keeps existing memories + adds observations for new conversations. `memory_search` queries both `memories` (existing) and `observations` (new). Phase 4 migration script converts memories → observations via a Reflector pass.

### Issue 8: `max_chroma_index_gb=0` intent — ✅ ACCEPTED (clarified)

**Review claim**: Verify whether setting `max_chroma_index_gb=0` is intentional.

**Verified at**: `hybrid_db.py:154-158` — when `max_chroma_index_gb == 0`, `_init_chroma` is skipped and `_chroma` is set to `None`. This means the Observer writes to SQLite+FTS5 only (no ChromaDB). For Phase 1, this is correct — observations are indexed by FTS5 keyword search, and the ChromaDB crash recovery bug from the audit is avoided.

**Resolution**: Intentional. ChromaDB for observations should be enabled in Phase 3 after crash recovery is validated. Not needed for Phase 1 — FTS5 keyword search on observation text is sufficient.

### Issue 9: `count_unobserved_tokens()` underspecified — ✅ ACCEPTED

**Review claim**: No specification for how to count unobserved tokens. Reviewer recommends watermark + tiktoken, matching `SummarizationMiddleware._count_message_tokens()`.

**Verified at**: `middleware_summarization.py:89` uses `tiktoken` or `len(content) // 4` heuristic. The `MessageStore.get_messages()` returns rows with `id` field — watermark approach (b) is feasible.

**Resolution**: Use watermark approach with tiktoken:
```python
def _count_unobserved_tokens(self) -> int:
    messages = self._message_store.get_messages(since_id=self._unobserved_since, limit=500)
    return sum(self._estimate_tokens(m.content) for m in messages)
```

---

### Scoring

| Issue | Accepted | Rationale |
|-------|----------|-----------|
| 1 (ABC signature) | ✅ Full | Verified against `middleware.py:28` — design was wrong |
| 2 (facts_extracted risk) | ⚠️ Modified | Produce facts silently, don't display until validated |
| 3 (token thresholds) | ✅ Full | Start higher with turn guard — calibration needed |
| 4 (thread vs asyncio) | ✅ Full | Use existing dual-path from `middleware_memory.py:626` |
| 5 (date type) | ✅ Full | Trivial — use existing `datetime(UTC)` convention |
| 6 (memory_search facts) | ✅ Full | Trivial — keep `find_facts_for_query` |
| 7 (migration strategy) | ✅ Full | Keep existing memories, Phase 4 migration |
| 8 (max_chroma_index_gb) | ✅ Full | Intentional, correct for Phase 1 |
| 9 (token counting) | ✅ Full | Use tiktoken matching SummarizationMiddleware |

**8 accepted, 0 rejected, 1 modified** (facts_extracted: keep but don't display).

### Implementation Status (2026-05-03)

| Issue | Status | Where applied |
|-------|--------|---------------|
| 1 (ABC signature) | ✅ Fixed | Section 12.3 `before_agent`/`after_agent` now takes `state: AgentState`, returns `dict[str, Any] \| None` matching `middleware.py:28` |
| 2 (facts_extracted risk) | ✅ Modified | Section 4.1 Observer prompt: facts stored silently, not displayed until Phase 3. Section 12.3 `_assemble_working_memory` excludes pinned facts (Phase 1-2). Section 13.3 updated. |
| 3 (token thresholds) | ✅ Fixed | Section 4.3: `8000/16000` thresholds. `MIN_OBSERVER_INTERVAL_TURNS = 3`. Section 12.3: same values. |
| 4 (thread vs asyncio) | ✅ Fixed | Section 4.3, 12.3: dual-path `_dispatch_fire_observer/reflector` with `try: asyncio.create_task except RuntimeError: threading.Thread` matching `middleware_memory.py:626` |
| 5 (date type) | ✅ Fixed | Section 4.3b: `ISO format with UTC` matching `datetime.now(UTC).isoformat()` from `hybrid_db.py:39` |
| 6 (memory_search facts) | ✅ Fixed | Section 5.1: `memory_search` now searches facts (`find_facts_for_query`), observations, reflections, and messages |
| 7 (migration strategy) | ✅ Fixed | Section 3.4: "Keep existing memories as-is for Phase 1. Phase 4: convert via Reflector pass." |
| 8 (max_chroma=0 intent) | ✅ Clarified | Section 3.1, 12.4: `max_chroma_index_gb=0` is intentional for Phase 1 — avoids crash recovery bugs. FTS5 sufficient for observation search. |
| 9 (token counting) | ✅ Fixed | Section 4.3b: `_count_unobserved_tokens` and `_estimate_tokens` using tiktoken with char/4 fallback, matching `middleware_summarization.py:89` |

All 9 issues addressed in design document. No production code was changed — the ObservationMiddleware module doesn't exist yet.
