# Backend Pitfall Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove runtime workspace scoping and `item_scopes`, make `session_id` the chat boundary, and keep tools, skills, subagents, files, and memory user-level.

**Architecture:** Preserve public API compatibility where cheap by accepting `workspace_id` parameters but ignoring them. Collapse runtime construction and storage paths toward user-level resources. Use `capabilities.yaml` only as simple user-level enable/disable compatibility, not per-workspace scoping.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, CoreMem/HybridDB, custom `src/sdk` runtime.

---

## Before And After

Before:

```text
user
  workspace
    files
    memory
    skills
    subagents
    item_scopes for tools/skills/subagents
    sessions
```

After:

```text
user
  files
  memory
  skills
  subagents
  capabilities
  sessions
```

Runtime rules:

- `session_id` separates chat threads.
- `user_id` owns files, memory, skills, subagents, tools, and settings.
- `workspace_id` is not part of runtime behavior.
- `item_scopes` is removed from runtime and API code.
- Old `workspace_id` request parameters may remain temporarily but must be ignored.

---

## File Structure

Create:

- `src/http/stream_adapter.py`: Transport-neutral `StreamChunk` normalization using `chunk.canonical_type`.
- `src/http/conversation_persistence.py`: Shared persistence helpers for REST, SSE, and WS.
- `tests/api/test_stream_adapter.py`: Canonical streaming tests.
- `tests/api/test_conversation_persistence.py`: Persistence helper tests.

Modify:

- `src/storage/paths.py`: Map old workspace path helpers to user-level locations and mark them compatibility wrappers.
- `src/storage/messages.py`: Make message store user-level; keep `workspace_id` argument as ignored compatibility.
- `src/sdk/runner.py`: Remove `workspace_id` from runtime cache semantics and remove `ItemScopeDB` filtering.
- `src/sdk/tools_core/skills.py`: Remove item-scope checks.
- `src/sdk/coordinator.py`: Remove item-scope subagent filtering.
- `src/http/routers/conversation.py`: Treat `session_id` as chat boundary; use shared stream/persistence helpers.
- `src/http/routers/ws.py`: Treat `session_id` as chat boundary; use shared stream/persistence helpers.
- `src/http/routers/tools.py`: Remove scope CRUD behavior; expose user-level enabled/disabled compatibility.
- `src/http/routers/skills.py`: Remove scope CRUD behavior; expose user-level enabled/disabled compatibility.
- `src/http/routers/subagents.py`: Remove scope CRUD behavior; expose user-level enabled/disabled compatibility.
- `src/http/routers/capabilities.py`: Make capabilities the simple user-level enable/disable compatibility endpoint.

Delete after callers are removed:

- `src/sdk/item_scopes.py`
- item-scope-specific tests, or update them to capabilities/user-level behavior.

---

### Task 1: Collapse Paths And Message Store To User-Level Runtime

**Files:**
- Modify: `src/storage/paths.py`
- Modify: `src/storage/messages.py`
- Test: `tests/storage/test_paths.py`
- Test: `tests/storage/test_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests asserting compatibility workspace methods now return user-level locations:

```python
def test_workspace_files_dir_maps_to_user_files_dir(tmp_path, monkeypatch):
    from src.storage.paths import DataPaths

    paths = DataPaths(user_id="alice", workspace_id="old_ws", ea_root=str(tmp_path))

    assert paths.workspace_files_dir() == paths.files_dir()
