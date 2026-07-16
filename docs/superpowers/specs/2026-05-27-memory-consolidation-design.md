# Memory Consolidation — Design Spec

**Author:** Eddy
**Date:** 2026-05-27
**Status:** Approved

---

## 0. Motivation

The current memory system has three overlapping storage layers (MessageStore, ObservationStore, MemoryStore) with cross-store side-push dependencies, a disabled LLM extraction pipeline (proved actively harmful: -35pp accuracy), and confused tool naming (`memory_search` searches conversations, not memories). Industry converges on observation-based memory (Mastra OM at 94.87% LongMemEval, Mem0 V3 at 94.8%) with text-first storage and background-agents for extraction.

This design consolidates observation, memory, and insight into a unified pipeline and store with two clean tiers: **observation** (perception) and **reflection** (processing).

---

## 1. Tool Renames (MessageStore Tools)

Tools that read from `MessageStore` (raw conversations) are renamed from `memory_*` to `message_*`. Implementation unchanged — rename only.

| Old Name | New Name | Reads From |
|----------|----------|-----------|
| `memory_search` | `message_search` | MessageStore via memcore (hybrid + heuristics) |
| `memory_count` | `message_count` | MessageStore via hybrid search + entity counting |
| `memory_get_history` | `message_history` | MessageStore date range queries |

These move from `src/sdk/tools_core/memory.py` to new file `src/sdk/tools_core/message.py`.

Registration changes in `src/sdk/native_tools.py`.

---

## 2. Unified MemoryStore

Merge `ObservationStore` (182 lines) and `MemoryStore` (1882 lines) into a single `MemoryStore` with two tables in one HybridDB. Uses `paths.user_memory_dir()` → `~/Assistant/Memory/global/` (same path as current MemoryStore, consistent with existing `user_memory_dir()`).

No separate compression step. Observations are the source of truth. The LLM searches observations via HybridDB at query time — relevance ranking replaces summarization.

### 2.1 Schema

**`observations`** — Episodic text records produced by Observer (perception)

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | `obs_{uuid12}` |
| `content` | LONGTEXT | Factual text, one fact per observation |
| `priority` | TEXT | 🔴 (high/precise), 🟡 (medium/preference), 🟢 (low/skipped) |
| `observation_ts` | TEXT | ISO timestamp when Observer created this |
| `referenced_date` | TEXT | Date mentioned in the content, or empty |
| `relative_date` | TEXT | Computed relative offset, or empty |
| `source_message_range` | TEXT | Message IDs this observation covers |
| `created_at` | TEXT | ISO timestamp |

**`reflections`** — Synthesized patterns produced by Reflector (processing)

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | `refl_{uuid12}` |
| `content` | LONGTEXT | Synthesized pattern, relationship, or prediction |
| `domain` | TEXT | Category (preference, career, lifestyle, etc.) |
| `linked_observation_ids` | JSON | Observation IDs contributing to this reflection |
| `confidence` | REAL | 0.0–1.0, boosted on access |
| `decay_rate` | REAL | Weekly decay multiplier |
| `access_count` | INTEGER | Times accessed via `memory_reflection` |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |
| `last_accessed_at` | TEXT | ISO timestamp |

Confidence model:
- Initial: 0.6 when created
- Access boost: `c + 0.1 * (1 - c)` on each access
- Weekly decay: `c - decay_rate * weeks` (decay_rate = 0.05)
- Soft-delete: `confidence <= 0.1` (filtered from queries; row remains)

### 2.2 Key Methods

```
insert_observations(observations: list[dict]) → int
    Stores observations.

get_recent_observations(days=7, limit=50) → list[dict]
get_all_observations() → list[dict]

search_observations(query, limit) → list[dict]
    FTS5 keyword search over observation text.
    Primary retrieval path — relevance ranking replaces compression.

insert_reflections(reflections: list[dict]) → int
    Stores reflection rows from Reflector.

search_reflections(query, method="hybrid", limit=5) → list[dict]
    method: fts | semantic | hybrid (default)

boost_reflection(reflection_id) → None
    Access-based confidence reinforcement.
```

