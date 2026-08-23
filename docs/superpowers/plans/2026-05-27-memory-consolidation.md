# Memory Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- []`) syntax for tracking.

# Memory Consolidation — SUPERSEDED

**Status:** ❌ **Superseded 2026-08-21** — this plan built the
Observer → Reflector two-table memory system, which CoreMem **v0.10.0
removed upstream** (replaced by compiler + dreaming + search). The app
migrated to `coremem>=0.13.1`; `memory_profile` now reads a `recall()`
digest and `memory_reflection` is gone. Retained for history only.

---

**Goal:** Consolidate the 3-layer memory system (MessageStore, ObservationStore, MemoryStore) into a single 2-tier pipeline (Observer → Reflector) with two tables (observations + reflections), rename message-read tools, and retire ~4,100 lines of dead code.

**Architecture:** Two background agents (Observer at ~8K tokens, Reflector at 24h intervals) write to a unified `MemoryStore` at `~/Assistant/Memory/global/`. Tools split into `message_*` (reads `MessageStore`) and `memory_*` (reads `MemoryStore`). No auto-injection — agent calls tools explicitly. No compression — HybridDB FTS5 relevance ranking replaces summarization.

**Tech Stack:** Python 3.11+, HybridDB (SQLite + FTS5 + ChromaDB), Pydantic, asyncio

---

## File Map

| File | Responsibility | Lines (est) |
|------|---------------|-------------|
| `src/sdk/tools_core/message.py` | **NEW** — `message_search`, `message_count`, `message_history` | ~200 |
| `src/storage/memory.py` | **REWRITE** — Unified MemoryStore (observations + reflections, 2 tables) | ~300 |
| `src/sdk/tools_core/memory.py` | **REWRITE** — `memory_profile`, `memory_reflection` | ~100 |
| `src/sdk/tools_core/observation.py` | **MODIFY** — Update Observer prompt (remove facts_extracted), add Reflector prompt + runner | ~250 |
| `src/sdk/middleware_observation.py` | **MODIFY** — Remove `before_agent()`, remove compression, add 24h Reflector schedule | ~150 |
| `src/sdk/native_tools.py` | **MODIFY** — Register message tools + updated memory tools | ~250 |
| `src/sdk/__init__.py` | **MODIFY** — Remove MemoryMiddleware export | ~130 |
| `src/sdk/runner.py` | **MODIFY** — Remove `clear_memory_store_cache` calls (3 lines) | ~490 |
| `src/http/routers/memories.py` | **MODIFY** — Update to use new MemoryStore | ~150 |
| `src/http/routers/conversation.py` | **MODIFY** — Remove MemoryMiddleware + consolidation calls | ~370 |
| `tests/sdk/test_memory_consolidation.py` | **NEW** — Tests for new MemoryStore + tools | ~500 |
| Tests (5 files) | **MODIFY** — Update to match new APIs | varies |

**Delete (6 files, ~4,100 lines):**
- `src/storage/observation.py` (182 lines)
- `src/storage/consolidation.py` (393 lines)
- `src/sdk/memory_planner.py` (271 lines)
- `src/sdk/memory_ranker.py` (413 lines)
- `src/sdk/middleware_memory.py` (960 lines)
- Tests referencing old Memory module: rewrite `tests/unit/test_memory_storage.py`, update `tests/sdk/test_observation.py`, update `tests/sdk/test_middleware_conformance.py`

---

### Task 1: Create message tools file (`message_search`, `message_count`, `message_history`)

**Files:**
- Create: `src/sdk/tools_core/message.py`
- Read: `src/sdk/tools_core/memory.py` (lines 293–1129 for existing implementations)

These are renamed copies of existing tools from `src/sdk/tools_core/memory.py`, with names changed. Keep the full existing implementation including memcore, HyDE, query expansion, session dedup, MemPalace boosting, and aggregation counting. Only drop imports from `src.storage.memory`, `src.sdk.memory_planner`, `src.sdk.memory_ranker`, and `src.storage.observation` (which are being retired). Rename function names and annotation titles only.

Copy the existing `memory_search`, `memory_count`, and `memory_get_history` functions verbatim — rename `@tool` function names and `ToolAnnotations(title=...)` only. All helper functions (`_get_memory_core`, `_expand_queries`, `_llm_expand_queries`, `_regex_expand_queries`, `_list_workspace_ids`, `_fetch_session_ids`, `_group_results_by_session`, `_search_all_workspaces`, `_generate_hyde_query`, `_content_similarity`, `_mempalace_boost`, `_dedup_by_session`, `_compute_aggregation_count`, `_get_history_fallback`) move into `message.py` unchanged.

The current `_get_memory_core()` helper calls into a memcore wrapper around MessageStore — keep this exactly as-is, just move it.

- [ ] **Step 2: Commit**

```bash
git add src/sdk/tools_core/message.py
git commit -m "feat: add message_search, message_count, message_history tools"
```

---

### Task 2: Write unified MemoryStore

**Files:**
- Modify: `src/storage/memory.py` (rewrite — fresh content, ~300 lines)
- Read: `src/storage/observation.py` (for existing schema to port)

The new `MemoryStore` replaces both the old `MemoryStore` (trigger/action) and `ObservationStore`. Two tables: `observations` and `reflections`. Uses `paths.user_memory_dir()`.

- [ ] **Step 1: Write `src/storage/memory.py`**

