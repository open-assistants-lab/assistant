# Deterministic Memory Ranker Plan

## Goal

Improve long-memory retrieval accuracy without adding extra LLM calls.

The current best stable memory path is strong but imperfect:

- Best historical run: **469/500 (93.8%)**
- Latest stable run with models.dev `ollama-cloud/minimax-m2.5`: **459/500 (91.8%)**
- Main remaining failures:
  - repeated `Where do I live?` missing `Denver`
  - search/history questions missing expected tokens
  - final recall missing older values like `dark roast`, `vs code`, `jira`, `retired`, `uplift`

We want to improve these without an LLM-assisted planner and without ASMR-style multi-agent retrieval latency.

## Core Idea

Introduce a deterministic evidence ranking layer between retrieval and context injection.

Current flow:

```text
query -> retrieve facts/memories/messages -> inject sections directly
```

New flow:

```text
query -> collect candidates -> score candidates -> dedupe -> inject top evidence only
```

The ranker should decide which facts/messages/memories are most useful for the current user query.

## Non-Goals

- No extra LLM calls.
- No full ASMR multi-agent retrieval.
- No broad keyword expansion explosion.
- No schema rewrite of Message DB.
- No replacement of structured facts; this builds on them.

## Design Overview

### Candidate Model

Add a lightweight candidate representation:

```python
@dataclass
class MemoryCandidate:
    source: Literal["fact", "memory", "message"]
    text: str
    score: float
    metadata: dict[str, Any]
```

Example fact candidate:

```python
MemoryCandidate(
    source="fact",
    text="user.location = Denver",
    score=0,
    metadata={
        "entity": "user",
        "attribute": "location",
        "value": "Denver",
        "current": True,
        "superseded": False,
        "domain": "personal",
        "updated_at": "...",
    },
)
```

Example message candidate:

```python
MemoryCandidate(
    source="message",
    text="user (2026-04-30): I moved to Denver last week",
    score=0,
    metadata={
        "role": "user",
        "ts": "2026-04-30T...",
        "search_score": 0.82,
    },
)
```

## Retrieval Inputs

For each memory query, collect candidates from:

1. **Current structured facts**
   - `MemoryStore.find_facts_for_query(query, include_superseded=False)`

2. **Superseded structured facts**
   - Only for history-like queries.
   - Use existing `find_fact_history_for_query()`.

3. **Message evidence**
   - Existing `MessageStore.search_hybrid()` over expanded queries.

4. **Learned memories**
   - Existing `MemoryStore.search_hybrid()`.
   - Lower priority than exact facts.

## Query Classification Without LLM

Keep this deterministic and conservative.

Use query features, not a full planner, to influence ranking:

```python
QueryFeatures(
    is_memory_query: bool,
    wants_current: bool,
    wants_history: bool,
    wants_summary: bool,
    wants_search_evidence: bool,
    wants_count_or_list: bool,
)
```

These features should only adjust scores and retrieval breadth.

They should not become a hard routing system that disables useful evidence entirely.

## Scoring Formula

Initial candidate score:

```text
score = 0
```

### Positive Signals

| Signal | Weight | Applies To | Notes |
|---|---:|---|---|
| source is current structured fact | +40 | facts | Current facts should beat messages for direct recall. |
| exact value/token overlap | +25 | all | Query terms overlap with candidate text. |
| attribute/domain overlap | +20 | facts/memories | Query mentions known attribute/domain. |
| message search score | +0 to +20 | messages | Normalize search score into bounded contribution. |
| recency bonus | +0 to +10 | messages/facts | Higher when query asks current/latest/now. |
| user-authored evidence | +8 | messages | User statements beat assistant summaries. |
| correction/update marker | +8 | messages/facts | `actually`, `changed`, `new`, `moved`, etc. |
| source is exact fact and value is short | +5 | facts | Helps direct answers like name/location/project. |

### Negative Signals

| Signal | Weight | Applies To | Notes |
|---|---:|---|---|
| superseded fact for current query | -50 | facts | Prevent stale facts in direct recall. |
| old message for current query | -10 to -25 | messages | Avoid old evidence when current value exists. |
| duplicate value/attribute | -10 | all | Avoid repeating equivalent context. |
| assistant-only evidence when user evidence exists | -10 | messages | Assistant paraphrases are secondary. |
| very long candidate text | -5 to -15 | all | Avoid crowding context with verbose snippets. |

### Historical Query Adjustment

If `wants_history=True`:

- reduce superseded penalty from `-50` to `0`
- add `+20` to superseded facts from matching fact chain
- include ordered fact history in final context

### Summary Query Adjustment

If `wants_summary=True`:

- prefer diversity across domains
- include more current facts
- allow a few historical facts if high score
- cap messages aggressively

## Dedupe Rules

After scoring, dedupe candidates before injection.

Rules:

1. **Fact key dedupe**
   - For current queries, keep only the highest-scoring candidate per `fact_key`.

2. **Text fingerprint dedupe**
   - Normalize lowercase alphanumeric words.
   - Drop near-identical snippets.

3. **Value dedupe**
   - If multiple candidates say `Denver`, keep exact fact first, then best message evidence.

4. **Source priority tie-breaker**
   - facts > user messages > learned memories > assistant messages