### 2.3 What Gets Retired

| File | Lines | Fate |
|------|-------|------|
| `src/storage/memory.py` | 1882 | Rewritten (same file, new content) |
| `src/storage/observation.py` | 182 | Merged into new memory.py |
| `src/storage/consolidation.py` | 393 | Merged into Reflector |
| `src/sdk/memory_planner.py` | 271 | Deleted (no longer needed) |
| `src/sdk/memory_ranker.py` | 413 | Deleted (no longer needed) |
| `src/sdk/middleware_memory.py` | 960 | Deleted (already disabled, now removed permanently) |

Removed concepts: `Memory` dataclass (trigger/action pairs, 20 fields), `Connection` dataclass (graph edges), `Insight` dataclass, confidence chains, supersession tracking, memory graph, compression stage.

---

## 3. Pipeline — Observer → Reflector

Two background agents, two-tiers, one pipeline. Both read `MessageStore` and write to unified `MemoryStore`.

### 3.1 Observer (Perception)

**When**: Fires when cumulative unobserved tokens since last Observer run exceed 8K AND at least 3 turns have passed. Cumulative — uses `_count_unobserved_tokens()` (same as current summarization trigger pattern). Never blocks the main agent.

**Input**: Last 30 days / 500 messages from `MessageStore`, plus previous observations (for dedup).

**Output**: JSON array of observations. Each observation: single fact, exact value, 🔴🟡🟢 priority.

**Model**: `OBSERVER_MODEL` env var, defaults to `DEFAULT_MODEL`. Flash-tier recommended.

**Prompt**: `OBSERVER_PROMPT` in `src/sdk/tools_core/observation.py`.

### 3.2 Reflector (Processing)

**When**: Every 24 hours (configurable later — hourly, daily, weekly, monthly, quarterly via `REFLECTOR_INTERVAL` env var). Runs as a scheduled background task. Offline — latency doesn't matter.

**Input**: New observations since last Reflector run from `MemoryStore`, plus all prior reflections as context (bounded — reflections are compact). Cross-cycle scope — sees patterns no single Observer run produces.

**Output**: Reflections stored in `reflections` table. Discovers patterns, career arcs, value systems, contradictions, predicted needs. Contradictions are noted in reflection content ("previously observed X, now Y as of DATE") but contradictory observations are not deleted — both persist with their timestamps for the LLM to resolve at query time.

**What distinguishes reflection from observation**: An observation says "lives in Denver." A reflection says *why this matters* — "User relocated twice in 3 years for family reasons; values proximity to good schools above cost."

**Model**: `REFLECTOR_MODEL` env var, defaults to `DEFAULT_MODEL`. Best model available — offline, no latency constraint.

**Prompt**: `REFLECTOR_PROMPT` in `src/sdk/tools_core/observation.py`. Reads all observations; produces reflections with `content`, `domain`, `linked_observation_ids`.

### 3.3 The Two Stages — Concrete Distinction

| | Observer (Perception) | Reflector (Processing) |
|---|---|---|
| **Scope** | This batch of messages | All observations across all time |
| **What it does** | Extracts individual facts | Discovers patterns, relationships, arcs |
| **Example output** | "lives in Denver" | "Relocated twice in 3 years for family reasons; prioritizes good schools and outdoor lifestyle over career advancement" |
| **Analogy** | Taking notes during a conversation | Reflecting on months of notes, forming understanding |
| **Runs** | Every ~8K tokens of new conversation | Every 24 hours (configurable: hourly, daily, weekly, monthly, quarterly) |
| **Model** | Cheap (flash tier) | Best (offline) |
| **Cost** | Frequent, cheap calls | Rare, expensive calls |

### 3.4 Why No Compression Between Them

Compression loses detail. HybridDB search replaces summarization — when the LLM queries "what do you know about Denver?", FTS5 returns the top N most relevant observations. The LLM synthesizes at query time from fresh, lossless data.

### 3.5 No Auto-Injection

