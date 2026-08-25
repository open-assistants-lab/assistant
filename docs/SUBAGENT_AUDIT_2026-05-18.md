# Subagent Component Audit — 2026-05-18

## 1. Architecture Overview

The subagent system lets the main LLM (and the user through the Flutter UI) spawn child agents. Each subagent runs in its own `AgentLoop` with its own provider, toolset, system prompt, and lifecycle managed by a SQLite-backed work queue.

```
┌─────────────────────────────────────────────────────────────┐
│                    Main Agent Loop                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  LLM decides to call subagent_create / subagent_start │  │
│  └──────────┬────────────────────────────────────────────┘  │
│             │ calls SDK tool                                │
│             ▼                                               │
│  ┌─────────────────────┐                                    │
│  │ tools_core/subagent │  (sync bridge → async loop)       │
│  └──────────┬──────────┘                                    │
└─────────────┼───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│                  SubagentCoordinator                        │
│  • validate_agent_def()       • load_def()                  │
│  • create() / update()        • delete()                    │
│  • start() → asyncio.create_task(_run_job())                │
│  • _run_job() → claim_task → _run_loop()                    │
│  • _run_loop() → AgentLoop + middlewares                    │
└─────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│                     WorkQueueDB (SQLite)                    │
│  Per-user: data/users/{id}/subagents/work_queue.db         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ work_queue table: 18 columns                         │  │
│  │ id, parent_id, user_id, workspace_id, agent_name,    │  │
│  │ task, status, progress, result, error, instructions, │  │
│  │ config, cancel_requested, claimed_by, timestamps     │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Subagent AgentLoop                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Middleware stack:                                     │  │
│  │  1. ProgressMiddleware   — progress updates + doom    │  │
│  │  2. InstructionMiddleware— cancel + instructions      │  │
│  │  3. SummarizationMiddleware— token compression        │  │
│  │  4. ObservationMiddleware — memory observations        │  │
│  │                                                        │  │
│  │  Tools: resolved by _build_tools_for_subagent()       │  │
│  │  Model: from AgentDef.model or settings default       │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Lifecycle of a Subagent

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐   ┌───────────┐
  │  CREATE  │──▶│  START   │──▶│  CLAIM   │──▶│   RUNNING   │──▶│ COMPLETED │
  └──────────┘   └──────────┘   └──────────┘   └─────────────┘   └───────────┘
       │              │                                               │
       │              │                                               │
       ▼              ▼                                               ▼
  AgentDef YAML  task inserted   heartbeat_loop(5s)              SubagentResult
  → disk persist  → status:      → progress updates              → stored in SQLite
                  "pending"      → InstructionMiddleware checks   → fetched via
                                 → cancel_requested poll          subagent_check
                                                                   
                                 ┌─── CANCELLING (via subagent_cancel)
                                 │      │
                                 │      ▼
                                 │  CANCELLED
                                 │
                                 └─── FAILED (timeout/error)
                                        │
                                        ▼
                                    FAILED
```

---

## 2. File-by-File Breakdown

### 2.1 `src/sdk/subagent_models.py` — 103 lines

**Exports:** `AgentDef`, `SubagentResult`, `TaskStatus`, error classes, defaults.

**`AgentDef` fields:**

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `name` | `str` | required | Regex: `^[a-zA-Z0-9_-]+$` |
| `description` | `str` | `""` | Shown in UI |
| `workspace_id` | `str` | `"personal"` | Set by coordinator at creation |
| `model` | `str\|None` | `None` | Falls back to main app model |
| `provider_options` | `dict` | `{}` | Passed through to provider |
| `system_prompt` | `str\|None` | `None` | Custom system prompt |
| **`tools`** | `list[str]\|None` | **`None`** | **`None` = ALL tools. This is the security problem.** |
| `disallowed_tools` | `list[str]` | `DEFAULT_DISALLOWED_TOOLS` | Only blocks `subagent_*` tools |
| `skills` | `list[str]` | `[]` | Skills to load; empty = no skills |
| `max_llm_calls` | `int` | `50` | |
| `cost_limit_usd` | `float` | `1.0` | |
| `timeout_seconds` | `int` | `300` | |
| `mcp_config` | `dict\|None` | `None` | MCP server config |
| `output_schema` | `dict\|None` | `None` | Structured output schema |
| `handoff_instructions` | `str\|None` | `None` | For future handoff |
| `artifact_policy` | `str\|None` | `None` | Not yet implemented |