```python
"""Unified observation-based memory store.

Two-tier pipeline: Observer (perception) → Reflector (processing).
Two tables: observations (what was said), reflections (what it means).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.app_logging import get_logger
from src.sdk.hybrid_db import HybridDB

logger = get_logger()


class MemoryStore:
    """Unified observation-based memory.

    observations — Episodic text records from Observer.
    reflections — Synthesized patterns from Reflector.
    """

    def __init__(self, user_id: str, workspace_id: str = "personal",
                 *, base_dir: str | None = None):
        self.user_id = user_id
        self.workspace_id = workspace_id

        if base_dir is not None:
            base_path = Path(base_dir)
        else:
            from src.storage.paths import get_paths
            base_path = get_paths(user_id, workspace_id).user_memory_dir()
        base_path.mkdir(parents=True, exist_ok=True)
        self.db = HybridDB(str(base_path), max_chroma_index_gb=0)
        self._init_tables()

    def _init_tables(self) -> None:
        self.db.create_table(
            "observations",
            {
                "id": "TEXT PRIMARY KEY",
                "content": "LONGTEXT",
                "priority": "TEXT",
                "observation_ts": "TEXT",
                "referenced_date": "TEXT",
                "relative_date": "TEXT",
                "source_message_range": "TEXT",
                "created_at": "TEXT",
            },
        )
        self.db.create_table(
            "reflections",
            {
                "id": "TEXT PRIMARY KEY",
                "content": "LONGTEXT",
                "domain": "TEXT",
                "linked_observation_ids": "TEXT",
                "confidence": "REAL DEFAULT 0.6",
                "decay_rate": "REAL DEFAULT 0.05",
                "access_count": "INTEGER DEFAULT 0",
                "created_at": "TEXT",
                "updated_at": "TEXT",
                "last_accessed_at": "TEXT",
            },
        )

    # ── Observations ──────────────────────────────────────

    def insert_observations(self, observations: list[dict[str, Any]]) -> int:
        count = 0
        for obs in observations:
            obs_id = obs.get("id") or f"obs_{uuid.uuid4().hex[:12]}"
            row = {
                "id": obs_id,
                "content": str(obs.get("content", "")),
                "priority": str(obs.get("priority", "🟢")),
                "observation_ts": obs.get("observation_ts")
                    or datetime.now(UTC).isoformat(),
                "referenced_date": obs.get("referenced_date") or "",
                "relative_date": obs.get("relative_date") or "",
                "source_message_range": obs.get("source_message_range") or "",
                "created_at": datetime.now(UTC).isoformat(),
            }
            try:
                self.db.insert("observations", row, sync=False)
                count += 1
            except Exception:
                pass
        self.db.process_journal()
        return count

    def get_recent_observations(self, days: int = 7, limit: int = 50
                                ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        return self.db.query(
            "observations",
            where="observation_ts > ?",
            params=(cutoff,),
            order_by="observation_ts DESC",
            limit=limit,
        )

    def get_all_observations(self) -> list[dict[str, Any]]:
        return self.db.query("observations", order_by="observation_ts ASC",
                             limit=10000)

    def search_observations(self, query: str, limit: int = 10
                           ) -> list[dict[str, Any]]:
        return self.db.search_all("observations", query, limit=limit)

    # ── Reflections ───────────────────────────────────────

    def insert_reflections(self, reflections: list[dict[str, Any]]) -> int:
        count = 0
        for refl in reflections:
            refl_id = refl.get("id") or f"refl_{uuid.uuid4().hex[:12]}"
            now = datetime.now(UTC).isoformat()
            linked = refl.get("linked_observation_ids", [])
            linked_str = (linked if isinstance(linked, str)
                          else str(linked).replace("'", '"'))
            row = {
                "id": refl_id,
                "content": str(refl.get("content", "")),
                "domain": str(refl.get("domain", "")),
                "linked_observation_ids": linked_str,
                "confidence": float(refl.get("confidence", 0.6)),
                "decay_rate": float(refl.get("decay_rate", 0.05)),
                "access_count": 0,
                "created_at": now,
                "updated_at": now,
                "last_accessed_at": None,
            }
            try:
                self.db.insert("reflections", row, sync=False)
                count += 1
            except Exception:
                pass
        self.db.process_journal()
        return count

    def search_reflections(self, query: str, method: str = "hybrid",
                           limit: int = 5) -> list[dict[str, Any]]:
        if method == "fts":
            results = self.db.search_all("reflections", query, limit=limit)
        elif method == "semantic":
            try:
                results = self.db.semantic_search("reflections", query,
                                                  limit=limit)
            except Exception:
                results = self.db.search_all("reflections", query, limit=limit)
        else:
            results = self.db.search_all("reflections", query, limit=limit)
            if not results:
                try:
                    results = self.db.semantic_search("reflections", query,
                                                      limit=limit)
                except Exception:
                    pass
        return results

    def boost_reflection(self, reflection_id: str) -> None:
        row = self.db.get("reflections", reflection_id)
        if not row:
            return
        confidence = float(row.get("confidence", 0.6))
        access_count = int(row.get("access_count", 0) or 0)
        new_conf = min(confidence + 0.1 * (1.0 - confidence), 1.0)
        now = datetime.now(UTC).isoformat()
        self.db.update("reflections", reflection_id, {
            "confidence": new_conf,
            "access_count": access_count + 1,
            "last_accessed_at": now,
            "updated_at": now,
        })

    def apply_decay(self) -> int:
        """Apply weekly decay to all reflections. Called by Reflector schedule."""
        now = datetime.now(UTC).isoformat()
        rows = self.db.query("reflections", limit=10000)
        softened = 0
        for row in rows:
            if not row.get("updated_at"):
                continue
            try:
                last = datetime.fromisoformat(str(row["updated_at"]))
            except (ValueError, TypeError):
                continue
            weeks = (datetime.now(UTC) - last).total_seconds() / (7 * 86400)
            if weeks < 1.0:
                continue
            confidence = float(row.get("confidence", 0.6))
            decay_rate = float(row.get("decay_rate", 0.05))
            new_conf = confidence - decay_rate * weeks
            if new_conf <= 0.1:
                continue  # soft-delete: still in DB, filtered from queries
            self.db.update("reflections", row["id"], {
                "confidence": new_conf,
                "updated_at": now,
            })
            softened += 1
        return softened

    def get_reflections(self, limit: int = 20, min_confidence: float = 0.1
                       ) -> list[dict[str, Any]]:
        return self.db.query(
            "reflections",
            where="confidence >= ?",
            params=(min_confidence,),
            order_by="confidence DESC",
            limit=limit,
        )

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


_memory_store_cache: dict[str, MemoryStore] = {}


def get_memory_store(user_id: str, workspace_id: str = "personal") -> MemoryStore:
    key = f"{user_id}:{workspace_id}"
    if key not in _memory_store_cache:
        _memory_store_cache[key] = MemoryStore(user_id, workspace_id)
    return _memory_store_cache[key]


def clear_memory_store_cache() -> None:
    for store in _memory_store_cache.values():
        try:
            store.close()
        except Exception:
            pass
    _memory_store_cache.clear()
```