## Context Formatting

Do not inject large separate sections blindly.

Recommended output format:

```text
## Relevant Memory Search Results
Use Exact Facts first when they directly answer the user question.

### Highest-Ranked Evidence
1. [fact/current] user.location = Denver
2. [message/user/2026-04-30] I moved to Denver last week
3. [fact/current] user.current_project = Real-Time Analytics Pipeline
```

For history queries:

```text
## Relevant Memory Search Results
Use current facts for current-state questions. Use the history chain only for before/previous/timeline questions.

### Highest-Ranked Evidence
1. [fact/current] user.location = Denver

### Relevant History
1. [superseded] user.location = Austin
2. [current] user.location = Denver
```

## Injection Limits

Default limits:

| Query Type | Facts | Messages | Memories | History |
|---|---:|---:|---:|---:|
| direct/current fact | 3 | 3 | 0-2 | 0 |
| search/history | 4 | 5 | 2 | 0-4 |
| summary/final recall | 8 | 4 | 4 | 4 |

Hard context cap:

- target max injected context: **2,500 chars**
- absolute max: **4,000 chars**

This should reduce model confusion and improve latency.

## Implementation Steps

### Step 1: Add Ranker Module

Create:

```text
src/sdk/memory_ranker.py
```

Exports:

```python
@dataclass
class QueryFeatures: ...

@dataclass
class MemoryCandidate: ...

def extract_query_features(query: str) -> QueryFeatures: ...
def collect_memory_candidates(user_id: str, query: str) -> list[MemoryCandidate]: ...
def rank_memory_candidates(query: str, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]: ...
def format_ranked_memory_context(query: str, ranked: list[MemoryCandidate]) -> str: ...
```

### Step 2: Wire Into MemoryMiddleware

Change `MemoryMiddleware._get_baseline_memory_context()` to:

```python
candidates = collect_memory_candidates(self.user_id, query)
ranked = rank_memory_candidates(query, candidates)
return format_ranked_memory_context(query, ranked)
```

Keep old baseline behind env flag for comparison:

```text
MEMORY_RANKER_ENABLED=true
```

Initial rollout:

- default `false` during implementation
- enable for smoke and targeted tests
- enable by default only after it beats baseline

### Step 3: Add Unit Tests

Add:

```text
tests/sdk/test_memory_ranker.py
```

Core tests:

1. Current fact beats old message.
2. Superseded fact is excluded/penalized for current query.
3. Superseded fact is included for historical query.
4. User message beats assistant message when both mention same fact.
5. Dedupe keeps exact fact + best evidence only.
6. Summary query preserves diverse domains.
7. Context formatter respects max items/chars.

### Step 4: Targeted Benchmark Before 500-Turn Run

Create or adapt a smaller targeted eval:

```text
tests/benchmarks/test_ws_memory_targeted_failures.py
```

Focus on known failure classes:

- `Where do I live?` repeated after update
- current project after multiple project updates
- search/history questions
- final recall of historical values
- gym cancellation wording
- drink preference chain

Target:

- 50-100 turns
- under 15 minutes
- should pass **>=95%** before a full 500-turn run

### Step 5: Full Benchmark

Run:

```bash
MEMORY_RANKER_ENABLED=true \
PYTHONUNBUFFERED=1 \
uv run python tests/benchmarks/test_ws_memory_progressive.py
```

Compare against:

- current stable run: **459/500 (91.8%)**
- best historical run: **469/500 (93.8%)**

Success criteria:

| Metric | Target |
|---|---:|
| Overall | >= 469/500 (93.8%) |
| fact_recall | 100% |
| fact_store | 100% |
| idempotent | >= 96% |
| search | >= 90% |
| final_recall | >= 84% |
| errors | 0 |
| avg latency | <= current baseline or no worse than +10% |

## Risk Assessment

### Risks

- Scoring weights overfit benchmark phrasing.
- Too little context hurts broad summary questions.
- Too much history reintroduces stale-current confusion.
- Candidate collection can add DB latency if not bounded.

### Mitigations

- Feature flag rollout.
- Keep old baseline path.
- Unit test scoring invariants instead of individual phrases.
- Cap candidate counts and injected chars.
- Log top candidates and scores for failure analysis.

## Logging / Observability

Add one log event per injection:

```json
{
  "event": "memory.ranker_context_injected",
  "query": "Where do I live?",
  "candidate_count": 24,
  "injected_count": 4,
  "top_sources": ["fact", "message", "fact"],
  "top_scores": [72.0, 58.4, 42.0],
  "context_chars": 812
}
```

This should make failures diagnosable without reading full prompts.

## Expected Outcome

Expected improvement:

- Recover `Where do I live?` idempotent failures.
- Improve search/history evidence selection.
- Improve final recall by allowing high-scoring historical facts only when appropriate.
- Reduce context size and possibly improve latency.

Expected score:

- conservative: **+1pp** over current stable run → ~92.8%
- likely: **+2-3pp** → ~94-95%
- best case: restores or exceeds previous best **93.8%** while keeping errors at 0

## Rollback Plan

If ranker underperforms:

```bash
MEMORY_RANKER_ENABLED=false
```

The current baseline path remains available and should continue passing SDK tests.