### 2.2 `src/sdk/work_queue.py` — 440 lines

Purpose: Async SQLite persistence layer for task coordination.

**Schema:** 18 columns, 3 indexes, WAL journal.

**Key methods:**
- `insert_task()` → generates hex ID, inserts PENDING row, freezes `AgentDef` as JSON in `config` column
- `claim_task()` → atomic `UPDATE ... WHERE status = 'pending'`, prevents double-claim
- `heartbeat()` → updates heartbeat_at every 5s in coordinator's `_heartbeat_loop`
- `set_completed()` / `set_failed()` → terminal with `cancel_requested = 0` guard
- `set_cancelled()` → unconditional terminal (bypasses cancel_requested guard)
- `mark_stale_running_failed()` → exists but **never called** from any startup code
- `request_cancel()` → sets `cancel_requested = 1`, transitions RUNNING→CANCELLING

**Singleton cache:** `_db_cache: dict[str, WorkQueueDB]` — never cleaned, accumulates per `user_id:workspace_id` for process lifetime.

### 2.3 `src/sdk/coordinator.py` — 563 lines

Purpose: Ties everything together — creates AgentDefs, starts jobs, assembles AgentLoops.

**Creation & management:**
- `create()` — writes AgentDef YAML to `data/users/{id}/subagents/{name}/config.yaml`
- `update()` — partial `model_copy(update=...)`, re-persists YAML
- `delete()` — cancels active tasks, `shutil.rmtree()` agent directory
- `load_def()` — workspace-first, user-global fallback, catches all exceptions

**Job lifecycle:**
- `start()` — validates, inserts task, `asyncio.create_task(_run_job())`, returns task_id immediately
- `_run_job()` — `claim_task()`, starts heartbeat loop, calls `_run_loop()` with timeout, handles cancel→completed race, cleans up heartbeat
- `_run_loop()` — assembles AgentLoop with middlewares, runs it, calculates cost, returns SubagentResult

**`invoke()` — dead code path:** Old synchronous-style method that bypasses `validate_agent_def()` and `claim_task()`. No longer called from any tool. The LLM can only use `subagent_start` (fire-and-forget).

**Tool resolution (`_build_tools_for_subagent`):**
```python
def _build_tools_for_subagent(agent_def):
    all_native = get_native_tools()
    tool_map = {t.name: t for t in all_native}

    allowed = set(agent_def.tools) if agent_def.tools else set(tool_map.keys())  # ← ALL tools
    disallowed = set(agent_def.disallowed_tools)
    final = allowed - disallowed
    # Remove subagent_*, extra memory_*, skill management tools
    final.update({"memory_search"})  # mandatory
    if agent_def.skills:
        final.add("skills_load")

    return [tool_map[n] for n in sorted(final) if n in tool_map]
```

**Singleton cache:** `_coordinators: dict[str, SubagentCoordinator]` — same leak pattern.

### 2.4 `src/sdk/middleware_progress.py` — 85 lines

Runs `abefore_model` hook inside subagent's AgentLoop.

**Does:**
- Extracts last tool result, counts steps
- Detects doom loops (same tool+args 3x)
- Writes progress dict to work_queue
- Auto-injects instruction on doom loop

**Doom loop hash is fragile:**
```python
tool_args = {}
try:
    parsed = json.loads(last_result.content)  # expects JSON dict
    if isinstance(parsed, dict):
        tool_args = parsed
except: pass

args_json = json.dumps(tool_args or {})  # string tools → "{}"
call_hash = f"{tool_name}:{md5(args_json)[:8]}"  # all string calls identical
```

### 2.5 `src/sdk/middleware_instruction.py` — 62 lines

Runs `abefore_model`, polls work_queue for cancel + new instructions.

**Cancel:** raises `TaskCancelledError` → caught by coordinator → `CANCELLED` status.

**Instructions:** filters by `added_at > _last_checked`, injects as `[Supervisor Update]` system messages.

**`_last_checked` is not persisted** — server restart re-injects all instructions.

### 2.6 `src/sdk/tools_core/subagent.py` — 564 lines

9 SDK-native tools registered via `@tool` decorator.

**The async bridge (critical risk):**
```python
_loop: asyncio.AbstractEventLoop | None = None

def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_loop.run_forever, daemon=True)
        thread.start()
    return _loop

def _run_async(coro) -> Any:
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return future.result()  # blocks synchronously on async result
```

Every `subagent_*` tool call routes through this bridge. If the daemon thread crashes or the event loop errors, `future.result()` hangs forever — no timeout, no recovery, no error propagation.

