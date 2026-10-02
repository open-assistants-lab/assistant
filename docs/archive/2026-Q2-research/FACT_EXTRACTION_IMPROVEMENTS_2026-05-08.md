# Fact Extraction Improvements — 2026-05-08

## Problem

After fixing tool/system role filtering, truncation limits, and knowledge-update resolution positioning, the remaining LongMemEval failures share a common root cause: **facts aren't extracted from conversation messages into the fact store**.

Concrete examples from evaluation:

| Question | GT | Synthesis Got | Why |
|---|---|---|---|
| Clothing items to pick up/return | 3 | 2 | `clothing_pickup_items_count = 2` stored; 3rd item not extracted |
| Model kits worked on/bought | 5 | 4 | List items counted (4); scalar `current_project = Tiger I tank` not extracted |
| Projects led/leading | 2 | none | No facts stored at all |

**Important constraint**: fixes must be general-purpose. No question-specific heuristics. A user in production should benefit equally.

## Current Pipeline

```
User Messages → MemoryMiddleware.after_agent() → _should_extract()?
    │
    ├── (every 3 turns) OR (keyword trigger)
    │
    ├── _do_extract(): LLM call with EXTRACTION_PROMPT → JSON array of patterns
    │
    └── _store_patterns():
        ├── memory_type == "fact" → upsert_fact_memory(entity, attribute, value)
        ├── trigger+domain dedup: if (trigger, domain) already seen → skip
        └── confidence capped at MAX_CONFIDENCE = 0.7
```

## Root Causes

### 1. No Historical Extraction on Bulk Load

**File:** `src/sdk/middleware_memory.py:662`

`MemoryMiddleware.after_agent()` extracts facts incrementally during active conversation — every 3 turns. But when the eval loads 47 sessions of historical messages into MessageStore, extraction never fires. The agent answers questions before any facts from those sessions exist.

**Why this is general-purpose:** Any system importing chat history (migration, backup restore, multi-device sync, onboarding new user with history) has the same gap. The fact store would be empty for all prior conversations.

**Fix A: Run extraction pass on all unprocessed messages after bulk insert.**

```
After bulk import of historical sessions → iterate unprocessed messages in batches → fire extraction for each batch
```

### 2. Trigger+Domain Dedup Kills Multi-Item Facts

**File:** `src/sdk/middleware_memory.py:787-790`

```python
trigger_key = (trigger.lower().strip(), domain.lower().strip())
if trigger_key in seen_triggers:
    continue
seen_triggers.add(trigger_key)
```

When a user says "I need to pick up exchanged boots from Zara, return the navy blazer, and get new jeans from Nordstrom," the LLM might extract 3 facts with the same trigger ("clothing pickup") and domain ("personal"). The dedup keeps only the first one and silently drops the rest.

**Why this is general-purpose:** Users naturally list multiple items under the same context. This isn't LongMemEval-specific — it's a data loss bug that affects any multi-item user statement.

**Fix B: Remove trigger+domain dedup. Deduplicate by full content hash instead.**

```python
# Before (WRONG): drops facts with same trigger+domain
trigger_key = (trigger.lower().strip(), domain.lower().strip())
if trigger_key in seen_triggers:
    continue

# After (CORRECT): only dedup exact content matches
content_hash = hashlib.md5(
    f"{trigger}|{action}|{domain}|{json.dumps(structured_data, sort_keys=True)}".encode()
).hexdigest()
if content_hash in seen_hashes:
    continue
```

### 3. Scalar-Only Fact Extraction Misses Lists

**File:** `src/sdk/middleware_memory.py:93-165` (EXTRACTION_PROMPT)

The current extraction prompt asks for individual facts:

```json
{"entity": "user", "attribute": "property", "value": "single value"}
```