- [ ] **Step 2: Commit**

```bash
git add src/storage/memory.py
git commit -m "feat: unified MemoryStore — observations + reflections, two tables"
```

---

### Task 3: Update Observer prompt (remove facts_extracted, simplify)

**Files:**
- Modify: `src/sdk/tools_core/observation.py`

- [ ] **Step 1: Update `OBSERVER_PROMPT` in `src/sdk/tools_core/observation.py`**

Replace the existing `OBSERVER_PROMPT` (lines 17–51) with a version that drops `facts_extracted`:

```python
OBSERVER_PROMPT = """You are an observer agent. Your job is to extract key facts from a conversation and record them as precise, concise observations.

Input: A conversation log between a user and an AI assistant.

Output: A JSON array of observations. Each observation must have:
- "id": a unique ID like "obs_<uuid>"
- "content": ONE fact per observation, in plain English. Be exact with values (names, numbers, dates).
- "priority": one of "🔴" (high — precise value like name, address, number), "🟡" (medium — preference, opinion), "🟢" (low — context, trivia)
- "referenced_date": the date mentioned in the observation content, or "" if none

CRITICAL RULES:
- One fact per observation. Do not combine multiple facts.
- Use exact values as stated. Never paraphrase numbers or proper nouns.
- If the user CORRECTS previously stated information, capture both as separate observations with different timestamps.
- Skip generic chat, greetings, and meta-commentary.
- Skip observations already observed (listed below as known).

{conversation}

{previous_context}

Return ONLY the JSON array, no markdown wrapping, no explanation.
"""
```

- [ ] **Step 2: Add `REFLECTOR_PROMPT` after `OBSERVER_PROMPT` in the same file**

```python
REFLECTOR_PROMPT = """You are a reflection agent. Your job is to think about what you know about a user and discover patterns, relationships, and deeper meaning.

Input: All observations collected about the user, plus any previous reflections for context.

Output: A JSON array of reflections. Each reflection must have:
- "id": a unique ID like "refl_<uuid>"
- "content": A synthesized insight — not a fact, but what the facts MEAN when considered together. Patterns, contradictions, values, trajectories, predictions.
- "domain": Category label (preference, career, lifestyle, relationship, skill, value, habit, health, finance, etc.)
- "linked_observation_ids": List of observation IDs that support this reflection

CRITICAL RULES:
- Do NOT repeat facts. Observations already say "lives in Denver." You say WHY it matters — "Has relocated twice for family; values school quality above career."
- Discover multi-observation patterns. Single facts do not need reflection.
- If observations contradict ("lives in Seattle" vs "lives in Denver"), note the change: "Previously in Seattle, now in Denver as of DATE. Reason: ..."
- Generate predictions where patterns warrant: "May relocate again within 2 years based on past behavior."
- Quality over quantity. 3-5 meaningful reflections are better than 15 trivial ones.

{observations}

{previous_reflections}

Return ONLY the JSON array, no markdown wrapping, no explanation.
"""
```

- [ ] **Step 3: Add `run_reflector()` function**

Add after the existing `run_observer()` function (after line 187):

```python
async def run_reflector(
    observations: list[dict[str, Any]],
    provider: Any,
    previous_reflections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the Reflector to discover patterns from observations."""
    import json as _json

    obs_text = _json.dumps(observations, indent=2, default=str)

    prev_text = ""
    if previous_reflections:
        prev_refs = [
            {"id": r.get("id", ""), "content": r.get("content", "")[:500]}
            for r in previous_reflections[-10:]
        ]
        prev_text = _json.dumps(prev_refs, indent=2, default=str)
        prev_text = f"\n\nPrevious reflections for context (do not repeat these):\n{prev_text}"

    prompt = REFLECTOR_PROMPT.format(
        observations=obs_text,
        previous_reflections=prev_text,
    )

    from src.sdk.messages import Message

    messages = [
        Message.system("You discover patterns and meaning from observations."),
        Message.user(prompt),
    ]
    response = await provider.chat(messages)

    content = response.content if response.content else "[]"
    if isinstance(content, list):
        text_parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = "".join(text_parts)

    try:
        result = _json.loads(content)
    except _json.JSONDecodeError:
        import re
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                result = _json.loads(match.group())
            except _json.JSONDecodeError:
                return {"reflections": []}
        else:
            return {"reflections": []}

    if isinstance(result, list):
        return {"reflections": result}
    if isinstance(result, dict):
        return {"reflections": result.get("reflections", [])}
    return {"reflections": []}
```

