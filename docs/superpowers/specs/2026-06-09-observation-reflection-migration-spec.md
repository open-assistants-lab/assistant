# Migrate EA Observation/Reflection to OSS CoreMem Pipelines

**Date:** 2026-06-09
**Status:** Draft
**Motivation:** EA vendors three custom files (`src/storage/memory.py`, `src/sdk/middleware_observation.py`, `src/sdk/tools_core/observation.py`) that duplicate CoreMem's `ObserverPipeline` and `ReflectorPipeline` with worse defaults, no alignment gate, no classification, no dedup, and no hybrid reflector trigger. CoreMem `>=0.7.1` is already a dependency — EA just doesn't use its pipelines.

---

## 1. Current Architecture (To Be Removed)

```
MessageStore (MemoryCore @ conversation/)
  └── messages table

MemoryStore (HybridDB @ memory/)         ← CUSTOM, to DELETE
  ├── observations table (custom schema)
  └── reflections table (custom schema)

ObservationMiddleware (custom trigger)
  ├── _fire_observer() → run_observer()
  │     └── single LLM call, 8K threshold, 3-turn debounce
  └── _fire_reflector() → run_reflector()
        └── single LLM call, 24h-only trigger

memory_profile / memory_reflection tools
  └── read from MemoryStore
```

**3 files to delete (plus CoreMem PR):**
- `src/storage/memory.py` (MemoryStore, `get_memory_store`, `clear_memory_store_cache`)
- `src/sdk/tools_core/observation.py` (run_observer, run_reflector, prompts)
- `src/sdk/middleware_observation.py` (ObservationMiddleware — no longer needed)

---

## 1a. CoreMem Changes Required PR

A PR to `open-assistants-lab/CoreMem` adding a background polling loop to `ObserverPipeline` (mirroring `ReflectorPipeline`):

### `ObserverPipeline.start()` / `.stop()` / `._run_loop()`

```python
async def start(self) -> None:
    """Start background observer worker. Idempotent."""
    if self._task is not None and not self._task.done():
        return
    self._task = asyncio.create_task(self._run_loop())

async def stop(self) -> None:
    """Stop background observer worker. Idempotent."""
    if self._task is None:
        return
    if self._task.done():
        self._task = None
        return
    self._task.cancel()
    try:
        await asyncio.wait_for(self._task, timeout=30.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    self._task = None

async def _run_loop(self) -> None:
    """Background loop: poll for new messages and extract observations.
    Auto-restarts on crash with exponential backoff."""
    backoff = 1.0
    max_backoff = 60.0
    while True:
        try:
            await asyncio.sleep(60.0)
            await self.extract()
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Observer worker error, restarting in %ss: %s", backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
```

### `MemoryCore.start_pipelines()` / `.stop_pipelines()`

```python
async def start_pipelines(self) -> None:
    """Start background observer and reflector workers."""
    if self._observer_pipeline:
        await self._observer_pipeline.start()
    if self._reflector_pipeline:
        await self._reflector_pipeline.start()

async def stop_pipelines(self) -> None:
    """Stop background observer and reflector workers."""
    if self._observer_pipeline:
        await self._observer_pipeline.stop()
    if self._reflector_pipeline:
        await self._reflector_pipeline.stop()
```

---

## 2. Target Architecture

```
MessageStore (MemoryCore @ conversation/)
  ├── messages table
  ├── observations table (CoreMem _OBSERVATIONS_SCHEMA — 25 cols)
  ├── observation_events table
  ├── observation_conflicts table
  └── reflections table (CoreMem _REFLECTIONS_SCHEMA — 8 cols)

No separate MemoryStore. No separate DB for memory.
No ObservationMiddleware — both pipelines run as background workers owned by MemoryCore.

Both pipelines owned by MemoryCore:
  ├── coremem.observer.ObserverPipeline (background polling every 60s)
  │     ├── token_threshold=100 (instead of 8000)
  │     ├── min_turns=1 (instead of 3)
  │     ├── 5 parallel LFs (entities, actions, preferences, temporal, sentiment)
  │     ├── alignment-gated source quotes (EXACT/FUZZY/NONE)
  │     ├── classification + durability filter (12 memory_types)
  │     └── semantic dedup + merge (duplicate/refine/supersede/contradict)
  │
  └── coremem.reflector.ReflectorPipeline (background polling every 60s)
        ├── interval_hours=24 (unchanged)
        ├── trigger_every_n_observations=50 (new — hybrid OR trigger)
        ├── min_observations=10 (unchanged)
        ├── priority sampling at >200 obs
        └── cosine similarity dedup quality gate (requires embedding_fn)

memory_profile / memory_reflection tools
  └── read from MemoryCore via MessageStore
```