This produces `user.clothing_pickup = boots` (scalar). When multiple items share the same attribute, each update overwrites the previous one (via `upsert_fact_memory`'s fact_key dedup). The final fact store has only the last-seen value.

For the model kits question:
- Session 3: "completed kits = [Revell F-15, Spitfire Mk.V]" → stored as scalar "Revell F-15 Eagle, Tamiya 1/48 Spitfire Mk.V" (comma-separated, not list-valued)
- Session 5: "purchased kits = [B-29 bomber, 69 Camaro]" → different attribute, stored separately
- Session 7: "current project = Tiger I tank" → scalar, stored separately but with different attribute

The synthesis code counts list-valued facts only. Scalar facts with different attributes require semantic matching (which attribute is relevant to "model kits"?), which synthesis can't do.

**Why this is general-purpose:** Any domain where users track collections over time — model kits, books read, restaurants tried, projects managed — suffers from this. The extraction should preserve list semantics when applicable.

**Fix C: Enhance extraction prompt to produce list-valued facts.**

```
When multiple items belong to the same category:
- Use "value" as a JSON array: ["item1", "item2", "item3"]
- Example: user says "I bought boots, a jacket, and jeans"
  → {"entity": "user", "attribute": "clothing_purchases", "value": ["boots", "jacket", "jeans"]}
- If items accumulate across sessions, append to the list, don't replace
```

### 4. Extraction Confidence Capped at 0.7

**File:** `src/storage/memory.py:34`

`MAX_CONFIDENCE = 0.7` caps all learned facts. Combined with decay (fact floor 0.5), newly extracted facts are penalized compared to manually set facts (confidence=1.0). The knowledge-update resolution was already handling this correctly by preferring newer values regardless of confidence, so this is lower priority.

## Proposed Fix Priority

| Priority | Fix | Effort | Expected Impact |
|---|---|---|---|
| P0 | Fix B: Remove trigger+domain dedup | ~5 lines | Prevents silent data loss; fixes clothing items (-1 missing) |
| P0 | Fix A: Historical extraction pass | ~30 lines | Populates facts on bulk load; fixes projects led (no facts) |
| P1 | Fix C: List-valued fact extraction | ~20 lines prompt + ~10 lines parser | Enables counting of multi-item facts; fixes model kits (-1 missing) |
| P2 | Fix Confidence cap review | ~5 lines | Lower priority; knowledge-update resolution handles staleness |

## Implementation Notes

### Fix A: Historical Extraction

Two approaches:

**Approach 1 (simpler):** Call extraction directly in the eval adapter after bulk loading sessions.
- Pro: No production code changes
- Con: Only benefits eval, not production use cases

**Approach 2 (preferred):** Add a method to MemoryStore that triggers extraction on unprocessed messages.
- Pro: Benefits all bulk-load scenarios
- Con: More implementation surface area

### Fix B: Content-Based Dedup

The replacement logic must handle:
1. Identical facts extracted from the same conversation segment (LLM sometimes duplicates) → dedup by hash
2. Same entity+attribute with different value across sessions → NOT deduped; supersede mechanism handles this
3. Same trigger+action with different structured_data → NOT deduped; store both

### Fix C: List-Valued Extraction Prompt

The prompt change should instruct the LLM to use JSON arrays for accumulated facts:

```
EXAMPLES:
User: "I've completed Revell F-15 Eagle and Tamiya Spitfire. I also bought B-29 bomber and 69 Camaro."
{
  "trigger": "model building update",
  "action": "listed model kits",
  "structured_data": {
    "entity": "user",
    "attribute": "completed_model_kits",
    "value": ["Revell F-15 Eagle", "Tamiya 1/48 Spitfire Mk.V"]
  }
},
{
  "trigger": "model building update", 
  "action": "listed model kits",
  "structured_data": {
    "entity": "user",
    "attribute": "purchased_model_kits",
    "value": ["1/72 scale B-29 bomber", "1/24 scale '69 Camaro"]
  }
}
```

The `_store_patterns()` parser must handle both scalar strings and JSON array values in `structured_data.value`.

## Files Changed

| File | Change |
|---|---|
| `src/sdk/middleware_memory.py:787-790` | Remove trigger+domain dedup; replace with content hash dedup |
| `src/sdk/middleware_memory.py:93-165` | Update EXTRACTION_PROMPT with list-valued fact examples |
| `src/sdk/middleware_memory.py:792-833` | Handle list-valued structured_data.value in _store_patterns() |
| `src/storage/memory.py` | (Optional) Add `extract_facts_from_messages()` method for historical bulk processing |
| `tests/evaluation/longmemeval_adapter.py` | (Alternative) Call extraction after bulk session load |

## Code Review — 2026-05-08

Each proposed fix was verified against the actual code.

### Fix B: Trigger+Domain Dedup — CONFIRMED (P0)

**Status:** Valid and critical. Code at `src/sdk/middleware_memory.py:787-790` matches the document exactly:

```python
trigger_key = (trigger.lower().strip(), domain.lower().strip())
if trigger_key in seen_triggers:
    continue
seen_triggers.add(trigger_key)
```

A pure data-loss bug: any fact sharing the same `(trigger, domain)` pair is silently dropped, even if `attribute` and `value` differ. The fix proposed (content hash dedup) is the correct approach.

### Fix C: Scalar-Only / List-Valued Extraction — CONFIRMED (P1)

**Status:** Valid. Two issues confirmed:

1. `EXTRACTION_PROMPT` (lines 93-165) has only scalar examples (`"value": "Jordan Mitchell"`, `"value": "Denver"`). No list-valued examples exist to guide the LLM.

2. `_store_patterns()` at line 795 forces all values to string: `value = str(structured_data.get("value") or action).strip()`. If the LLM returned a JSON array like `["boots", "jacket"]`, `str()` would produce Python repr `"['boots', 'jacket']"` — not parseable data.

Notably, the synthesis side (`longmemeval_synthesis.py:149`) already handles list values via `re.findall(r"'([^']*)'", value)`. The extraction pipeline just never produces them. Both the prompt and the parser need changes.

### Fix A: Historical Extraction — PARTIALLY INCORRECT (P0)

**Status:** The analysis has a factual gap. The document claims no mechanism for bulk historical extraction exists. In reality:

1. `MemoryMiddleware.extract_from_messages()` already exists at `src/sdk/middleware_memory.py:559-595` — a classmethod that extracts facts from a list of message strings (threads to `asyncio.run`, supports up to 120s timeout). This was built precisely for bulk processing.

2. The eval adapter at `tests/evaluation/longmemeval_adapter.py:211-212` has an explicit comment:
   ```
   # NOTE: Memory extraction skipped — using retrieval-only mode
   # (raw message search + ranker heuristics, no lossy LLM extraction)
   ```
   This is an architectural choice by the adapter, not a missing code path.

3. `after_agent()` (line 662) works correctly for incremental extraction during active conversations. The document is correct that it won't fire on bulk imports, but the existing `extract_from_messages()` classmethod fills this gap.

**Corrected implementation:** Rather than adding new methods to `MemoryStore` or the adapter, the eval adapter should call the existing `MemoryMiddleware.extract_from_messages(messages, user_id, workspace_id)` after each batch import. The production path (`extract_from_messages`) also benefits any system doing bulk imports via the `/conversation/import` endpoint — the import handler should be updated to call this after insertion.

### Fix Confidence Cap — CONFIRMED (P2)

**Status:** Correct, and correctly deprioritized by the document. `MAX_CONFIDENCE = 0.7` at `src/storage/memory.py:34` is confirmed. Used at `middleware_memory.py:814` in `_store_patterns()`. The knowledge-update resolution already handles staleness regardless of confidence, so this is cosmetic.

### Summary

| Fix | Status | Action |
|-----|--------|--------|
| **B** — Dedup bug | CONFIRMED | Implement as proposed: content hash dedup replacing trigger+domain |
| **C** — List-valued extraction | CONFIRMED | Update prompt + make `value` parsing list-aware in `_store_patterns()` |
| **A** — Historical extraction | EXISTS | Call existing `MemoryMiddleware.extract_from_messages()` in adapter and `/conversation/import` handler |
| Confidence cap | CONFIRMED | Low priority; no action needed |