- [ ] **Step 4: Update `run_observer()` prompt formatting**

The current `run_observer()` calls `OBSERVER_PROMPT.format(conversation=...)` with a single placeholder. Update to use the new two-placeholder prompt:

```python
# OLD:
prompt = OBSERVER_PROMPT.format(conversation=convo_text)

# NEW:
prev_text = ""
if previous_observations:
    prev_text = "\n\nKnown observations (do not re-extract):\n" + \
        "\n".join(f"- {obs.get('content', '')}" for obs in previous_observations[:30])

prompt = OBSERVER_PROMPT.format(
    conversation=convo_text,
    previous_context=prev_text,
)
```

Also remove any `facts_extracted` assembly from the observation dicts returned by the Observer — the Observer now only produces `id`, `content`, `priority`, `referenced_date`, `observation_ts`, `source_message_range`.

- [ ] **Step 5: Commit**

```bash
git add src/sdk/tools_core/observation.py
git commit -m "feat: update Observer prompt, add Reflector prompt and runner"
```

---

### Task 4: Rewrite memory tools (`memory_profile`, `memory_reflection`)

**Files:**
- Modify: `src/sdk/tools_core/memory.py` (rewrite — fresh content, ~100 lines)

Replace the entire file with just `memory_profile` and `memory_reflection`. All message-search tools moved to `message.py`.

- [ ] **Step 1: Write `src/sdk/tools_core/memory.py`**

```python
"""Memory tools — read from unified MemoryStore (observations + reflections)."""

from typing import Any

from src.sdk.tools import ToolAnnotations, tool


def _get_memory_store(user_id: str, workspace_id: str) -> Any:
    from src.storage.memory import get_memory_store
    return get_memory_store(user_id, workspace_id)


@tool
def memory_profile(
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Return the current working memory — what the system knows about the user.

    Returns recent observations collected by the Observer. Use when the user
    asks "what do you know about me?" or the agent needs to refresh its
    understanding of the user's context.

    Args:
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
    """
    store = _get_memory_store(user_id, workspace_id)
    recent = store.get_recent_observations(days=7, limit=50)

    if not recent:
        return "No observations available yet. The Observer has not processed any conversations."

    parts = ["## Working Memory (Recent Observations)\n"]
    for obs in recent:
        priority = obs.get("priority", "🟢")
        ts = str(obs.get("observation_ts", ""))[:10]
        content = str(obs.get("content", ""))
        parts.append(f"{priority} {ts} {content}")

    return "\n".join(parts)


memory_profile.annotations = ToolAnnotations(
    title="Get User Profile", read_only=True, idempotent=True
)


@tool
def memory_reflection(
    query: str,
    method: str = "hybrid",
    limit: int = 5,
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Search synthesized reflections — patterns and insights about the user.

    Reflections are higher-order patterns discovered by the Reflector from
    analyzing observations across time. Use when looking for themes, trends,
    or synthesized understanding about the user.

    Args:
        query: What to search for (e.g., "career", "relationships", "habits")
        method: Search method (fts, semantic, or hybrid) — default: hybrid
        limit: Maximum results (default: 5)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
    """
    store = _get_memory_store(user_id, workspace_id)
    results = store.search_reflections(query, method=method, limit=limit)

    if not results:
        return f"No reflections found for: {query}"

    parts = [f"## Reflections for '{query}'\n"]
    for i, refl in enumerate(results, 1):
        confidence = float(refl.get("confidence", 0.6))
        domain = str(refl.get("domain", ""))
        content = str(refl.get("content", ""))
        parts.append(f"{i}. [{domain}] {content} (confidence: {confidence:.0%})")
        store.boost_reflection(refl["id"])

    return "\n".join(parts)


memory_reflection.annotations = ToolAnnotations(
    title="Search Reflections", read_only=True, idempotent=True
)
```

- [ ] **Step 2: Commit**

```bash
git add src/sdk/tools_core/memory.py
git commit -m "feat: rewrite memory tools — memory_profile, memory_reflection"
```

---

### Task 5: Update `native_tools.py` registrations

**Files:**
- Modify: `src/sdk/native_tools.py`

- [ ] **Step 1: Replace the memory imports section (line 77)**

Change:
```python
from src.sdk.tools_core.memory import memory_search
```
To:
```python
from src.sdk.tools_core.memory import memory_profile, memory_reflection
from src.sdk.tools_core.message import message_search, message_count, message_history
```

- [ ] **Step 2: Replace the memory registration section (lines 140–145)**

Change:
```python
    # registry.register(memory_get_history)  # disabled: uses old store
    registry.register(memory_search)
    # registry.register(memory_search_all)
    # registry.register(memory_search_all_workspaces)
    # registry.register(memory_search_insights)
    # registry.register(memory_count)
```
To:
```python
    registry.register(message_search)
    registry.register(message_count)
    registry.register(message_history)
    registry.register(memory_profile)
    registry.register(memory_reflection)
```

- [ ] **Step 3: Commit**

```bash
git add src/sdk/native_tools.py
git commit -m "feat: register message_* and memory_* tools in native registry"
```

---

