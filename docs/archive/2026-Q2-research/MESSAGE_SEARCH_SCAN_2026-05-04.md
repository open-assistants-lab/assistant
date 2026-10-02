# Message Search — Scan Findings

**Date:** 2026-05-04
**Files:** `src/storage/messages.py`, `src/sdk/tools_core/memory.py`, `src/sdk/middleware_memory.py`

---

## Issue 1: `_llm_expand_queries` is silently broken (CRITICAL)

**Status:** Fixed on 2026-05-07. Query expansion now calls the SDK provider `chat()` API with `Message.user(...)` instead of the removed LangChain `invoke()` API.

**File:** `src/sdk/tools_core/memory.py:31-66`

```python
def _llm_expand_queries(query):
    model = create_model_from_config(model_str)       # returns LLMProvider
    result = model.invoke(prompt)                     # ← AttributeError!
```

`create_model_from_config` returns `LLMProvider` (OllamaLocal, OpenAIProvider, etc.). SDK
providers use `chat()` and `achat()`. **There is no `invoke()` method.** The `AttributeError`
is caught by `except Exception: pass` at line 64 and silently returns `None`. This has been
broken since LangChain removal (Phase 8).

### Impact

Every message search in the system relies on `_regex_expand_queries` (the fallback). The
LLM-driven query rephrasing has **never actually run** in the SDK era. Search recall is
degraded for nuanced queries. The `MEMORY_EXPANSION_MODEL` environment variable is dead.

### Fix

Replace `model.invoke(prompt)` with `model.chat(prompt)` (sync) or `await model.achat(prompt)`
(async context):

```python
# Option A: sync (for non-async callers)
response = model.chat([Message.user(prompt)])
text = response.content

# Option B: async (for middleware callers)
response = await model.achat([Message.user(prompt)])
text = response.content
```

Option B requires making `_expand_queries` async throughout the call chain, but the
middleware already runs in async context (`abefore_model`).

---

## Issue 2: Double recency weighting on search results

**Status:** Fixed on 2026-05-07. `memory_search()` no longer applies a second post-search recency multiplier; recency weighting remains inside `MessageStore.search_hybrid()`/HybridDB.

**Files:** `src/storage/messages.py:179-192`, `src/sdk/tools_core/memory.py:396-410`

HybridDB's `search()` applies recency weighting via `_compute_recency`:

```python
# messages.py:179 (inside search_hybrid)
rows = self.db.search("messages", "content", query,
    recency_weight=0.3, recency_column="ts", ...)
```

Then `memory_search` applies a SECOND multi-level recency boost:

```python
# memory.py:396-410
for r in all_results:
    days_ago = (today - r.ts.date()).days
    if days_ago <= 1:    r.score *= 1.2
    elif days_ago <= 3:  r.score *= 1.1
    elif days_ago <= 7:  r.score *= 1.05
    elif days_ago <= 30: r.score *= 0.95
    else:                r.score *= 0.85
```

The same recency signal is counted twice. A result from yesterday gets a boost from
`_compute_recency` (inside HybridDB) AND a 1.2x multiplier here.

### Impact

Very recent messages dominate results more than intended. A 1-day-old vaguely-relevant
message outranks a 3-day-old highly-relevant message.

### Fix

Remove the post-search recency boost from `memory_search` (lines 396-410) — the
`recency_weight` parameter already handles this in HybridDB. Or remove `recency_weight`
from `search_hybrid` and keep only the post-search adjustment. Don't do both.

---

## Issue 3: High-confidence facts suppress message search

**Status:** Fixed on 2026-05-07. `memory_search()` now searches messages even when high-confidence facts are present, so facts and conversation snippets are blended in the response.

**File:** `src/sdk/tools_core/memory.py:362-366`

```python
high_conf_facts = [m for m in fact_results if not m.is_superseded and m.confidence > 0.6]
if len(high_conf_facts) < 3:
    # ... search messages ...
```

If 3+ structured facts match the query, the ENTIRE conversation message search is skipped.
The agent sees structured facts but not the raw conversation context. This is an all-or-nothing
switch — no blending of facts and conversation snippets.

### Impact

For well-indexed users with many facts, message search is never used. The agent can't find
recent conversational nuance because it only sees structured fact triples. Example:
"what did Eddy say about the deployment pipeline" → facts might have the pipeline name but
not the conversation context.

### Fix

Blend facts and messages proportionally rather than using an all-or-nothing threshold:

```python
message_limit = max(2, 8 - len(high_conf_facts))
# Always search messages, just reduce limit when facts are abundant
```

---

## Issue 4: Cross-workspace penalty overrides hybrid score

**Status:** Fixed on 2026-05-07. Cross-workspace results now use a softer `0.95` score multiplier and remain tagged with their workspace for transparency.

**File:** `src/sdk/tools_core/memory.py:392`

```python
r.score *= 0.85  # slight penalty for non-primary workspace
```

The score from hybrid search (FTS5 bm25 + ChromaDB cosine + recency) is multiplied by 0.85
for cross-workspace results. A highly relevant result from a secondary workspace (score 0.95)
drops to 0.81. Combined with the recency double-count (Issue 2), this means cross-workspace
results are systematically suppressed regardless of relevance.

### Fix

Apply the penalty BEFORE the final sort and keep it proportional:

```python
r.score *= 0.95  # softer penalty
r._workspace = ws_id  # tag for transparency
```

---

## Issue 5: `memory_get_history` silently returns on empty range

**Status:** Fixed on 2026-05-07. `memory_get_history()` now distinguishes an empty persisted store from an empty date/range result via `count_messages()`.

**File:** `src/sdk/tools_core/memory.py:289-290`

```python
if not messages:
    return f"No messages found for {date_str}"
```

If no messages exist for a date range, the function returns a text message. But memoization
at the tool level means the agent treats this as a definitive answer — there really were no
messages that day. If the message store was never populated (CLI path, Issue 3 from earlier
scan), the agent incorrectly concludes "nothing happened that day."

### Fix

Distinguish "no messages in store" from "empty date range":

```python
if not messages:
    total = conversation.count_messages()
    if total == 0:
        return "No conversation history available (messages have not been persisted)"
    return f"No messages found for {date_str} (total messages in store: {total})"
```

---

## Summary

| # | Type | Severity | Lines | Impact |
|---|---|---|---|---|
| 1 | Bug | **CRITICAL** | memory.py:51 | LLM query expansion silently broken since Phase 8 |
| 2 | Bug | MEDIUM | memory.py:396-410 | Double recency weighting skews results |
| 3 | Design | MEDIUM | memory.py:366 | 3+ facts suppress all message search |
| 4 | Design | LOW | memory.py:392 | Cross-workspace penalty too aggressive |
| 5 | Design | LOW | memory.py:289 | Empty store indistinguishable from empty range |