**Tool list:**

| Tool | Destructive | Returns | Notes |
|------|-------------|---------|-------|
| `subagent_create` | ✅ | confirmation | Requires `user_id` as positional param (non-standard) |
| `subagent_update` | ✅ | confirmation | Partial update via `model_copy(update=...)` |
| `subagent_delete` | ✅ | confirmation | Cancels + deletes |
| `subagent_list` | ❌ (read-only) | list of defs + tasks | |
| `subagent_start` | ⚠️ (open_world) | job ID | Fire-and-forget |
| `subagent_check` | ❌ (read-only) | single job status | Returns formatted Markdown string |
| `subagent_tasks` | ❌ (read-only) | job list by status | |
| `subagent_instruct` | ⚠️ | confirmation | Injects course-correction |
| `subagent_cancel` | ✅ | confirmation | Sets cancel_requested |

### 2.7 `src/http/routers/subagents.py` — 292 lines

10 route registrations across two routers: 9 on the `/subagents` router plus 1 on the `tools_router`. Used by Flutter UI.

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/tools/names` | List tool names (for create dialog picker) — **exists and works on `tools_router`** |
| `GET` | `/subagents` | List agent defs with scope (uses `list_defs_with_scope()`) |
| `POST` | `/subagents` | Create agent def |
| `PATCH` | `/subagents/{name}` | Update agent def |
| `DELETE` | `/subagents/{name}` | Delete agent def |
| `POST` | `/subagents/{name}/start` | Start subagent job |
| `GET` | `/subagents/jobs` | List jobs |
| `GET` | `/subagents/jobs/{job_id}` | Get single job |
| `POST` | `/subagents/jobs/{job_id}/instructions` | Send instruction |
| `POST` | `/subagents/jobs/{job_id}/cancel` | Cancel job |

**Not implemented (defined in design spec):**
- `GET /subagents/jobs/{job_id}/events?after_sequence=...` — per-message transcript

**Scope in responses:** The `GET /subagents` response correctly includes `"scope": "workspace" | "user"` via `coordinator.list_defs_with_scope()`. The design spec's concern about missing scope metadata was previously valid but is now implemented.

**Request model:** `SubagentCreateRequest` has no `scope` field, so all subagents created via the REST API are workspace-scoped. Creating user-scoped subagents through the UI requires a backend change or an explicit `scope` parameter.

**Type mismatch (SDK tool path only):** `SubagentCreateRequest.provider_options` is `dict`, but the SDK tool `subagent_create` accepts `provider_options: str | None` (JSON string parsed with `_parse_object_json`). This does NOT affect the HTTP route (which creates `AgentDef` directly), only the LLM-tool path. The Flutter UI uses the HTTP route, not the tool, so it is unaffected.

### 2.8 `flutter_app/lib/models/subagent.dart` — 114 lines

`SubagentAgentDef` and `SubagentJob` with JSON deserialization. `SubagentPanelTab` enum defined but unused.

**`SubagentJob.result` typed as `String?`** — but backend stores it as a JSON object. `fromJson` uses `.toString()` which gives `"Instance of '...'"` for Map values. Actual rendering works because the backend pre-serializes.

### 2.9 `flutter_app/lib/providers/subagent_provider.dart` — 221 lines

`SubagentNotifier` (Riverpod `StateNotifier`):
- `loadList()` — fetches agent defs + jobs
- `createAgent()` — POST to backend
- `startJob()` / `cancelJob()` / `instructJob()` — lifecycle actions
- `_ensurePolling()` — `Timer.periodic(2s)` while non-terminal jobs exist
- Stale-response guard via `_loadSequence`

**Issues:**
- Terminal jobs accumulate in `activeJobs` forever
- Poll timer created once, not reset when new jobs start
- `pollJob()` swallows all exceptions silently
- `scope` parameter in `createAgent()` is sent to backend but HTTP request model has no `scope` field

### 2.10 `flutter_app/lib/features/workspace/subagents_panel.dart` — 1254 lines

**Widgets:**
- `SubagentsPanel` — main panel with header + agent list
- `_SubagentTile` — per-agent card with actions
- `_ScopeBadge` / `_StatusBadge` — styled pills
- `_ScopeSelector` — Radio buttons (user vs workspace scope)
- `_NumberField` — numeric input (creates new `TextEditingController` on every rebuild)
- `_DetailInfoRow` — agent info display
- `_JobCard` — job status card

**Dialogs:**
- Create — name, description, scope, model, system prompt, limits, fake tool picker
- Edit — same as create, pre-filled, name NOT editable
- Start — task text area
- Detail — agent info + job history + action buttons
- Instruct — instruction text input
- Confirm Delete — with warning

**The broken tool picker (lines 306-319):**
```dart
allTools
    .where((t) => !t.startsWith('subagent_'))
    .map((t) => CheckboxListTile(
        value: true,          // always checked
        onChanged: null,      // never toggles
        title: Text(t, ...),
    ))