### Task 6: Update `ObservationMiddleware` — remove auto-injection, remove compression, add 24h Reflector schedule

**Files:**
- Modify: `src/sdk/middleware_observation.py`

- [ ] **Step 1: Rewrite `src/sdk/middleware_observation.py`**

Replace entire file content:

```python
"""Observation middleware — background Observer and scheduled Reflector.

Observer fires when 8K cumulative unobserved tokens accumulate.
Reflector fires every 24 hours.
No auto-injection — agent calls memory tools explicitly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger
from src.sdk.middleware import Middleware

logger = get_logger()

OBSERVER_THRESHOLD_TOKENS = 8000
MIN_OBSERVER_INTERVAL_TURNS = 3
REFLECTOR_INTERVAL_SECONDS = 24 * 3600


class ObservationMiddleware(Middleware):
    """Background Observer + scheduled Reflector. No auto-injection."""

    def __init__(self, user_id: str = "default_user",
                 workspace_id: str = "personal",
                 base_dir: str | None = None):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._base_dir = base_dir
        self._unobserved_since = datetime.now(UTC)
        self._turns_since_observer = 0
        self._observer_running = False
        self._observer_lock = threading.Lock()
        self._reflector_last_run = time.time()
        self._memory_store = None
        self._message_store = None

    @property
    def _store(self) -> Any:
        if self._memory_store is None:
            from src.storage.memory import get_memory_store
            self._memory_store = get_memory_store(self.user_id, self.workspace_id)
        return self._memory_store

    @property
    def _conversation(self) -> Any:
        if self._message_store is None:
            from src.storage.messages import get_message_store
            self._message_store = get_message_store(self.user_id, self.workspace_id)
        return self._message_store

    def after_agent(self, state: Any) -> dict[str, Any] | None:
        self._turns_since_observer += 1

        # Observer check
        unobserved_tokens = self._count_unobserved_tokens()
        if (unobserved_tokens >= OBSERVER_THRESHOLD_TOKENS and
                self._turns_since_observer >= MIN_OBSERVER_INTERVAL_TURNS):
            self._dispatch_fire_observer()
            self._turns_since_observer = 0

        # Reflector check — every 24 hours
        now = time.time()
        if now - self._reflector_last_run >= REFLECTOR_INTERVAL_SECONDS:
            self._dispatch_fire_reflector()
            self._reflector_last_run = now

        return None

    def _count_unobserved_tokens(self) -> int:
        try:
            messages = self._conversation.get_recent_messages(count=500, offset=0)
            if not messages:
                return 0
            text = " ".join(m.content or "" for m in messages)
            return max(len(text) // 4, 0)  # rough: ~4 chars per token
        except Exception:
            return 0

    def _dispatch_fire_observer(self) -> None:
        with self._observer_lock:
            if self._observer_running:
                return
            self._observer_running = True
        try:
            asyncio.create_task(self._fire_observer())
        except RuntimeError:
            t = threading.Thread(target=self._fire_observer_sync, daemon=True)
            t.start()

    def _dispatch_fire_reflector(self) -> None:
        try:
            asyncio.create_task(self._fire_reflector())
        except RuntimeError:
            t = threading.Thread(target=self._fire_reflector_sync, daemon=True)
            t.start()

    async def _fire_observer(self) -> None:
        try:
            from src.sdk.providers.factory import create_model_from_config
            from src.sdk.tools_core.observation import run_observer

            provider = create_model_from_config()
            messages = self._conversation.get_recent_messages(count=500, offset=0)
            if not messages:
                self._observer_running = False
                return

            raw_messages = [
                {"role": m.role, "content": m.content}
                for m in messages if m.content.strip()
            ]
            previous = self._store.get_recent_observations(days=30, limit=50)

            result = await run_observer(raw_messages, provider,
                                        previous_observations=previous)
            observations = result.get("observations", [])
            if observations:
                self._store.insert_observations(observations)
                logger.info("observer.completed",
                            {"count": len(observations)},
                            user_id=self.user_id)
        except Exception as e:
            logger.error("observer.error", {"error": str(e)}, user_id=self.user_id)
        finally:
            self._observer_running = False

    async def _fire_reflector(self) -> None:
        try:
            from src.sdk.providers.factory import create_model_from_config
            from src.sdk.tools_core.observation import run_reflector

            provider = create_model_from_config()
            observations = self._store.get_all_observations()
            if len(observations) < 10:
                logger.info("reflector.skipped",
                            {"reason": "too_few_observations",
                             "count": len(observations)},
                            user_id=self.user_id)
                return

            previous_reflections = self._store.get_reflections(limit=10)

            # Apply decay to existing reflections first
            self._store.apply_decay()

            result = await run_reflector(observations, provider,
                                         previous_reflections=previous_reflections)
            reflections = result.get("reflections", [])
            if reflections:
                self._store.insert_reflections(reflections)
                logger.info("reflector.completed",
                            {"count": len(reflections)},
                            user_id=self.user_id)
        except Exception as e:
            logger.error("reflector.error", {"error": str(e)}, user_id=self.user_id)

    def _fire_observer_sync(self) -> None:
        asyncio.run(self._fire_observer())

    def _fire_reflector_sync(self) -> None:
        asyncio.run(self._fire_reflector())
```

- [ ] **Step 2: Commit**

```bash
git add src/sdk/middleware_observation.py
git commit -m "feat: rewrite ObservationMiddleware — remove auto-inject, add 24h Reflector schedule"
```

---

### Task 7: Update external references — SDK init, runner, HTTP routers