---

## 3. Schema Changes

### 3.1 New tables (auto-created by MemoryCore with `enable_observations=True`)

Observations (`_OBSERVATIONS_SCHEMA` — 25 columns):

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | uuid |
| `kind` | TEXT | 'fact' or 'entity' |
| `content` | LONGTEXT | The fact text |
| `source_quote` | TEXT | Verbatim quote from conversation |
| `source_fact_ids` | TEXT | JSON array |
| `source_message_ids` | TEXT | JSON array |
| `referenced_date` | TEXT | Extracted date reference |
| `observation_ts` | TEXT | When observed (ISO) |
| `user_id` | TEXT | Who this is about |
| `agent_id` | TEXT | Which agent observed |
| `session_id` | TEXT | Conversation session |
| `alignment_tier` | TEXT | EXACT / FUZZY / NONE |
| `alignment_confidence` | REAL | 0.0-1.0 |
| `importance` | REAL | 0.0-1.0 (replaces emoji priority) |
| `confidence` | REAL | Default 0.800 |
| `memory_type` | TEXT | profile / preference / project / ... |
| `durability` | TEXT | durable / temporary |
| `sensitivity` | TEXT | normal / personal / sensitive |
| `status` | TEXT | candidate / active / superseded / archived |
| `valid_from` | TEXT | Temporal validity start |
| `valid_to` | TEXT | Temporal validity end |
| `superseded_by` | TEXT | ID of superseding observation |
| `entities` | TEXT | JSON array |
| `reflected` | INTEGER | 0 or 1 |
| `embedding` | TEXT | JSON float array |