```
All tools are fetched and displayed, but the user can never restrict them. The selected tools are never passed to `createAgent()` — it sends `null` for `tools`, which means ALL tools on the backend.

**Integration:**
- Listens to `currentWorkspaceIdProvider` — reloads on switch
- Listens to `agentProvider` — reloads after main chat finishes (catches chat-created subagents)
- 1 Flutter test covers the refresh behavior; 0 tests cover dialogs

---

## 3. Tool/Skill Assignment Architecture

### 3.1 How tools are resolved (coordinator.py lines 91-111)

```
AgentDef.tools = None
  → allow ALL native tool names
AgentDef.tools = ["web_search", "memory_search"]
  → allow only those names

THEN subtract AgentDef.disallowed_tools
THEN subtract subagent_* tools (always)
THEN subtract extra memory_* tools (always, except memory_search)
THEN subtract skill management tools (always)
THEN add memory_search (always, mandatory)
THEN add skills_load IF AgentDef.skills is non-empty
```

### 3.2 The default security problem

When the user (or LLM) creates a subagent without specifying `tools`, the subagent gets EVERYTHING:

```
shell_execute       → arbitrary command execution
files_delete        → delete any workspace file
files_write         → write any workspace file  
files_edit          → edit any workspace file
email_send          → send email as the user
browser_click/input/type → full browser automation
firecrawl_*         → web scraping and crawling
subagent_create/start  → NO (blocked by default)
```

The `disallowed_tools` default only prevents recursion (`subagent_*` tools). There is no concept of "minimum privilege" — no tiered tool access, no read-only by default, no security-oriented defaults.

### 3.3 How skills are loaded

If `AgentDef.skills` is specified:
- `skills_load` is added to the tool list
- The system prompt includes skill descriptions with progressive disclosure ("Call skills_load before following instructions")
- The subagent must explicitly call `skills_load(skill_name=...)` to load skill context

If `AgentDef.skills` is empty (default):
- No skills loaded
- Subagent has no skill context
- This is the safer default (unlike tools)

### 3.4 LLM Incentive Problem

The `subagent_create` tool signature accepts optional `tools` and `disallowed_tools` parameters, but:
1. The LLM has no system-prompt guidance like *"When creating a subagent, restrict its tools to the minimum needed for its task."*
2. The LLM typically calls `subagent_create(name="scraper", description="scrapes websites")` with no tool restrictions
3. The Flutter UI's tool picker is literally disabled
4. Result: every subagent created in practice has full system access

---

## 4. Critical Risks (P0)

### 4.1 Background thread deadlock

**Where:** `src/sdk/tools_core/subagent.py` lines 31-47

**What:** A single daemon thread running `asyncio.run_forever()`. If it crashes (unhandled exception in any coroutine), `_loop.is_closed()` returns `True`, so `_get_loop()` creates a new loop and thread. But `future.result()` on the old loop's futures still hangs.

**Impact:** Every `subagent_*` tool call silently blocks forever. No timeout, no error, no recovery. The main agent appears to hang mid-conversation.

**Why it exists:** The SDK tool system (`@tool` decorator) expects synchronous functions. The coordinator is async (SQLite via `aiosqlite`). The bridging pattern was necessary but fragile.

### 4.2 No stale job recovery on restart

**Where:** `src/sdk/work_queue.py` lines 259-281 (`mark_stale_running_failed` exists but is never called)

**What:** If the backend restarts while subagent jobs are running (deploy, crash, `kill -9`), those tasks remain in `RUNNING` or `CANCELLING` status in SQLite. The heartbeat timestamp stays frozen at the restart time.

**Impact:** The Flutter UI shows "in progress" jobs that will never complete. Users cannot interact with them (cancel/instruct silently logs because the worker process no longer exists). The 5-second heartbeat loop is gone, so `mark_stale_running_failed()` is the only recovery path — and it's not wired up.

**Where it should be called:** `coordinator.py` init, `http/main.py` startup, or `WorkQueueDB.__init__` / first access.

### 4.3 `load_def()` silently swallows errors

**Where:** `coordinator.py` lines 531-553

```python
def load_def(self, name: str) -> AgentDef | None:
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
        return AgentDef(**data)
    except Exception as e:
        logger.warning("subagent.load_failed", ...)
    # ... global fallback ...
    try:
        ...
    except Exception as e:
        logger.warning("subagent.load_failed", ...)
    return None