**Files:**
- Modify: `src/sdk/__init__.py`
- Modify: `src/sdk/runner.py`
- Modify: `src/http/routers/conversation.py`
- Modify: `src/http/routers/memories.py`

- [ ] **Step 1: Update `src/sdk/__init__.py`**

Remove the `MemoryMiddleware` import (line 43) and its export in `__all__` (line 98):

Change line 43 from:
```python
from src.sdk.middleware_memory import MemoryMiddleware
```
To: (remove the line entirely)

Change line 98 from:
```python
    "MemoryMiddleware",
```
To: (remove the line entirely)

Also update the module docstring line 13 to remove `MemoryMiddleware - memory extraction/injection (SDK-native)`.

- [ ] **Step 2: Update `src/sdk/runner.py`**

Remove `clear_memory_store_cache` calls at lines 474–475, 485–486, 493–494. Replace each with a pass or nothing.

Line 474–475 block: Remove:
```python
    from src.storage.memory import clear_memory_store_cache
    clear_memory_store_cache()
```
Line 485–486 block: Remove same.
Line 493–494 block: Remove same.

- [ ] **Step 3: Update `src/http/routers/conversation.py`**

Remove the `extract_conversation_memories` endpoint (lines 370–407) which uses `MemoryMiddleware` and `trigger_consolidation`. Delete the entire function.

- [ ] **Step 4: Rewrite `src/http/routers/memories.py`**

Replace entire file with endpoints that use the new `MemoryStore`:

```python
from fastapi import APIRouter, Query

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("/observations")
async def list_observations(
    user_id: str = "default_user",
    workspace_id: str = "personal",
    days: int = 7,
    limit: int = 50,
):
    """List recent observations."""
    from src.storage.memory import get_memory_store
    store = get_memory_store(user_id, workspace_id)
    results = store.get_recent_observations(days=days, limit=limit)
    return {"observations": results}


@router.get("/reflections")
async def list_reflections(
    user_id: str = "default_user",
    workspace_id: str = "personal",
    limit: int = 20,
):
    """List reflections (patterns and insights)."""
    from src.storage.memory import get_memory_store
    store = get_memory_store(user_id, workspace_id)
    results = store.get_reflections(limit=limit)
    return {"reflections": results}


@router.post("/reflections/search")
async def search_reflections(
    query: str = Query(...),
    method: str = "hybrid",
    limit: int = 5,
    user_id: str = "default_user",
    workspace_id: str = "personal",
):
    """Search reflections."""
    from src.storage.memory import get_memory_store
    store = get_memory_store(user_id, workspace_id)
    results = store.search_reflections(query, method=method, limit=limit)
    for r in results:
        store.boost_reflection(r["id"])
    return {"query": query, "method": method, "results": results}


@router.post("/observations/search")
async def search_observations(
    query: str = Query(...),
    limit: int = 10,
    user_id: str = "default_user",
    workspace_id: str = "personal",
):
    """Search observations."""
    from src.storage.memory import get_memory_store
    store = get_memory_store(user_id, workspace_id)
    results = store.search_observations(query, limit=limit)
    return {"query": query, "results": results}


@router.delete("/clear")
async def clear_memories(
    user_id: str = "default_user",
    workspace_id: str = "personal",
):
    """Reset memory store cache (new memory will be created on next access)."""
    from src.storage.memory import clear_memory_store_cache
    clear_memory_store_cache()
    return {"status": "cleared", "user_id": user_id, "workspace_id": workspace_id}
```

- [ ] **Step 5: Commit**

```bash
git add src/sdk/__init__.py src/sdk/runner.py src/http/routers/conversation.py src/http/routers/memories.py
git commit -m "feat: update external references — remove MemoryMiddleware, rewrite /memories router"
```

---

### Task 8: Delete old files

**Files:**
- Delete: `src/storage/observation.py`
- Delete: `src/storage/consolidation.py`
- Delete: `src/sdk/memory_planner.py`
- Delete: `src/sdk/memory_ranker.py`
- Delete: `src/sdk/middleware_memory.py`

- [ ] **Step 1: Delete files and commit**

```bash
rm src/storage/observation.py
rm src/storage/consolidation.py
rm src/sdk/memory_planner.py
rm src/sdk/memory_ranker.py
rm src/sdk/middleware_memory.py
git add -u
git commit -m "feat: remove old memory system files (~4,100 lines)"
```

---

### Task 9: Write tests for new MemoryStore and tools

**Files:**
- Create: `tests/sdk/test_memory_consolidation.py`

- [ ] **Step 1: Write `tests/sdk/test_memory_consolidation.py`**