```

Add a message-store test asserting workspace IDs do not create separate stores:

```python
def test_message_store_ignores_workspace_id(tmp_path):
    from src.storage.messages import MessageStore

    personal = MessageStore("alice", base_dir=tmp_path / "conversation", workspace_id="personal")
    other = MessageStore("alice", base_dir=tmp_path / "conversation", workspace_id="old_ws")

    personal.add_message("user", "hello", session_id="s1")
    assert [m.content for m in other.get_messages_by_session_id("s1")] == ["hello"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/storage/test_paths.py tests/storage/test_messages.py -v`

- [ ] **Step 3: Implement user-level path aliases**

Add or use user-level methods like `files_dir()`, `user_skills_dir()`, `user_subagents_dir()`, `user_memory_dir()`, and make `workspace_*` methods call them.

Expected behavior:

```python
def workspace_files_dir(self) -> Path:
    return self.files_dir()

def workspace_skills_dir(self) -> Path:
    return self.user_skills_dir()

def workspace_subagents_dir(self) -> Path:
    return self.user_subagents_dir()

def workspace_memory_dir(self) -> Path:
    return self.user_memory_dir()
```

- [ ] **Step 4: Make MessageStore ignore workspace_id for storage path**

When `base_dir` is not passed, always use `paths.conversation_dir()`.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/storage/test_paths.py tests/storage/test_messages.py -v`

Expected: PASS.

---

### Task 2: Remove ItemScopeDB From Runtime Construction

**Files:**
- Modify: `src/sdk/runner.py`
- Modify: `src/sdk/tools_core/skills.py`
- Modify: `src/sdk/coordinator.py`
- Test: `tests/sdk/test_runner.py`
- Test: `tests/sdk/test_skills.py`
- Test: `tests/sdk/test_subagent_v1.py`

- [ ] **Step 1: Write failing tests or update existing tests**

Add assertions that runtime code does not depend on `ItemScopeDB`:

```python
def test_create_sdk_loop_does_not_import_item_scopes(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise AssertionError("runtime must not import item_scopes")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
```

Use existing provider/tool stubs in `tests/sdk/test_runner.py` so the test does not call a live provider.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/sdk/test_runner.py tests/sdk/test_skills.py tests/sdk/test_subagent_v1.py -v`

- [ ] **Step 3: Remove item scope filtering from runner**

Remove imports and filtering using `ItemScopeDB`. Native tools should be available unless disabled by user-level capabilities.

- [ ] **Step 4: Remove item scope checks from skills tool**

Delete `_skill_available` logic that queries `ItemScopeDB`. Skill availability becomes user-level: if the skill exists in the user skill catalog, it can load.

- [ ] **Step 5: Remove item scope checks from subagent coordinator**

Subagent listing should return user-level subagents without workspace filtering.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/sdk/test_runner.py tests/sdk/test_skills.py tests/sdk/test_subagent_v1.py -v`

Expected: PASS.

---

### Task 3: Remove Scope CRUD From Tools, Skills, And Subagents APIs

**Files:**
- Modify: `src/http/routers/tools.py`
- Modify: `src/http/routers/skills.py`
- Modify: `src/http/routers/subagents.py`
- Test: `tests/api/test_skills_api.py`
- Test: `tests/api/test_subagents.py`

- [ ] **Step 1: Update tests for compatibility response shape**

For list responses, keep cheap compatibility fields:

```python
assert item["enabled"] in (True, False)
assert item["scope"] in ("all", "none")
assert item["workspace_ids"] == []
```

Semantics:

```text
enabled=true  -> scope="all", workspace_ids=[]
enabled=false -> scope="none", workspace_ids=[]
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `uv run pytest tests/api/test_skills_api.py tests/api/test_subagents.py -v`

- [ ] **Step 3: Remove `ItemScopeDB` imports and writes**

Delete all direct use of `ItemScopeDB` from the three routers.

- [ ] **Step 4: Keep request compatibility**

If endpoints still accept `scope` and `workspace_ids`, translate only to user-level enabled state:

```python
enabled = body.scope != "none"
```

Ignore `workspace_ids`.

- [ ] **Step 5: Reset user loops after enabled-state changes**

Call:

```python
reset_user_sdk_loops(user_id, reason="capabilities_changed")
```

- [ ] **Step 6: Run focused API tests**

Run: `uv run pytest tests/api/test_skills_api.py tests/api/test_subagents.py -v`

Expected: PASS.

---

### Task 4: Add Shared Stream Adapter And Persistence Helpers

**Files:**
- Create: `src/http/stream_adapter.py`
- Create: `src/http/conversation_persistence.py`
- Modify: `src/http/routers/conversation.py`
- Modify: `src/http/routers/ws.py`
- Test: `tests/api/test_stream_adapter.py`
- Test: `tests/api/test_conversation_persistence.py`
- Test: `tests/api/test_conversation.py`
- Test: `tests/api/test_ws_protocol.py`

- [ ] **Step 1: Add stream adapter tests**

```python
from src.http.stream_adapter import StreamEvent, adapt_stream_chunk
from src.sdk.messages import StreamChunk


def test_text_delta_uses_canonical_type():
    event = adapt_stream_chunk(StreamChunk(type="ai_token", content="hello"))
    assert event == StreamEvent(kind="text_delta", content="hello")
```

- [ ] **Step 2: Add persistence helper tests**

Use a fake conversation object and assert tool, reasoning, and assistant messages are persisted with `session_id` and no `workspace_id` metadata.

- [ ] **Step 3: Implement helpers**

`StreamEvent` should include `kind`, `content`, `tool`, `call_id`, `args`, and `result_preview`.

Persistence helpers should write:

```python
conversation.add_message("assistant", content, metadata={"stream": stream}, session_id=session_id)
```

No new `workspace_id` metadata should be written.

- [ ] **Step 4: Refactor REST/SSE/WS to use helpers**

Branch on `event.kind`, not direct `chunk.type`, except where transport format needs the original chunk.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/api/test_stream_adapter.py tests/api/test_conversation_persistence.py tests/api/test_conversation.py tests/api/test_ws_protocol.py -v`

Expected: PASS.

---

### Task 5: Simplify SDK Loop Cache To User Session Boundary

**Files:**
- Modify: `src/sdk/runner.py`
- Test: `tests/sdk/test_runner.py`

- [ ] **Step 1: Add cache-key tests**

Assert `workspace_id` does not affect cache key:

```python
def test_loop_cache_key_ignores_workspace_id():
    from src.sdk.runner import _loop_cache_key

    assert _loop_cache_key("alice", "personal", "model", session_id="s1") == _loop_cache_key(
        "alice", "old_ws", "model", session_id="s1"
    )
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/sdk/test_runner.py::test_loop_cache_key_ignores_workspace_id -v`

- [ ] **Step 3: Update cache key**

Keep `workspace_id` parameter for compatibility, but do not include it in the key:

```python
key = f"{user_id}:{model or 'default'}"
```

Continue including `session_id` and provider key hash.

- [ ] **Step 4: Update reset helpers**

`reset_sdk_loop(user_id, workspace_id, session_id)` should ignore `workspace_id` and remove by user/session. `reset_user_sdk_loops` should accept `reason` and return removed count.

- [ ] **Step 5: Run runner tests**

Run: `uv run pytest tests/sdk/test_runner.py -v`

Expected: PASS.

---

### Task 6: Delete Item Scopes Module And Update Tests

**Files:**
- Delete: `src/sdk/item_scopes.py`
- Modify/Delete: `tests/sdk/test_item_scopes.py`
- Search all `src/**/*.py` and `tests/**/*.py` for `item_scopes` / `ItemScopeDB`

- [ ] **Step 1: Search for remaining references**

Run: `rg "item_scopes|ItemScopeDB" src tests`

- [ ] **Step 2: Remove remaining references**

No production code should import `src.sdk.item_scopes`.

- [ ] **Step 3: Delete or rewrite item scope tests**

If the tests only cover deleted storage behavior, delete them. If they assert user-level enablement behavior, move them to capabilities tests.

- [ ] **Step 4: Run reference search again**

Run: `rg "item_scopes|ItemScopeDB" src tests`

Expected: no production references; docs may still mention historical design.

---

### Task 7: Verification Sweep

**Files:**
- No new files expected

- [ ] **Step 1: Run focused backend tests**

Run: `uv run pytest tests/api/ tests/sdk/ tests/storage/ -v`

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ tests/`

- [ ] **Step 3: Run type check**

Run: `uv run mypy src/`

- [ ] **Step 4: Summarize compatibility behavior**

Document in the final implementation summary:

- `workspace_id` accepted but ignored.
- `session_id` is the conversation boundary.
- `item_scopes` removed.
- Tools, skills, subagents, files, and memory are user-level.

---

## Self-Review

- Spec coverage: Covers removing workspace runtime semantics, deleting item scopes, keeping session separation, and cleaning streaming/persistence drift.
- Placeholder scan: No TBD/TODO implementation placeholders remain.
- Scope check: This is a backend runtime cleanup. Frontend workspace/scope UI removal is intentionally deferred unless tests require compatibility fields.
- Type consistency: New helper names are consistent: `StreamEvent`, `adapt_stream_chunk`, `persist_tool_messages`, `persist_reasoning_message`, `persist_assistant_message`, and `reset_user_sdk_loops`.