```

**What:** Every exception (YAML parse error, Pydantic validation error, file permissions, missing fields) returns `None`. Downstream cannot distinguish "subagent doesn't exist" from "config file is corrupt."

**Impact:** The LLM gets `"subagent 'foo' not found"` and retries creation. The user sees "404" in the UI. No error details surfaced.

---

## 5. Half-Finished Features

### 5.1 Tool picker is a facade (Flutter)

**Where:** `subagents_panel.dart` lines 306-319

All checkboxes are `value: true, onChanged: null`. The infrastructure exists (fetch from `/tools/names`, filter `subagent_*`, render as list) but the interactive state is deliberately disabled.

**Why it's this way:** Likely deferred because the tool list is long (~80+ tools) and a simple checkbox list is bad UX at that scale. Needs categorization or search.

### 5.2 Model field is free-text

**Where:** `subagents_panel.dart` create dialog — plain `TextField` with no autocomplete.

The backend has `GET /models` returning all supported models from models.dev. The design spec shows a dropdown with suggestions. Neither is implemented.

### 5.3 No synchronous invoke for LLM

**Where:** `subagent_start` returns immediately; `invoke()` in coordinator is dead code.

The LLM cannot say "go do this and come back with the result." It must manually chain `subagent_start` → poll `subagent_check` → read result. Most LLMs don't do this because:
1. It's complex and they lose track
2. The tools are marked as `open_world` / `destructive`, not "blocking"
3. The system prompt doesn't explain the pattern

### 5.4 No per-message transcript endpoint

Design spec defines `GET /subagents/jobs/{id}/events?after_sequence=...`. Not implemented. The detail dialog shows progress maps (`steps_completed`, `phase`, `message`) but no actual conversation history.

### 5.5 `ObservationMiddleware` — runtime import risk

`coordinator._run_loop()` imports `ObservationMiddleware` at runtime:
```python
from src.sdk.middleware_observation import ObservationMiddleware
```
The module exists (271 lines, `src/sdk/middleware_observation.py`) and the import succeeds in normal operation. However, the import is inside `_run_loop()` rather than at module top level, so if the module is moved or renamed the failure would be a runtime `ImportError` caught by the generic `except Exception` handler in `_run_job()`, silently failing the subagent with a vague error instead of catching it at startup.

---

## 6. Architectural Concerns

### 6.1 Global caches never cleaned

Both `work_queue._db_cache` and `coordinator._coordinators` are plain dicts:
- Keyed by `user_id:workspace_id`
- Accumulate for process lifetime
- No `close()` on app shutdown
- No eviction, no memory limit, no LRU

For a long-running server with many users, these grow unbounded. Each `WorkQueueDB` holds an open `aiosqlite.Connection`.

### 6.2 Doom loop detection is fragile

`ProgressMiddleware` hashes tool results to detect the same tool+args 3x:

```python
try:
    parsed = json.loads(last_result.content)
    if isinstance(parsed, dict):
        tool_args = parsed