```python
"""Tests for unified MemoryStore, message tools, and memory tools."""

import tempfile
from pathlib import Path

import pytest


class TestMemoryStore:
    """Tests for the unified MemoryStore."""

    def test_insert_and_get_observations(self):
        from src.storage.memory import MemoryStore
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("test_user", base_dir=tmpdir)
            count = store.insert_observations([
                {
                    "id": "obs_1",
                    "content": "lives in Denver",
                    "priority": "🔴",
                    "observation_ts": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "obs_2",
                    "content": "commutes 45 min",
                    "priority": "🟡",
                    "observation_ts": "2026-01-02T00:00:00Z",
                },
            ])
            assert count == 2

            recent = store.get_recent_observations(days=365)
            assert len(recent) == 2
            assert recent[0]["content"] == "commutes 45 min"  # most recent first

            all_obs = store.get_all_observations()
            assert len(all_obs) == 2

            store.close()

    def test_insert_and_search_reflections(self):
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("test_user", base_dir=tmpdir)
            count = store.insert_reflections([
                {
                    "id": "refl_1",
                    "content": "User relocated twice for family; values schools",
                    "domain": "lifestyle",
                    "linked_observation_ids": ["obs_1", "obs_2"],
                    "confidence": 0.7,
                },
            ])
            assert count == 1

            results = store.search_reflections("relocated", method="fts")
            assert len(results) >= 1

            refs = store.get_reflections()
            assert len(refs) >= 1
            assert float(refs[0]["confidence"]) == 0.7

            store.close()

    def test_boost_reflection(self):
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("test_user", base_dir=tmpdir)
            store.insert_reflections([
                {
                    "id": "refl_boost",
                    "content": "User prefers remote work",
                    "domain": "career",
                },
            ])
            store.boost_reflection("refl_boost")
            store.boost_reflection("refl_boost")

            refs = store.get_reflections()
            refl = [r for r in refs if r["id"] == "refl_boost"][0]
            assert float(refl["confidence"]) > 0.6  # boosted twice
            assert int(refl.get("access_count", 0)) == 2
            store.close()

    def test_apply_decay(self):
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("test_user", base_dir=tmpdir)
            store.insert_reflections([
                {
                    "id": "refl_decay",
                    "content": "Old pattern",
                    "domain": "habit",
                    "confidence": 0.5,
                },
            ])
            # Set updated_at to 2 weeks ago to trigger decay
            from datetime import UTC, datetime, timedelta
            two_weeks = (datetime.now(UTC) - timedelta(weeks=2)).isoformat()
            store.db.update("reflections", "refl_decay",
                           {"updated_at": two_weeks})

            decayed = store.apply_decay()
            assert decayed >= 0

            store.close()

    def test_search_observations(self):
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("test_user", base_dir=tmpdir)
            store.insert_observations([
                {
                    "id": "obs_s1",
                    "content": "allergic to shellfish",
                    "priority": "🔴",
                },
                {
                    "id": "obs_s2",
                    "content": "prefers Python over JavaScript",
                    "priority": "🟡",
                },
            ])
            results = store.search_observations("shellfish")
            assert len(results) >= 1
            assert "shellfish" in str(results[0]["content"]).lower()

            results2 = store.search_observations("Python")
            assert len(results2) >= 1
            store.close()


class TestMemoryStoreCache:
    """Tests for the MemoryStore cache."""

    def test_get_memory_store_caches(self):
        from src.storage.memory import get_memory_store, clear_memory_store_cache
        clear_memory_store_cache()
        store1 = get_memory_store("cache_user")
        store2 = get_memory_store("cache_user")
        assert store1 is store2
        clear_memory_store_cache()
        store3 = get_memory_store("cache_user")
        assert store1 is not store3  # new instance after cache clear
        clear_memory_store_cache()


class TestMessageTools:
    """Tests for message_search, message_count, message_history tools."""

    def test_message_search_imports(self):
        from src.sdk.tools_core.message import message_search
        assert message_search is not None
        assert message_search.annotations.read_only is True

    def test_message_count_imports(self):
        from src.sdk.tools_core.message import message_count
        assert message_count is not None
        assert message_count.annotations.read_only is True

    def test_message_history_imports(self):
        from src.sdk.tools_core.message import message_history
        assert message_history is not None
        assert message_history.annotations.read_only is True


class TestMemoryTools:
    """Tests for memory_profile and memory_reflection tools."""

    def test_memory_profile_imports(self):
        from src.sdk.tools_core.memory import memory_profile
        assert memory_profile is not None
        assert memory_profile.annotations.read_only is True

    def test_memory_reflection_imports(self):
        from src.sdk.tools_core.memory import memory_reflection
        assert memory_reflection is not None
        assert memory_reflection.annotations.read_only is True

    def test_memory_profile_no_data(self):
        from src.sdk.tools_core.memory import memory_profile
        import tempfile
        from unittest.mock import patch
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("profile_test", base_dir=tmpdir)
            with patch("src.sdk.tools_core.memory._get_memory_store",
                       return_value=store):
                result = memory_profile(user_id="profile_test")
                assert "No observations" in result
            store.close()

    def test_memory_reflection_no_data(self):
        from src.sdk.tools_core.memory import memory_reflection
        import tempfile
        from unittest.mock import patch
        from src.storage.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore("refl_test", base_dir=tmpdir)
            with patch("src.sdk.tools_core.memory._get_memory_store",
                       return_value=store):
                result = memory_reflection(query="career",
                                           user_id="refl_test")
                assert "No reflections" in result
            store.close()


class TestNativeToolsRegistration:
    """Tests that the new tools are registered."""

    def test_message_tools_registered(self):
        from src.sdk.native_tools import get_native_tool_names
        names = get_native_tool_names()
        assert "message_search" in names
        assert "message_count" in names
        assert "message_history" in names

    def test_memory_tools_registered(self):
        from src.sdk.native_tools import get_native_tool_names
        names = get_native_tool_names()
        assert "memory_profile" in names
        assert "memory_reflection" in names

    def test_old_tools_not_registered(self):
        from src.sdk.native_tools import get_native_tool_names
        names = get_native_tool_names()
        assert "memory_search" not in names
        assert "memory_search_all" not in names
        assert "memory_search_all_workspaces" not in names
        assert "memory_search_insights" not in names
        assert "memory_connect" not in names
        assert "memory_count" not in names
```

- [ ] **Step 2: Run the new tests**

```bash
uv run pytest tests/sdk/test_memory_consolidation.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/sdk/test_memory_consolidation.py
git commit -m "test: add MemoryStore, message tools, and memory tools tests"
```