The ObservationMiddleware `before_agent()` (working memory injection) is **removed**. The agent must explicitly call:
- `memory_profile` for user context (recent observations)
- `memory_reflection` for pattern search
- `message_search` for raw conversation lookup

Observer and Reflector run silently in background.

---

## 4. Tool API

### 4.1 Message Tools (`src/sdk/tools_core/message.py`)

```
message_search(query, limit=5) → str
    Reads MessageStore via memcore. Hybrid search + heuristics.
    Annotations: read_only=True, idempotent=True

message_count(query) → str
    Reads MessageStore via hybrid. Deterministic entity extraction + counting.
    Annotations: read_only=True, idempotent=True

message_history(days=7, date_str=None) → str
    Reads MessageStore date range queries.
    Annotations: read_only=True, idempotent=True
```

### 4.2 Memory Tools (`src/sdk/tools_core/memory.py`)

```
memory_profile() → str
    Returns: recent observations (7d). Answers "what do you know about me right now?"
    Annotations: read_only=True, idempotent=True

memory_reflection(query, method="hybrid", limit=5) → str
    Searches reflections table. FTS, semantic, or hybrid.
    Boosts accessed reflections' confidence on read.
    Annotations: read_only=True, idempotent=True
```

### 4.3 Retired Tools

| Tool | Reason |
|------|--------|
| `memory_search_all` | Cross-store aggregation — no longer needed with unified store |
| `memory_search_all_workspaces` | Can be re-added later if cross-workspace reflection search is needed |
| `memory_connect` | Was for Memory object graph edges; no graph in new design |
| `memory_search_insights` | Renamed to `memory_reflection` |

---

## 5. Storage Layout

```
~/Assistant/
├── Conversation/
│   └── app.db              # MessageStore (HybridDB) — unchanged
├── Memory/
│   └── global/
│       ├── app.db          # MemoryStore (HybridDB) — unified
│       │   Tables: observations, reflections
│       └── vectors/        # ChromaDB for reflection semantic search
└── Workspaces/{workspace_id}/
    └── conversation.app.db # Workspace MessageStore — unchanged
```

Memory uses `paths.user_memory_dir()` (`~/Assistant/Memory/global/`). This replaces both the old `MemoryStore` path and the old `ObservationStore` path (`Workspaces/{id}/Memory/observations/`). Observations and reflections are global, not per-workspace.

---

## 6. Files to Create / Modify / Delete

### Create
- `src/sdk/tools_core/message.py` — `message_search`, `message_count`, `message_history`

### Modify
- `src/storage/memory.py` — rewritten: unified MemoryStore (2 tables, ~300 lines)
- `src/sdk/tools_core/memory.py` — rewritten: `memory_profile`, `memory_reflection`
- `src/sdk/tools_core/observation.py` — add Reflector prompt (`REFLECTOR_PROMPT`) and `run_reflector()`; update Observer prompt
- `src/sdk/middleware_observation.py` — remove `before_agent()`; add Reflector trigger to `after_agent()`; remove compression trigger
- `src/sdk/native_tools.py` — update registration: message tools from message.py, memory tools from memory.py

### Delete
- `src/storage/observation.py` — merged into new memory.py
- `src/storage/consolidation.py` — merged into Reflector
- `src/sdk/memory_planner.py` — no longer needed
- `src/sdk/memory_ranker.py` — no longer needed
- `src/sdk/middleware_memory.py` — already disabled, permanently removed

---

## 7. Migration

1. Create new MemoryStore in `src/storage/memory.py` (fresh start — old data not compatible)
2. Move message tools to `src/sdk/tools_core/message.py`, rename to `message_*`
3. Rewrite memory tools in `src/sdk/tools_core/memory.py`
4. Update `ObservationMiddleware` — remove `before_agent`, remove compression, add Reflector trigger
5. Update `native_tools.py` registrations
6. Delete old files
7. Run tests: `uv run pytest tests/sdk/ -v`

No data migration. The trigger/action Memory model is incompatible with observation-based memory.