except: pass  # non-JSON → tool_args = {}
args_json = json.dumps(tool_args or {})  # always "{}" for strings
```

Tools that return strings (`shell_execute`, `files_list`, etc.) always hash to `"{}"`. Every call to `shell_execute("ls")` looks identical to `shell_execute("rm -rf /")` — same hash. Result: false positive doom loops on string-returning tools.

### 6.3 `AgentDef.name` regex is strict

`pattern=r"^[a-zA-Z0-9_-]+$"` — no spaces, no dots, no special chars. The Flutter UI enforces no client-side validation, producing opaque 422 errors.

### 6.4 `user_id` required in tool params

`subagent_create(name, user_id, ...)` — `user_id` is a required positional parameter. Most SDK tools use `user_id="default_user"` or extract it from context. This is non-standard and makes the tool harder to test.

### 6.5 Race conditions in delete

`coordinator.delete()`:
```python
await db.request_cancel_active_tasks_for_agent(name)
shutil.rmtree(agent_path)  # task could start between these lines
```

If a `subagent_start` call arrives after cancel request but before `rmtree`, the agent config is gone but the task runs. The `load_def()` in `start()` would return `None` → ValueError → 404 to caller, but the background job might already be created.

---

## 7. Test Gaps

| Area | Tests | Status |
|------|-------|--------|
| SDK coordinator + work queue | ~60 tests in `test_subagent_v1.py` | Good coverage of happy path + cancel races. Includes `mark_stale_running_failed` test but the method is not called from production code. |
| Tool registration | ~11 tests in `test_subagent_tools_async.py` | JSON field parsing, validation |
| HTTP endpoints | ~12 tests in `test_api_subagents.py` | CRUD lifecycle, 404s, invalid IDs |
| Interaction evaluations | 12 tests in `test_subagent_skills.py` | **Skipped by default** — requires `RUN_HTTP_EVALS=1` + running server |
| Flutter panel widget | 0 dedicated tests (2 existing Flutter test files reference subagents only tangentially: `agent_provider_test.dart`, `workspace_panel_test.dart`) | **Severely insufficient** — no dialog, provider, or error tests |
| Flutter provider | 0 dedicated tests | **None** |
| Security/permissions | 0 | **None** — no test verifies tool restrictions |
| Doom loop edge cases | 0 | **None** — no test for string-returning tools |
| Background thread crash recovery | 0 | **None** |
| Stale job recovery | 0 dedicated tests (test exists for `mark_stale_running_failed` but it's never called in production) | **None** |
| Concurrent agent creation | 0 | **None** |
| Corrupt config file behavior | 0 | **None** |

---

## 8. Summary by Priority

| Pri | Issue | Where | Impact |
|-----|-------|-------|--------|
| **P0** | Background thread death → silent hang | `subagent.py` | All subagent tool calls hang forever |
| **P0** | No stale job recovery on restart | `work_queue.py` | Zombie RUNNING tasks after restart |
| **P0** | Default tool set is all tools | `coordinator.py` `_build_tools_for_subagent()` | Subagents can `shell_execute`, `files_delete`, `email_send` by default |
| **P1** | Tool picker disabled in UI | `subagents_panel.dart:306-319` | Users cannot restrict tools through UI |
| **P1** | Model field is free-text | `subagents_panel.dart` | No validation, no autocomplete |
| **P1** | No blocking invoke for LLM | `coordinator.py` `invoke()` dead code | LLM cannot get subagent results inline |
| **P1** | No per-message transcript | not implemented | No subagent conversation review |
| **P2** | `load_def()` swallows errors | `coordinator.py:531-553` | Can't distinguish "not found" from corruption |
| **P2** | Global caches never cleaned | `work_queue.py` + `coordinator.py` | Memory leak over long runs |
| **P2** | Doom loop fragile on string tools | `middleware_progress.py` | False positive doom loops |
| **P2** | No Flutter widget tests | — | UI will break silently |
| **P3** | `ObservationMiddleware` silent import | `coordinator.py:373` | Missing middleware unnoticed |
| **P3** | Terminal jobs accumulate | `subagent_provider.dart` | Unbounded state growth |
| **P3** | `user_id` required in tool params | `subagent.py` tool signatures | Non-standard, harder to test |
| **P3** | Delete race condition | `coordinator.py:451-460` | Job could start during deletion |

---

## 9. Design Context

The subagent system was built as **Phase 11** of the project. The design is documented in:

- `docs/SUBAGENT_RESEARCH.md` — original research and architecture
- `docs/superpowers/specs/2026-05-17-async-subagents-design.md` — async subagent design spec
- `docs/superpowers/specs/2026-05-18-subagent-ui-design.md` — Flutter UI design spec
- `docs/superpowers/plans/2026-05-17-async-subagents.md` — implementation plan (completed)

The system replaced an earlier LangChain-based `SubagentManager` with a custom SDK-native approach: SQLite work queue + async event loop + background job coordination.

The Flutter panel was added in the same phase with the design priority of "get it working, then refine." The tool picker being disabled and model field being free-text are known gaps from that approach — the plumbing exists but the UX polish was deferred.

**The system works end-to-end.** You can create a subagent, start a task, monitor progress, cancel it, and get results — through both the LLM and the UI. The concerns are about:
1. **Safety** — default tool exposure is too wide
2. **Resilience** — thread crash and restart scenarios are unhandled
3. **UX completeness** — tool picker, model selector, transcript view are half-built
4. **Observability** — errors are silently swallowed in multiple locations