Reflections (`_REFLECTIONS_SCHEMA` — 8 columns):

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | uuid |
| `content` | LONGTEXT | Synthesized insight |
| `domain` | TEXT | Category label |
| `linked_observation_ids` | TEXT | JSON array |
| `score` | REAL | Confidence (replaces Assistant's confidence+decay_rate) |
| `embedding` | TEXT | For cosine similarity dedup |
| `user_id` | TEXT | |
| `session_id` | TEXT | |

Plus `observation_events` and `observation_conflicts` tables.

### 3.2 EA columns dropped

| EA Column | Replaced By |
|-----------|-------------|
| `priority` (emoji: 🔴🟡🟢) | `importance` (float 0.0-1.0) |
| `relative_date` | `referenced_date` (already exists) |
| `source_message_range` | `source_message_ids` + `source_quote` |
| `confidence` (reflections, 0.6) | `score` (reflections, 1.0) |
| `decay_rate` (reflections, 0.05) | CoreMem's `apply_decay` (flat 0.9 multiplier, half-life model) |
| `access_count` / `last_accessed_at` | Not needed — CoreMem doesn't boost on access |
| `updated_at` | Not needed — observations are append-only with supersede |

### 3.3 Data migration

Existing `data/users/{user_id}/memory/app.db` needs to be migrated into `data/users/{user_id}/conversation/app.db`:

1. **Copy observations** — raw SQL `INSERT INTO conversation.observations SELECT ... FROM memory.observations` with:
   - `priority` emoji → `importance` float: `🟢→0.3, 🟡→0.5, 🔴→0.8`
   - `source_message_range` → `source_message_ids` (parse range string to JSON array)
   - Preserve existing `id` values (CoreMem uses `str(uuid.uuid4())[:12]`, EA uses `uuid.uuid4().hex[:12]` — different format)
   - Set new columns to defaults: `kind='fact'`, `durability='durable'`, `status='candidate'`, `reflected=0`, `entities='[]'`, `confidence=0.800`

2. **Copy reflections** — raw SQL `INSERT INTO conversation.reflections SELECT ... FROM memory.reflections` with:
   - `confidence` → `score` (same value)
   - `linked_observation_ids` — ensure JSON array format (EA stores both string and JSON)
   - Preserve existing `id` values (CoreMem's `insert_reflections()` ignores `item.get("id")` — must use raw SQL)
   - Set new columns to defaults: `user_id=''`, `session_id=''`, `embedding=''`

3. **Mark migrated** — write a sentinel file or DB flag so migration runs once
4. **Keep old DB** for rollback — do not delete `memory/app.db`

---

## 4. Detailed Changes

### 4.1 MessageStore changes (`src/storage/messages.py`)

- `MemoryCore` constructor gets `enable_observations=True`, `enable_reflections=True`, `observation_kwargs={"session_id": ""}`
- Add `_migrate_memory_store()` that copies data from old `memory/app.db` into `conversation/app.db` (run once, gated by sentinel)
- **Expose `core` as a public property** — tools need access to `MemoryCore.observations()` and `.reflections()`
- **Start pipelines after init** — `MessageStore.__init__` is synchronous. Wrap in a helper: check for running event loop with `asyncio.get_running_loop()`, and if found, `asyncio.create_task(core.start_pipelines())`. If no loop, defer — pipelines start on first message ingest. This fires up both background workers (poll every 60s).
- **Stop pipelines on shutdown** — `core.stop_pipelines()` in any cleanup path (optional, for clean teardown)

### 4.2 Delete `src/sdk/middleware_observation.py`

No replacement needed. Both pipelines run as background workers owned by `MemoryCore`.

### 4.3 Delete `src/storage/memory.py`

- `MemoryStore` class removed
- `get_memory_store()` and `clear_memory_store_cache()` removed
- All callers switch to `MessageStore` + `MemoryCore` methods

### 4.4 Delete `src/sdk/tools_core/observation.py`

- `run_observer`, `run_reflector`, prompts removed

### 4.5 Coordinator updated (`src/sdk/coordinator.py`)

- Remove `ObservationMiddleware` import and construction (lines 437-474). Middleware no longer exists — pipelines run as background workers in `MemoryCore`.

### 4.6 Runner updated (`src/sdk/runner.py`)

- Remove `ObservationMiddleware` import and construction at lines 29, 365. Middleware no longer exists.

### 4.7 Memory tools updated (`src/sdk/tools_core/memory.py`)

```python
def _get_core(user_id, workspace_id):
    from src.storage.messages import get_message_store
    return get_message_store(user_id, workspace_id).core

@tool
def memory_profile(user_id="default_user", workspace_id="personal"):
    """Return observations about the user — may be empty if Observer hasn't run.

    Returns recent observations collected by the Observer. If no observations
    are available, use message_search to find specific facts from conversation
    history instead.

    Use when the user asks "what do you know about me?" or the agent needs
    to refresh its understanding of the user's context.

    Args:
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
    """
    core = _get_core(user_id, workspace_id)
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    results = core.get_observations(ts_after=cutoff, limit=50)
    if not results:
        return "No observations available. Try message_search to find specific facts from conversation history."
    parts = ["## Working Memory (Recent Observations)\n"]
    for obs in results:
        importance = float(obs.get("importance", 0.3))
        ts = str(obs.get("observation_ts", ""))[:10]
        content = str(obs.get("content", ""))
        parts.append(f"[{importance:.0%}] {ts} {content}")
    return "\n".join(parts)

@tool
def memory_reflection(query, method="hybrid", limit=5, user_id="default_user", workspace_id="personal"):
    """Search synthesized reflections — patterns and insights about the user.

    Reflections are higher-order patterns discovered by the Reflector from
    analyzing observations across time. May be empty if the Reflector hasn't
    run yet (requires 10+ observations and 24h interval or 50 unreflected facts).

    Use when looking for themes, trends, or synthesized understanding about
    the user. For specific fact recall, use message_search instead.

    Args:
        query: What to search for (e.g., "career", "relationships", "habits")
        method: Search method (fts, semantic, or hybrid) — default: hybrid
        limit: Maximum results (default: 5)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
    """
    core = _get_core(user_id, workspace_id)
    results = core.reflections(query=query, limit=limit)
    if not results:
        return f"No reflections found for: {query}"
    parts = [f"## Reflections for '{query}'\n"]
    for i, refl in enumerate(results, 1):
        score = float(refl.get("score", 1.0))
        domain = str(refl.get("domain", ""))
        content = str(refl.get("content", ""))
        parts.append(f"{i}. [{domain}] {content} (confidence: {score:.0%})")
    return "\n".join(parts)
```

**Changes from current:**
- `method` parameter becomes a no-op — CoreMem always uses hybrid search. Keep for backward compat but document as ignored.
- `boost_reflection()` removed — CoreMem doesn't boost on access.
- `confidence` → `score` in display.
- `days` filter computed manually via `ts_after` — CoreMem's `get_observations()` doesn't have a `days` parameter.
- No emoji priority — use `importance` as a percentage label `[80%]` instead.

### 4.8 HTTP router updated (`src/http/routers/memories.py`)

- Replace `get_memory_store(user_id, workspace_id)` with `get_message_store(user_id, workspace_id).core`
- `list_observations`: `core.get_observations(ts_after=cutoff, limit=limit)` instead of `store.get_recent_observations(days=days, limit=limit)`
- `list_reflections`: `core.get_reflections(limit=limit)` instead of `store.get_reflections(limit=limit)`
- `search_reflections`: `core.search_reflections(query, limit=limit)` instead of `store.search_reflections(query, method, limit)`. Remove `boost_reflection()` call.
- `search_observations`: `core.search_observations(query, limit=limit)` instead of `store.search_observations(query, limit=limit)`
- `clear_memories`: Replace `clear_memory_store_cache()` with `core.clear()` + raw SQL deletes for observations/reflections — deletes all messages, observations, and reflections for the user. Workspace-level add/read/update/delete for observations and reflections is handled via the existing HTTP endpoints. Note: `MemoryCore.clear()` only deletes messages; observations/reflections need explicit `DELETE FROM observations` and `DELETE FROM reflections`.

### 4.9 Test updates

- `tests/sdk/test_memory.py` — update patches from `src.storage.memory.get_memory_store` to `src.storage.messages.get_message_store`
- `tests/sdk/test_workspace_isolation.py` — `MemoryStore` test at line 75 needs to be rewritten for `MessageStore` + `MemoryCore`
- `tests/perf/perf_instrument.py` — instruments `MemoryStore.__init__` (line 168). Update to instrument `MessageStore` or `MemoryCore` instead.
- `tests/benchmarks/longmemeval/eval.py` — imports `run_observer` from `tools_core.observation.py` (line 523) and uses it directly as `await run_observer(chunk, obs_model, all_obs)`. Replace with `ObserverPipeline.extract()` — or since this is a benchmark that wants synchronous invocation, create a temporary `ObserverPipeline(memory=..., session_id="")` and call `extract()` on it.
- `tests/sdk/test_tool_contracts.py` — `memory_profile` and `memory_reflection` tests at lines 292-301 — update patches.
- `tests/sdk/test_subagent_v1.py` — no changes needed. `memory_profile`/`memory_reflection` tool exclusions in subagent config are unrelated to this migration.
- `tests/sdk/test_coordinator.py` — remove `ObservationMiddleware` mock/assert if present.

---

## 5. File Manifest

| Action | File | Reason |
|--------|------|--------|
| DELETE | `src/storage/memory.py` (244 lines) | Entirely replaced by MemoryCore |
| DELETE | `src/sdk/tools_core/observation.py` (187 lines) | Replaced by ObserverPipeline |
| DELETE | `src/sdk/middleware_observation.py` (164 lines) | No longer needed — pipelines are background workers |
| MODIFY | `src/storage/messages.py` | Enable observations on MemoryCore + migration + public `core` property + start pipelines |
| MODIFY | `src/sdk/tools_core/memory.py` (~95 lines) | Switch to `core.observations()`/`reflections()`, remove `boost_reflection()` |
| MODIFY | `src/http/routers/memories.py` | Replace `get_memory_store` → `MessageStore.core` |
| MODIFY | `src/sdk/coordinator.py` | Remove ObservationMiddleware import and construction |
| MODIFY | `src/sdk/runner.py` | Remove ObservationMiddleware import and construction |
| MODIFY | `tests/sdk/test_memory.py` | Update patches |
| MODIFY | `tests/sdk/test_workspace_isolation.py` | Rewrite MemoryStore test |
| MODIFY | `tests/perf/perf_instrument.py` | Update instrument target |
| MODIFY | `tests/benchmarks/longmemeval/eval.py` | Update observer import |
| MODIFY | `tests/sdk/test_tool_contracts.py` | Update patches |

---

## 6. Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Schema migration fails for existing users | Low | Keep old DB file, add rollback sentinel |
| CoreMem ObserverPipeline blocks on long runs | Low | Uses `asyncio.gather` with `return_exceptions=True` |
| Different observation format breaks tools | Medium | CoreMem observations have more fields; display code must handle both |
| `importance` vs emoji priority breaks formatting | Low | Use `importance` as percentage label `[80%]` instead of emoji |
| `score` vs `confidence` breaks reflection output | Low | Show as percentage either way |
| **Provider mismatch** — CoreMem uses its own factory | Medium | `ObserverPipeline` uses `create_provider("deepseek:deepseek-v4-flash")`, not Assistant's `create_model_from_config()`. If EA uses a different model, observations use a different provider. Mitigation: pass `observation_model` and `reflect_model` to `MemoryCore` constructor. |
| **Workspace scoping lost** — `MessageStore` is per-user | Medium | Assistant's `MemoryStore` is per-workspace. After migration, observations are shared across workspaces. Mitigation: add `workspace_id` filtering to `MemoryCore` observation queries, or accept shared observations as a simplification. |
| **`session_id=""` matches all messages** | Low | EA doesn't use session IDs. `MemoryCore.ingest()` defaults to `session_id=""`. ObserverPipeline fetches all messages. Correct behavior. |
| **`embedding_fn` not provided** — quality gate skipped | Low | Cosine similarity dedup in ReflectorPipeline is skipped. Observations still deduped by ObserverPipeline's Phase 5. |

---

## 7. Success Criteria

| Metric | Before | After |
|--------|--------|-------|
| Observation trigger threshold | 8K tokens / 3 turns | 100 tokens / 1 turn |
| Observation LLM calls per turn | 1 call (single-pass) | 5 parallel LFs + batched relations |
| Dedup strategy | Prompt-based ("don't repeat") | Semantic dedup + merge (duplicate/refine/supersede) |
| Source quote verification | None | 3-tier alignment gate (EXACT/FUZZY/NONE) |
| Classification | None | 12 memory_types + durability + sensitivity |
| Reflector trigger | 24h only | 24h OR 50 unreflected facts |
| Reflector quality gate | None | Cosine similarity dedup (if `embedding_fn` provided) |
| Duplicate observation code | 595 lines | 0 lines (deleted) |
| Data DBs per user | 2 (messages + memory) | 1 (messages only) |

---

## 8. Implementation Order

1. **Add `start()`/`stop()`/`_run_loop()` to CoreMem's `ObserverPipeline`** — PR to `open-assistants-lab/CoreMem`
2. **Add `start_pipelines()`/`stop_pipelines()` to CoreMem's `MemoryCore`** — same PR
3. **Update `coremem` dependency** — point EA at the new version with the background loop
4. **Schema migration** — Add `_migrate_memory_store()` to `MessageStore.__init__`, copy data from old `memory/app.db` to `conversation/app.db` using raw SQL to preserve IDs
5. **Enable observations on MemoryCore** — `enable_observations=True, enable_reflections=True` in `MessageStore`, expose `core` as public property, call `asyncio.create_task(core.start_pipelines())` after init
6. **Update memory tools** — Switch from `MemoryStore` to `MemoryCore`, remove `boost_reflection()`, use `importancescore` percentage label
7. **Update HTTP router** — Replace `get_memory_store` with `MessageStore.core`
8. **Delete old files** — `storage/memory.py`, `tools_core/observation.py`, `middleware_observation.py`
9. **Fix remaining callers** — `coordinator.py`, `runner.py`, `perf_instrument.py`, `longmemeval/eval.py`
10. **Update tests**
11. **Run validation** — Ensure `memory_profile` and `memory_reflection` tools work end-to-end

---

## 9. Open Questions

1. **Provider model** — Should `ObserverPipeline` use Assistant's configured model (via `create_model_from_config()`) or CoreMem's default `"deepseek:deepseek-v4-flash"`? Currently `MemoryCore.__init__` accepts `observation_model` and `reflect_model` params but `MessageStore` doesn't pass them.
2. **Workspace scoping** — Should observations be per-workspace (current behavior) or shared across workspaces (simpler)? If per-workspace, need to add `workspace_id` column to observations or filter by metadata.
3. **`embedding_fn` source** — Where should the embedding function come from for `ReflectorPipeline`'s cosine similarity gate? ChromaDB's internal embedding function? A separate model call?
4. **`method` parameter on `memory_reflection`** — CoreMem always uses hybrid search. Should we keep the parameter as a no-op for backward compat, or remove it?
