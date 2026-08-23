# EA CoreMem Observation & Reflection Integration — SUPERSEDED

**Status:** ❌ **Superseded 2026-08-21** — CoreMem **v0.10.0 removed the
observer/reflector architecture** ("replace observer/reflector with
compiler+dreaming+search"). `enable_observations` and `memory_reflection`
no longer exist upstream; the app migrated to `coremem>=0.13.1` with
`memory_profile` reworked onto `recall()` and `memory_reflection` removed
(see `2026-06-09-observation-reflection-migration-spec.md`). This spec is
retained for history only.

---

2026-06-09

## 1. Motivation

EA has two legacy stores that predate CoreMem integration:
- `src/storage/memory.py` — `MemoryStore` with its own HybridDB (separate from conversation DB)
- `src/storage/messages.py` — `MessageStore` wrapping `MemoryCore` (conversation DB)
- `src/sdk/tools_core/memory.py` — tools that read from the old `MemoryStore`

CoreMem v0.8.0 now has native observation/reflection CRUD with metadata filtering. EA should:
1. Move observations/reflections into the same MemoryCore as messages (single DB per user)
2. Enable `enable_observations=True` on MemoryCore
3. Add fire-and-forget observer/reflector tools
4. Wire workspace delete to cascade observation delete

## 2. Current vs Proposed

### Storage

| Aspect | Current (legacy) | Proposed |
|--------|-----------------|----------|
| Messages DB | `data/conversation/app.db` | same |
| Observations DB | `data/memory/app.db` (separate!) | `data/conversation/app.db` (same as messages) |
| Reflections DB | `data/memory/app.db` | `data/conversation/app.db` |
| MemoryCore init | `enable_observations=False` | `enable_observations=True` |

### Tools

| Tool | Current | Proposed | Action |
|------|---------|----------|--------|
| `memory_observation` | `memory_profile` (reads old MemoryStore) | reads `core.observations(metadata={"workspace_id": wid}, limit=50)` | Rename + rewire |
| `memory_observation_update` | ❌ does not exist | updates an observation by id | New |
| `memory_observation_delete` | ❌ does not exist | deletes observations by workspace or id | New |
| `memory_reflection` | reads old `MemoryStore.search_reflections()` + `boost_reflection()` | reads `core.reflections(query, limit=limit)` — drop `method` param and `boost_reflection` | Rewire |
| `memory_reflection_update` | ❌ does not exist | updates a reflection by id | New |
| `memory_reflection_delete` | ❌ does not exist | deletes reflections by user or id | New |
| `memory_observe` | ❌ does not exist | `core.observer(metadata={"workspace_id": wid})` + fire-and-forget | New |
| `memory_reflect` | ❌ does not exist | `core.reflector(user_id=uid)` + fire-and-forget | New |

### Workspace Delete (src/sdk/tools_core/workspace.py:workspace_delete)

| Current | Proposed |
|---------|----------|
| Delete messages only | Delete messages + observations for workspace |

## 3. Implementation

### 3a. Schema Migration

Old `MemoryStore` data lives in `data/users/{uid}/memory/app.db`. New data goes into `data/users/{uid}/conversation/app.db` (same as messages).

No migration script — old data becomes read-only. Users start fresh with v0.8.0 observations.

### 3b. Enable Observations on MemoryCore

In `src/sdk/tools_core/message.py:_get_message_core()`:

```python
_coremem_cache[cache_key] = MemoryCore(
    path=conv_path,
    llm_provider=llm_provider,
    enable_observations=True,
)
```

MemoryCore v0.8.0 auto-runs `ALTER TABLE` migration on first init to add `metadata` column to existing `observations` table.

Note: `_get_message_core()` caches by `user_id` only (all workspaces share the same MemoryCore). Workspace filtering is done via `metadata={"workspace_id": wid}` at query time. This is correct — observations for different workspaces live in the same table, differentiated by the `metadata` column.

### 3c. Deprecate Legacy MemoryStore

`src/storage/memory.py` remains for backward compat but no new tools use it. The `_get_memory_store()` helper in `memory.py` tools is deprecated — new tools call `_get_message_core()` from `message.py` instead.

### 3d. New Tools

In `src/sdk/tools_core/memory.py`, add `memory_observe` and `memory_reflect`:

```python
@tool
def memory_observe(
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Extract observations from recent messages (fire-and-forget).
    
    Kicks off background observation extraction for this workspace.
    Observations are stored automatically when complete.
    Use memory_observation to retrieve them.
    
    Args:
        user_id: User identifier
        workspace_id: Workspace to observe
    """
    core = _get_message_core(user_id)
    obs = core.observer(metadata={"workspace_id": workspace_id})
    asyncio.create_task(obs.extract())
    return "Observation extraction started in background"

@tool
def memory_reflect(
    user_id: str = "default_user",
) -> str:
    """Generate reflections from unreflected observations (fire-and-forget).
    
    Kicks off background reflection generation for this user.
    Reflections are stored automatically when complete.
    Use memory_reflection to retrieve them.
    
    Args:
        user_id: User to reflect on
    """
    core = _get_message_core(user_id)
    ref = core.reflector(user_id=user_id)
    asyncio.create_task(ref.extract())
    return "Reflection generation started in background"
```

`asyncio.create_task()` is safe here because FastAPI endpoint handlers run in an async context with a running event loop.

### 3e. Rework Tools

`memory_profile` is renamed to `memory_observation`. Replace `store.get_recent_observations()` with:

```python
@tool
def memory_observation(
    query: str | None = None,
    limit: int = 50,
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Search or list stored observations about the user.

    If query is provided, semantically searches observations. If not,
    returns recent observations for this workspace.

    Use when the user asks "what do you know about me?" or the agent needs
    to refresh its understanding of the user's context.

    Args:
        query: Optional semantic search query
        limit: Max results (default: 50)
        user_id: User identifier
        workspace_id: Workspace ID
    """
    core = _get_message_core(user_id)
    obs = core.observations(query=query, metadata={"workspace_id": workspace_id}, limit=limit)
    if not obs:
        return "No observations available. Try message_search to find specific facts from conversation history."
    parts = ["## Observations\n"]
    for o in obs:
        ts = str(o.get("observation_ts", ""))[:10]
        parts.append(f"- [{ts}] {o.get('content', '')}")
    return "\n".join(parts)
```

`memory_reflection` — replace `store.search_reflections()` with:

```python
@tool
def memory_reflection(
    query: str,
    limit: int = 5,
    user_id: str = "default_user",
) -> str:
    """Search synthesized reflections — patterns and insights about the user.

    Args:
        query: What to search for (e.g., "career", "relationships")
        limit: Max results (default: 5)
        user_id: User identifier
    """
    core = _get_message_core(user_id)
    results = core.reflections(query=query, limit=limit)
    if not results:
        return f"No reflections found for: {query}"
    parts = [f"## Reflections for '{query}'\n"]
    for i, r in enumerate(results, 1):
        domain = str(r.get("domain", ""))
        parts.append(f"{i}. [{domain}] {r.get('content', '')}")
    return "\n".join(parts)
```

Drops `method` param (CoreMem always uses semantic search) and `boost_reflection` (CoreMem has no access tracking).

### 3f. Observation Update/Delete Tools

New tools for the agent to correct wrong facts or remove outdated ones:

```python
@tool
def memory_observation_update(
    observation_id: str,
    updates: dict[str, Any],
    user_id: str = "default_user",
) -> str:
    """Update a stored observation by ID. Fields are validated by CoreMem.

    Args:
        observation_id: ID of the observation to update
        updates: Fields to update (e.g. {"content": "corrected fact"})
        user_id: User identifier
    """
    core = _get_message_core(user_id)
    core.update_observation(observation_id, updates)
    return f"Observation {observation_id} updated"

@tool
def memory_observation_delete(
    observation_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str = "default_user",
) -> str:
    """Delete observations. Provide either an observation_id or a workspace_id.

    Args:
        observation_id: Specific observation to delete
        workspace_id: Delete all observations for this workspace
        user_id: User identifier
    """
    core = _get_message_core(user_id)
    if observation_id:
        count = core.delete_observations_by_id([observation_id])
        return f"Deleted {count} observation(s)"
    if workspace_id:
        count = core.delete_observations(metadata={"workspace_id": workspace_id})
        return f"Deleted {count} observation(s) for workspace '{workspace_id}'"
    return "Provide either observation_id or workspace_id"

@tool
def memory_reflection_update(
    reflection_id: str,
    updates: dict[str, Any],
    user_id: str = "default_user",
) -> str:
    """Update a stored reflection by ID.

    Args:
        reflection_id: ID of the reflection to update
        updates: Dict of fields to update (e.g. {"content": "corrected insight"})
        user_id: User identifier
    """
    core = _get_message_core(user_id)
    core.update_reflections(reflection_id, updates)
    return f"Reflection {reflection_id} updated"

@tool
def memory_reflection_delete(
    reflection_id: str | None = None,
    user_id: str = "default_user",
) -> str:
    """Delete reflections. Provide either a reflection_id or target user.

    Args:
        reflection_id: Specific reflection to delete
        user_id: User whose reflections to delete (defaults to calling user)
    """
    core = _get_message_core(user_id)
    if reflection_id:
        count = core.delete_reflections_by_id([reflection_id])
        return f"Deleted {count} reflection(s)"
    count = core.delete_reflections(user_id=user_id)
    return f"Deleted {count} reflection(s) for user '{user_id}'"
```

### 3g. Workspace Delete Cascade

In `src/sdk/tools_core/workspace.py:workspace_delete`, after deleting messages:

```python
core = _get_message_core(user_id)
core.delete_observations(metadata={"workspace_id": workspace_id})
```

## 4. Tests

File: `tests/sdk/test_coremem_observations.py`

```python
import tempfile
from datetime import UTC, datetime

from coremem.core import MemoryCore


def _make_core():
    d = tempfile.mkdtemp()
    core = MemoryCore(path=d, enable_observations=True)
    core._test_cleanup = lambda: __import__("shutil").rmtree(d, ignore_errors=True)
    return core


class TestMemoryObservation:
    """memory_observation reads observations via core.observations()."""

    def test_empty_when_no_observations(self):
        core = _make_core()
        result = core.observations(metadata={"workspace_id": "personal"}, limit=50)
        assert result == []

    def test_returns_recent_observations(self):
        core = _make_core()
        core.insert_observations([{
            "content": "User likes coffee",
            "metadata": {"workspace_id": "personal"},
            "observation_ts": datetime.now(UTC).isoformat(),
        }])
        obs = core.observations(metadata={"workspace_id": "personal"}, limit=50)
        assert len(obs) == 1
        assert obs[0]["content"] == "User likes coffee"

    def test_scoped_by_workspace(self):
        core = _make_core()
        core.insert_observations([{
            "content": "Work fact",
            "metadata": {"workspace_id": "work"},
            "observation_ts": datetime.now(UTC).isoformat(),
        }])
        personal = core.observations(metadata={"workspace_id": "personal"}, limit=50)
        assert personal == []

    @staticmethod
    def _make_core():
        d = tempfile.mkdtemp()
        core = MemoryCore(path=d, enable_observations=True)
        core._test_cleanup = lambda: __import__("shutil").rmtree(d, ignore_errors=True)
        return core


class TestMemoryObservationUpdate:
    """memory_observation_update calls core.update_observation()."""

    def test_update_observation_content(self):
        core = _make_core()
        ids = core.insert_observations([{
            "content": "Wrong fact",
            "metadata": {"workspace_id": "test"},
            "observation_ts": "2026-01-01",
        }])
        core.update_observation(ids[0], {"content": "Corrected fact"})
        obs = core.get_observations()
        assert obs[0]["content"] == "Corrected fact"


class TestMemoryObservationDelete:
    """memory_observation_delete calls delete_observations()."""

    def test_delete_by_id(self):
        core = _make_core()
        ids = core.insert_observations([
            {"content": "A", "observation_ts": "2026-01-01"},
            {"content": "B", "observation_ts": "2026-01-01"},
        ])
        core.delete_observations_by_id([ids[0]])
        assert len(core.get_observations()) == 1

    def test_delete_by_workspace(self):
        core = _make_core()
        core.insert_observations([
            {"content": "A", "metadata": {"workspace_id": "work"}, "observation_ts": "2026-01-01"},
            {"content": "B", "metadata": {"workspace_id": "personal"}, "observation_ts": "2026-01-01"},
        ])
        core.delete_observations(metadata={"workspace_id": "work"})
        assert len(core.get_observations()) == 1


class TestMemoryReflectionUpdateDelete:
    """memory_reflection_update/delete call CoreMem methods."""

    def test_update_reflection(self):
        core = _make_core()
        ids = core.insert_reflections([{
            "content": "Old insight",
            "domain": "general",
        }])
        core.update_reflections(ids[0], {"content": "Updated insight"})
        refs = core.get_reflections()
        assert refs[0]["content"] == "Updated insight"

    def test_delete_reflection_by_id(self):
        core = _make_core()
        ids = core.insert_reflections([
            {"content": "A", "domain": "general"},
            {"content": "B", "domain": "general"},
        ])
        core.delete_reflections_by_id([ids[0]])
        assert len(core.get_reflections()) == 1

    def test_delete_reflections_by_user(self):
        core = _make_core()
        core.insert_reflections([
            {"content": "A", "domain": "general", "user_id": "alice"},
            {"content": "B", "domain": "general", "user_id": "bob"},
        ])
        core.delete_reflections(user_id="alice")
        refs = core.get_reflections()
        assert len(refs) == 1
        assert refs[0]["user_id"] == "bob"


class TestObserverCreation:
    """memory_observe creates observer with workspace metadata."""

    def test_observer_configured_with_metadata(self):
        core = _make_core()
        obs = core.observer(session_id="test", metadata={"workspace_id": "test"})
        assert obs._session_id == "test"
        assert obs._metadata == {"workspace_id": "test"}


class TestReflectorCreation:
    """memory_reflect creates a per-user reflector."""

    def test_reflector_created(self):
        core = _make_core()
        ref = core.reflector(user_id="alice")
        assert ref._user_id == "alice"
```