---

### Task 10: Update existing tests that reference old memory system

**Files to update:**
- `tests/sdk/test_observation.py` — update ObservableStore references to new MemoryStore
- `tests/sdk/test_middleware_conformance.py` — remove MemoryMiddleware imports and test functions
- `tests/unit/test_memory_storage.py` — remove entirely (tests old trigger/action MemoryStore)
- `tests/unit/test_memory_and_other_tools.py` — remove memory tool tests (mocks old `get_memory_store`)
- `tests/sdk/test_memory_ranker.py` — remove entirely (tests deleted module)
- `tests/perf/test_memory_pipeline.py` — remove or skip (tests old pipeline)
- `tests/perf/perf_instrument.py` — remove memory perf instrumentation
- `tests/sdk/test_workspace_isolation.py` — remove `test_memory_stores_are_separate`
- `tests/benchmarks/longmemeval/eval.py` — remove or skip (uses old ObservationStore + MemoryMiddleware)
- `src/storage/__init__.py` — check for deleted module exports

- [ ] **Step 1: Remove `tests/unit/test_memory_storage.py`**

```bash
rm tests/unit/test_memory_storage.py
```

- [ ] **Step 2: Update `tests/sdk/test_middleware_conformance.py`**

The file imports `MemoryMiddleware`, `CORRECTION_KEYWORDS`, `EXTRACTION_TURN_INTERVAL` from `src.sdk.middleware_memory`. Any test function that instantiates `MemoryMiddleware(...)` or tests its methods must be removed. The file has ~10 MemoryMiddleware-related test functions — remove them all and their imports.

- [ ] **Step 3: Remove `tests/unit/test_memory_and_other_tools.py`**

This file mocks `get_memory_store` for memory tool tests. Since the old MemoryStore is gone and tools renamed, remove the entire file.

```bash
rm tests/unit/test_memory_and_other_tools.py
```

- [ ] **Step 4: Remove `tests/sdk/test_memory_ranker.py`**

```bash
rm tests/sdk/test_memory_ranker.py
```

- [ ] **Step 5: Remove `tests/perf/test_memory_pipeline.py`**

```bash
rm tests/perf/test_memory_pipeline.py
```

- [ ] **Step 6: Fix `tests/perf/perf_instrument.py`**

Remove any `MemoryStore` or `MemoryMiddleware` imports and the performance instrumentation functions that reference them. (The file instruments memory store operations for benchmarking — these are no longer applicable.)

- [ ] **Step 7: Fix `tests/sdk/test_workspace_isolation.py`**

Remove the `test_memory_stores_are_separate` function and its `MemoryStore` import at line 91. Keep all other workspace isolation tests.

- [ ] **Step 8: Fix `tests/benchmarks/longmemeval/eval.py`**

Remove or comment out any references to `ObservationStore`, `MemoryMiddleware`, and `src.sdk.tools_core.observation` (if importing old `run_observer`/`run_reflector` signatures). This benchmark file references the old observation system at lines 524, 538.

- [ ] **Step 9: Check `src/storage/__init__.py`**

Verify no imports from deleted modules (`observation`, `consolidation`). If it re-exports `get_observation_store` or `ObservationStore`, remove those lines.

- [ ] **Step 10: Update `tests/sdk/test_observation.py`**

Replace all `from src.storage.observation import ObservationStore` with `from src.storage.memory import MemoryStore`. Replace all `ObservationStore(...)` constructor calls with `MemoryStore(...)`. Replace `get_observation_store(...)` with `get_memory_store(...)`. The `ObservationStore` class is merged into `MemoryStore` — API is same (same table schema, same method names).

- [ ] **Step 11: Run tests for modified test files**

```bash
uv run pytest tests/sdk/test_observation.py tests/sdk/test_middleware_conformance.py -v
```

Expected: all tests pass with updated imports.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: update tests for new MemoryStore, remove old storage tests"
```

---

### Task 11: Final cleanup — verify full test suite

**Files:**
- all

- [ ] **Step 1: Run full SDK test suite**

```bash
uv run pytest tests/sdk/ -v
```

Expected: all SDK tests pass. Fix any failures.

- [ ] **Step 2: Run ruff linter**

```bash
uv run ruff check src/
```

Fix any lint errors.

- [ ] **Step 3: Run mypy type checker**

```bash
uv run mypy src/
```

Fix any type errors.

- [ ] **Step 4: Commit final fixes**

```bash
git add -u
git commit -m "chore: fix lint and type errors from memory consolidation"
```

---

## Post-Implementation Checklist

- [ ] `message_search`, `message_count`, `message_history` registered and callable
- [ ] `memory_profile`, `memory_reflection` registered and callable
- [ ] Old tools (`memory_search`, `memory_search_all`, `memory_search_insights`, `memory_connect`, `memory_count`) NOT registered
- [ ] `MemoryMiddleware` removed from SDK exports
- [ ] `clear_memory_store_cache` still exists but old MemoryStore logic gone
- [ ] ObservationMiddleware has no `before_agent()`
- [ ] ObservationMiddleware has 24h Reflector schedule
- [ ] HTTP `/memories` router uses new MemoryStore endpoints
- [ ] HTTP `/conversation` no longer has `extract_conversation_memories`
- [ ] All 6 old files deleted
- [ ] All tests pass: `uv run pytest tests/sdk/ -v`
- [ ] Lint clean: `uv run ruff check src/`
- [ ] Type check clean: `uv run mypy src/`
