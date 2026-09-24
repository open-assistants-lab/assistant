# Subagent Architecture Research and Historical V1 Design

**Status**: Historical research plus implemented reliability contract (updated 2026-09-24)
**Original research**: 2026-04-21
**Context**: Framework comparisons and the original V1 proposal are retained for history. The current implementation contract below supersedes conflicting proposal text; see `docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md` for the decision record.

---

## Original System Snapshot (2026-04; historical)

At the time of the original research, there were **two** subagent mechanisms that did not talk to each other:

### 1. `Handoff` (in `src/sdk/handoffs.py`)
- **Pattern**: Transfer/handoff (like OpenAI Swarm)
- Exposed as `transfer_to_{agent_name}` tool
- The LLM calls it to switch control to another agent
- `input_filter` controls what conversation history the receiving agent sees
- **Problem**: This is a **transfer**, not delegation-and-return. The parent loses control.

### 2. `SubagentManager` (in `src/subagent/manager.py`)
- **Pattern**: Delegation-and-return (like Claude Code's `Agent` tool)
- Parent creates a subagent, gives it a task, gets output back
- Subagent runs in its own `AgentLoop` with its own tools, model, system prompt
- Tool scoping: `config.tools` is an allowlist from native tools
- Skills are injected into system prompt at creation time
- Results: Only the last assistant message text is returned
- Supports batch invocation via `ThreadPoolExecutor`
- Supports scheduling (one-time, recurring via cron)
- **Problem**: No context isolation — subagent gets the full AgentLoop but no way to limit its iterations or cost. Results are extracted crudely (last assistant message). No streaming support.

### Key Gaps
1. **No true context isolation** — subagents share the same process and could theoretically access parent state
2. **No `maxTurns`** — subagent can loop forever
3. **No cost tracking per subagent** — `CostTracker` is per-loop, not aggregated
4. **Results extraction is fragile** — scanning reversed messages for last assistant content
5. **No progressive output** — parent can't see subagent progress until it finishes
6. **Config format is ad-hoc** — YAML with no schema validation beyond `SubagentConfig`
7. **No supervision** — parent can't check on, course-correct, or kill a running subagent
8. **No inter-agent coordination** — no shared state, no result passing between subagents
9. **`Handoff` not wired** — defined but never integrated into `AgentLoop` execution
10. **Subagents can recurse** — no `disallowed_tools` to prevent subagent-from-subagent creation
11. **No subagent reuse** — `subagent_create` always creates new; no way to update existing

---

## Framework Comparison

### 1. Claude Code — Orchestrator-Worker

**Architecture:** Parent agent invokes `Agent` tool, which spawns a child with fresh context. Child runs to completion, returns final output text wrapped in `<task_result>` tags. No parallel subagents.

**Key patterns:**
- Context isolation: fresh context window per subagent
- `maxTurns` to prevent infinite loops
- Model-driven: LLM chooses which subagent to invoke based on descriptions
- `permissionMode` and `isolation` levels

### 2. OpenAI Swarm — Handoff/Relay

**Architecture:** Agents hand off control to each other via function calls returning `Agent` objects. No hierarchy — any agent can transfer to any other.

**Key patterns:**
- Shared conversation context across handoffs
- Model-driven routing via tool descriptions
- Simple but no return-from-delegation (parent loses control)

### 3. Google ADK — Hierarchical Tree

**Architecture:** `LlmAgent` can have `sub_agents` declared on it. Framework auto-generates `transfer_to_{name}` tools. Supports both sequential and parallel sub-agent execution.

**Key patterns:**
- Declarative `sub_agents=[...]` on parent
- Auto-generated transfer tools (model-driven)
- Partial isolation — shared session, separate prompts
- Native A2A support for remote agents

### 4. Pydantic AI — Manual Pipeline

**Architecture:** No built-in multi-agent. Developers manually chain `Agent.run()` calls, passing typed `RunResult.output` between them.

**Key patterns:**
- Strongly typed output
- Complete isolation per agent call
- No framework-level coordination

### 5. CrewAI — Role-Based Crew

**Architecture:** Agents have `role`, `goal`, `backstory`. Tasks are assigned to agents or delegated by a manager. Sequential or hierarchical process.

**Key patterns:**
- Role-based agent definitions
- Manager agent orchestrates via delegation
- Tasks have assigned agents
- `human_input` flag for HITL

### 6. OpenCode — Dual Mechanism: `task` Tool + Agent Teams

**Architecture:** Two independent multi-agent systems: hierarchical subagents via `task` tool, and peer-to-peer agent teams with JSONL-based messaging.

**Key patterns:**
- Context isolation with `parentID` linking
- Permission cascade: each layer only restricts further
- Doom loop detection: same tool call 3× → escalate
- Session resumption via `task_id`
- Fire-and-forget + auto-wake for team agents

### 7. Planning-with-Files (Manus Pattern)

**Architecture:** A skill that enforces persistent markdown planning: `task_plan.md`, `progress.md`, `findings.md`. Agents re-read `task_plan.md` before each tool call.

**Strengths:** Self-organization for long sessions, durable across crashes.
**Weaknesses:** Designed for single-agent context decay, not inter-agent coordination. Markdown is fragile for structured data exchange. No atomic operations.

**Our decision:** Available as an optional internal skill for subagents. NOT used for inter-agent coordination. SQLite work_queue replaces it for coordination.

---

## Comparison Table

| Aspect | **Claude Code** | **OpenAI Swarm** | **Google ADK** | **OpenCode** | **Our V1** |
|---|---|---|---|---|---|
| **Pattern** | Orchestrator-Worker | Handoff/relay | Hierarchical tree | task + teams | Orchestrator + work_queue |
| **Delegation** | `Agent` tool | Function returning `Agent` | Auto-generated transfer tools | `task` tool | `delegate()` via work_queue |
| **Definition format** | Markdown+YAML | Python `Agent()` | Python/YAML | JSON or `.md` | `AgentDef` Pydantic model |
| **Context isolation** | Full | None | Partial | Full | Full (fresh AgentLoop) |
| **Tool scoping** | Allowlist + denylist | Own `functions` | Own `tools` | Wildcard rules, deny cascade | Allowlist + denylist |
| **Results flow** | Final output only | N/A | Child returns | `<task_result>` | `SubagentResult` in work_queue |
| **Progress monitoring** | ❌ | ❌ | ❌ | ❌ | ✅ work_queue.progress |
| **Course-correction** | ❌ | ❌ | ❌ | ❌ | ✅ work_queue.instructions |
| **Cancel/kill** | ❌ | ❌ | ❌ | ❌ | ✅ work_queue.cancel_requested |
| **`maxTurns`/`steps`** | ✅ | ❌ | ❌ | ✅ | ✅ `max_llm_calls` + `cost_limit_usd` |
| **Dynamic creation** | ❌ (predefined) | ✅ | ✅ | ✅ | ✅ runtime via `subagent_create` |
| **Reuse + amend** | ✅ (`.claude/agents/`) | ✅ | ✅ | ✅ (`.opencode/agents/`) | ✅ persistent AgentDef |
| **Parallel agents** | ❌ | ❌ | ❌ | ✅ | ✅ (multiple concurrent invokes) |

---

## Design Patterns That Emerge

### Pattern 1: Context Isolation is the Key Innovation

Our `SubagentManager._invoke_async()` already creates a fresh `AgentLoop` with only `[Message.user(task)]`. We keep this. Context isolation prevents parent context pollution and gives subagents a clean workspace.

### Pattern 2: Two Delegation Semantics

V1 supports **delegation-and-return** only. Handoff (transfer control permanently) is deferred to Phase 2 — it requires conversation-level changes that are orthogonal to the work_queue.

### Pattern 3: Model-Driven Routing (Phase 2)

V1 uses explicit start: `subagent_create` + `subagent_start`. Auto-generated `delegate_to_{name}` tools for model-driven routing are Phase 2.

### Pattern 4: Declarative Agent Definitions with Runtime Creation + Amendment

`AgentDef` serves dual purpose: declared in config for predefined agents, created dynamically at runtime, and amended for existing agents.

### Pattern 5: Structured Results via Work Queue

`SubagentResult` stored as JSON in the work_queue. No more scanning reversed messages. The main agent queries the DB for structured results.

### Pattern 6: Production Controls

`max_llm_calls` + `cost_limit_usd` from `AgentDef`, enforced by `AgentLoop`'s existing `CostTracker`. Doom loop detection in `ProgressMiddleware`. Cancel via `cancel_requested` column.

### Pattern 7: SQLite Work Queue for Coordination (Key Innovation)

No framework in our comparison uses a database-backed work queue for inter-agent coordination. This is simpler than file-based coordination, simpler than JSONL inboxes, and more capable than in-memory callbacks.

---

## Current Reliability Contract (2026-09-24)

This section describes implemented behavior; the V1 proposal below is historical where it differs.

- **Selection policy:** omitted/ambiguous legacy tool selection migrates visibly to `SAFE_DEFAULT`; explicit `tools=[]` means `NONE`; named tools mean exact `ALLOWLIST`. The runtime default is exactly `files_list`, `files_read`, `files_glob_search`, and `files_grep_search`. Custom and MCP tools are not admitted to child manifests by this change.
- **Launch preflight:** `start`, synchronous `delegate`, and deprecated compatibility `invoke` reject missing, disabled, forbidden, `ask`, or `deny` declarations before queue/provider/LLM work. `invoke` remains because external consumers are unknown; no in-repository production caller of `SubagentCoordinator.invoke` was found.
- **Manifest authority:** a successful launch stores a canonical manifest and full SHA-256 `plan_id`. Identity includes user, requested execution workspace, agent, selection mode, requested/effective tool and skill names, and deterministic resolved decision fields. Task execution and outbox replay prefer the frozen manifest; rows missing a frozen tool/skill snapshot fail closed rather than recomputing access. The coordinator cache is keyed by `(user_id, requested_workspace_id)`; user-level profile and queue storage remains separate from the requested execution workspace.
- **Child tool scope:** the four default file tools and skill tools hide model-visible identity fields and bind user/workspace from the coordinator. Curated file paths must be workspace-relative; absolute and parent-traversing paths fail. List/glob/grep skip symlinks resolving outside the workspace. Broader tools require explicit allowlists and normal preflight/governance checks.
- **Runtime governance:** actual arguments are still checked by child governance. A runtime `ask` stops the child with result-level `terminal_reason="blocked"` and `error_code="approval_required"`, without a child-owned pending proposal. Administrator `deny` remains authoritative. Main-agent HITL behavior is unchanged.
- **Skills:** the child can load only preflighted skill names. `skills_reload` does not expand that run-scoped allowlist; identity is bound for child skill tools.
- **Lifecycle and delivery:** `work_queue.status` owns task lifecycle. `result` owns output, usage, and result-level reason; `launch_plan` owns the execution capability snapshot; the completion outbox owns delivery state. Terminal transitions and outbox insertion are atomic. Delivery is at-least-once; the parent consumer uses `subagent-completion:{task_id}` and an indexed message-store key for idempotent conversation-message application, not cross-database or WebSocket exactly-once. Concurrent recovery drains may emit duplicate live WebSocket events; task claiming remains atomic, so this does not duplicate task execution. Every routable terminal outcome is replayed; a task without a parent session is acknowledged without a conversation message, while its durable result remains queryable. Completion payload copies are retained because no task/outbox retention policy or proven reduced-payload replay contract exists.
- **Terminal outcomes:** task status remains `completed`, `failed`, `timed_out`, or `cancelled`; `blocked` and `uncertain` are result-level distinctions. Empty final output is not success.
- **Loop integration:** cancellation before dispatch and after a model response is covered in streaming and non-streaming tests. Existing loop/middleware paths were retained because no safe duplicate-hook consolidation was demonstrated. Main-agent HITL regression tests remain in place.

We adopted selected Pi orchestration patterns—strict allowlists and preflight, immutable launch snapshots, lifecycle artifacts, replay, and verification-aware outcomes—without replacing Assistant's SQLite queue, per-user permission policy, governance approvals, or receipt authority.

## Original V1 Proposal (Historical; superseded where it conflicts with the contract above)

### Core Principle

The main agent is the supervisor. It dynamically creates subagents, delegates tasks, monitors progress, course-corrects, and cancels when needed. Subagents are isolated workers that do one task and return results.

```
User message → Main Agent
                    │
                    ├── Analyzes request
                    ├── Creates/reuses subagent(s)
                    ├── Delegates task(s) → work_queue
                    ├── Monitors progress (subagent_tasks)
                    ├── Course-corrects (subagent_instruct)
                    ├── Cancels if stuck (subagent_cancel)
                    └── Composes final answer from results
```

### Core Schemas

```python
class AgentDef(BaseModel):
    """Declarative subagent definition — created dynamically or loaded from config.
    Can be reused, amended, or created fresh."""
    name: str                                      # unique per user
    description: str = ""                          # shown to LLM for routing (Phase 2)
    model: str | None = None                       # model override (None = inherit from parent)
    system_prompt: str | None = None               # custom system prompt
    tools: list[str] | None = None                 # allowlist (None = all native tools)
    disallowed_tools: list[str] = [                # denylist — always includes subagent tools
        "subagent_create", "subagent_start",
        "subagent_list", "subagent_check", "subagent_tasks",
        "subagent_instruct", "subagent_cancel",
        "subagent_delete", "subagent_update",
    ]
    skills: list[str] = []                         # skill names to inject
    max_llm_calls: int = 50                         # per-task limit
    cost_limit_usd: float = 1.0                     # per-task cost limit
    timeout_seconds: int = 300                       # hard wall-clock timeout per task
    mcp_config: dict | None = None                  # per-subagent MCP server config


class SubagentResult(BaseModel):
    """Structured result from subagent invocation."""
    name: str
    task: str
    success: bool
    output: str                                     # final text output (may be truncated for large results)
    truncated: bool = False                          # True if output was truncated to fit token budget
    cost_usd: float = 0.0
    llm_calls: int = 0
    error: str | None = None
```

### Work Queue Schema (V1 — Simplified)

```sql
CREATE TABLE work_queue (
    id TEXT PRIMARY KEY,
    parent_id TEXT,                        -- groups tasks under one user request (NULL for root)
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,              -- which AgentDef to use
    task TEXT NOT NULL,                    -- the prompt/task description
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending → running → completed / failed / cancelled
    progress TEXT DEFAULT '{}',            -- JSON: {phase, step, steps_completed, message, stuck}
    result TEXT,                           -- JSON: SubagentResult
    error TEXT,
    instructions TEXT DEFAULT '[]',        -- JSON: [{added_at, message}] — mid-flight course-corrections
    config TEXT DEFAULT '{}',              -- JSON: AgentDef (frozen at task creation)
    cancel_requested INTEGER DEFAULT 0,    -- 1 = supervisor wants this cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_wq_user_status ON work_queue(user_id, status);
CREATE INDEX idx_wq_parent ON work_queue(parent_id);
```

**Why 11 columns instead of 21:** Every removed column has a clear reason:

| Cut column | Why |
|---|---|
| `priority` | No scheduler to prioritize. Tasks run immediately when invoked. |
| `depends_on` | Main agent orchestrates sequentially. No implicit DAG. |
| `inherit_results` | Main agent passes context in the task description. |
| `claimed_by` / `claimed_at` | Single-process. No worker pool claiming. |
| `llm_calls_used` / `cost_usd_used` | Tracked by `CostTracker` in `AgentLoop`. Written to `result` on completion. |
| `last_tool_call` | Doom loop stays in middleware memory. Write `stuck: true` to `progress` when detected. |
| `scheduled_at` | Use existing APScheduler for time-based triggers. Task state in work_queue. |
| `completed_at` | Derive from `updated_at` when `status = 'completed'`. |

### Subagent Middlewares (V1 — Two Middlewares)

Every subagent AgentLoop gets two middlewares that read/write the work_queue:

#### 1. `ProgressMiddleware` — updates progress and detects doom loops

```python
class ProgressMiddleware(Middleware):
    """Updates work_queue.progress after each tool call. Detects doom loops."""

    def __init__(self, task_id: str, db: WorkQueueDB):
        self.task_id = task_id
        self.db = db
        self._last_tool_calls: list[str] = []
        self._doom_threshold = 3

    async def after_tool_call(self, state, tool_name, tool_args, result, **kwargs):
        progress = json.loads(self.db.get_task(self.task_id)["progress"] or "{}")
        steps = progress.get("steps_completed", 0) + 1

        # Doom loop detection: same tool+args 3 times
        args_json = json.dumps(tool_args, sort_keys=True, ensure_ascii=True)
        call_hash = f"{tool_name}:{hashlib.md5(args_json.encode()).hexdigest()[:8]}"
        self._last_tool_calls.append(call_hash)
        if len(self._last_tool_calls) > self._doom_threshold:
            self._last_tool_calls = self._last_tool_calls[-self._doom_threshold:]

        is_stuck = (
            len(self._last_tool_calls) >= self._doom_threshold
            and len(set(self._last_tool_calls)) == 1
        )

        self.db.update_progress(self.task_id, {
            "steps_completed": steps,
            "phase": "executing",
            "message": f"Called {tool_name}",
            "stuck": is_stuck,
        })

        if is_stuck:
            self.db.add_instruction(self.task_id,
                "Doom loop detected: same tool called 3x with identical args. "
                "Consider cancelling or redirecting this task."
            )
```

#### 2. `InstructionMiddleware` — course-correction and cancel signals

```python
class InstructionMiddleware(Middleware):
    """Checks work_queue for instructions AND cancel signals before each LLM call."""

    def __init__(self, task_id: str, db: WorkQueueDB):
        self.task_id = task_id
        self.db = db
        self._last_checked = ""

    async def before_llm_call(self, state, **kwargs):
        row = self.db.get_task(self.task_id)

        # Cancel signal
        if row["cancel_requested"]:
            raise TaskCancelledError(f"Task {self.task_id} cancelled by supervisor.")

        # New instructions from main agent
        instructions = json.loads(row.get("instructions") or "[]")
        new = [i for i in instructions if i["added_at"] > self._last_checked]

        if new:
            self._last_checked = instructions[-1]["added_at"]
            for inst in new:
                state.messages.append(Message.system(
                    f"[Supervisor Update] {inst['message']}"
                ))
```

**Cost/limit enforcement** is handled by `AgentLoop`'s existing `CostTracker` + `RunConfig(max_llm_calls, cost_limit_usd)`. No separate middleware needed. Cost totals are written to the `result` column on task completion.

### Dynamic Subagent Creation, Reuse, and Amendment

The main agent can:

1. **Create a new subagent** — define from scratch
2. **Reuse an existing subagent** — invoke by name, same config
3. **Amend an existing subagent** — update name, model, tools, prompt, etc.

```python
# 1. Create from scratch
subagent_create(
    name="web_researcher",
    description="Researches information on the web",
    model="ollama:minimax-m2.5",
    tools=["search_web", "scrape_url", "map_url"],
    max_llm_calls=20,
    cost_limit_usd=0.50,
    system_prompt="You are a web researcher. Be concise."
)

# 2. Reuse existing — just invoke by name
subagent_start(
    agent_name="web_researcher",
    task="Find the top 5 AI companies by revenue in 2025"
)

# 3. Amend existing — update some fields, keep others
subagent_update(
    agent_name="web_researcher",
    model="anthropic:claude-sonnet-4-20250514",  # upgrade model
    max_llm_calls=30,                             # increase limit
    tools=["search_web", "scrape_url", "map_url", "files_read"],  # add a tool
    # system_prompt, description, etc. unchanged if not specified
)
```

Amendment persists to `data/users/{user_id}/subagents/{name}/config.yaml`. Only specified fields are updated; omitted fields retain their current values.

When a subagent is **invoked**, the `AgentDef` is **frozen** into the work_queue's `config` column. This means:
- If the user amends the subagent while a task is running, the running task uses the original config (no mid-flight config changes — use `subagent_instruct` instead).
- New invocations use the latest config.

### Supervision: Progress, Course-Correction, and Cancellation

The main agent has five supervision tools:

```python
# 1. Check progress on subtasks
subagent_tasks(parent_id=None) → [
    {"id": "task-1", "name": "researcher", "status": "running",
     "progress": {"phase": "executing", "steps_completed": 5, "stuck": False}},
    {"id": "task-2", "name": "writer", "status": "pending", "progress": {}},
]

# 2. Course-correct a running subagent
subagent_instruct(task_id, "Focus only on the top 3 companies. Skip deep financials.")
# → Injects a system message into the subagent's next iteration

# 3. Kill a stuck or misbehaving subagent
subagent_cancel(task_id)
# → Sets cancel_requested = 1
# → Subagent's InstructionMiddleware raises TaskCancelledError on next iteration
# → Main agent can then create a replacement task

# 4. Create/reuse a subagent
subagent_create(name, description, model, tools, ...)  # create new
subagent_update(name, model="new-model", tools=[...])    # amend existing

# 5. Delete a subagent
subagent_delete(name)  # removes config + cancels any running tasks
```

**Stuck detection** is built into `ProgressMiddleware`:
- Same tool called 3× with identical args → `progress.stuck = true` + auto-instruction
- Main agent checks `subagent_tasks()` and sees `stuck: true`
- Main agent decides: `subagent_instruct()` to redirect, or `subagent_cancel()` to kill

**Cancel flow:**
```
Main agent: "The researcher is stuck, kill it"
    │
    ▼
work_queue: SET cancel_requested = 1, updated_at = now() WHERE id = 'task-1'
    │
    ▼
Researcher AgentLoop (next iteration):
    InstructionMiddleware.before_llm_call() reads cancel_requested = 1
    │
    ▼
    raises TaskCancelledError
    │
    ▼
AgentLoop catches → work_queue: SET status = 'cancelled', result = {error: 'cancelled'}
    │
    ▼
Main agent: SELECT status FROM work_queue WHERE id = 'task-1'
    → status = 'cancelled'
```

### Multi-Subagent Orchestration (V1 — Simple Sequencing)

The main agent orchestrates sequentially. It delegates task-1, waits for completion, then decides what to do next based on the result.

```
User: "Research AI companies, write a report, email it to john@example.com"

Main Agent:
  1. Creates/uses subagent "researcher"
  2. subagent_start("researcher", "Research top 5 AI companies by revenue")
  3. [waits for completion via subagent_check]
  4. Gets result → "Here are the top 5 AI companies..."
  5. Creates/uses subagent "writer"
  6. subagent_start("writer", "Write a comparison report based on these findings: <result>")
  7. [waits for completion]
  8. Gets result → "Here is the comparison report..."
  9. Creates/uses subagent "emailer"
  10. subagent_start("emailer", "Email this report to john@example.com: <result>")
  11. [waits for completion]
  12. Composes final answer for user
```

**Parallel tasks** — the main agent can invoke multiple subagents and poll them:
```
Main Agent:
  1. task_a = subagent_start("researcher_a", "Research company A")
  2. task_b = subagent_start("researcher_b", "Research company B")  # runs in parallel
  3. [poll subagent_check for both]
  4. result_a = subagent_results(task_a)
  5. result_b = subagent_results(task_b)
  6. Combine and continue
```

**Context passing** — the main agent injects prior results into the next task's description. No `inherit_results` column or `depends_on` needed; the orchestrator does it explicitly.

### Context Isolation by Default

`delegate()` creates a fresh `AgentLoop` with:
- Own context window (only `[Message.user(task)]`)
- Own tool set (scoped by `AgentDef.tools` / `AgentDef.disallowed_tools`)
- Own `RunConfig` (with `max_llm_calls` and `cost_limit_usd`)
- Own `CostTracker`
- `ProgressMiddleware` + `InstructionMiddleware` (always)
- No `subagent_*` tools (prevents recursion)

### Persisted Definitions

`AgentDef` is saved to/loaded from `data/users/{user_id}/subagents/{name}/config.yaml`.

When a task is invoked, the config is **frozen** into `work_queue.config` — so running tasks aren't affected by config changes.

---

## V1 Tool List (7 Tools)

| Tool | Purpose |
|---|---|
| `subagent_create` | Create a new AgentDef, persist to disk |
| `subagent_update` | Amend an existing AgentDef (partial update, keeps unspecified fields) |
| `subagent_start` | Insert task into work_queue + start background execution |
| `subagent_list` | List user's AgentDefs + their running tasks |
| `subagent_check` | Check progress/status of one task |
| `subagent_tasks` | List progress/status of tasks |
| `subagent_instruct` | Course-correct a running subagent |
| `subagent_cancel` | Kill a running subagent |
| `subagent_delete` | Remove AgentDef + cancel any running tasks |

**Cut from V1:**
- `subagent_batch` — main agent orchestrates sequentially or via parallel invoke
- `subagent_validate` — AgentDef validation is built-in via Pydantic
- `subagent_schedule` / `subagent_schedule_cancel` / `subagent_schedule_list` — use existing APScheduler, wire later (Phase 2)
- `subagent_check` — checks one task (stuck detection visible via `progress.stuck`)

---

## V1 Implementation Plan

### Phase 1: Core Schemas + Work Queue (Foundation)

File changes:
- `src/sdk/work_queue.py` — `WorkQueueDB` class with all CRUD operations
- `src/sdk/subagent_models.py` — `AgentDef`, `SubagentResult`, `TaskCancelledError`
- `src/subagent/config.py` — Update `SubagentConfig` → `AgentDef` (expand fields)
- Migration: Create `work_queue` table in per-user SQLite

### Phase 2: Subagent Middlewares (Supervision)

File changes:
- `src/sdk/middleware_progress.py` — `ProgressMiddleware` (progress updates, doom loop detection)
- `src/sdk/middleware_instruction.py` — `InstructionMiddleware` (course-correction, cancel signal)

### Phase 3: SubagentCoordinator (Orchestration)

File changes:
- `src/sdk/coordinator.py` — `SubagentCoordinator` class:
  - `create(agent_def)` — register AgentDef, persist to disk
  - `update(name, **kwargs)` — amend existing AgentDef
  - `invoke(agent_name, task)` → task_id — insert work_queue row + run AgentLoop
  - `cancel(task_id)` — set cancel_requested
  - `instruct(task_id, message)` — add instruction
  - `check_progress(parent_id=None)` — query tasks
  - `get_result(task_id)` — get SubagentResult
  - `delete(name)` — remove AgentDef
- Wire `invoke()` to create `AgentLoop` with ProgressMiddleware + InstructionMiddleware + AgentDef config
- Replace `SubagentManager._invoke_async()` with `SubagentCoordinator.invoke()`

### Phase 4: Tool Migration

File changes:
- `src/sdk/tools_core/subagent.py` — Rewrite to use `WorkQueueDB` + `SubagentCoordinator`:
  - 8 tools as specified above
  - All tools interact with work_queue for task state
  - `subagent_start` creates AgentLoop with middlewares
  - `subagent_update` patches config YAML on disk

### Phase 5: Testing + Documentation

- Unit tests for `WorkQueueDB` (CRUD, cancel, stuck detection)
- Unit tests for `ProgressMiddleware` (progress updates, doom loop)
- Unit tests for `InstructionMiddleware` (course-correction, cancel)
- Integration tests for create/invoke/progress/cancel lifecycle
- Integration tests for subagent_update (amend existing)
- Integration tests for multi-subagent orchestration (sequential + parallel)
- Update AGENTS.md

---

## Phase 2 (Future)

| Feature | Rationale for deferral |
|---|---|
| `depends_on` / `inherit_results` columns | Main agent orchestrates sequentially in V1. DAG orchestration adds a worker pool. |
| Background watchdog | Main agent checks when active. Watchdog needs deployment consideration. |
| Webhook triggers (`webhook_triggers` table + FastAPI endpoint) | No user request yet. Requires auth, rate limiting. |
| Handoff mode (`loop.handoff()`) | Different UX — conversation transfer. Orthogonal to work_queue. |
| Model-driven routing (`delegate_to_{name}` auto-generation) | V1 uses explicit `subagent_create` + `subagent_start`. Auto-generation needs `sub_agents` on `AgentLoop`. |
| `AgentDef.middlewares` configurable | V1 uses fixed set (Progress + Instruction). Per-agent middleware selection adds config complexity. |
| `access_memory` / `access_messages` flags | V1: subagents inherit user context. Isolation flags need tool filtering. |
| `structured_output` in `SubagentResult` | V1: text output. Typed output requires schema per subagent. |
| `priority` column + worker pool | No scheduler to prioritize. Tasks run immediately. |
| Session resumption | Resume a completed subagent's work_queue row. Nice-to-have. |
| AgentDef versioning | Track config changes over time. |
| `delegate_to_{name}` auto-generation from `AgentLoop(sub_agents=[...])` | Model-driven routing. |

---

## Decision Records

### DR-001: SQLite Work Queue over Planning-with-Files for Coordination

**Context:** Planning-with-files uses `task_plan.md`, `progress.md`, `findings.md` for agent self-organization.

**Decision:** Use SQLite work_queue as the primary inter-agent coordination mechanism. Planning-with-files is an optional internal skill only — NOT for coordination.

**Rationale:**
- Files are not a coordination primitive: no atomic status transitions, no backpressure, race conditions on concurrent writes, no query capability.
- Work queue provides: atomic status transitions, structured progress, cancel signals, course-correction instructions, stuck detection, crash recovery — all via SQL.
- Subagents are short-lived (5-20 calls, fresh context). They don't have the context decay problem planning-with-files solves.

### DR-002: Work Queue over In-Memory Callbacks

**Context:** Earlier proposal used `SubagentEvent` callbacks for progress updates.

**Decision:** Use SQLite work_queue poll/query instead of in-memory callbacks.

**Rationale:**
- Callbacks don't survive process crashes. Work_queue rows do.
- Work_queue is observable from any tool, HTTP endpoint, or CLI.
- The middlewares already poll the DB on each iteration.

### DR-003: Per-User SQLite Database

**Context:** Could use a single shared database or per-user databases.

**Decision:** Use per-user SQLite at `data/users/{user_id}/subagents/work_queue.db`.

**Rationale:** Matches existing per-user isolation pattern. No cross-user data leakage. No contention between users. SQLite WAL mode handles concurrent reads within same user.

### DR-004: Dynamic Creation + Reuse + Amendment

**Context:** Subagents should be createable at runtime AND reusable AND amendable.

**Decision:** `subagent_create` creates new, `subagent_start` reuses by name, `subagent_update` amends existing. All persisted to `config.yaml`.

**Rationale:**
- The main agent often doesn't know in advance what subagents it needs. "Research X" requires different config than "Write about Y."
- Reuse avoids recreating identical subagents for multiple tasks.
- Amendment allows incremental improvement — "make the researcher more focused by narrowing its tools."
- Config is frozen into `work_queue.config` at invocation time, so running tasks aren't affected by amendments.

### DR-005: Subagents Cannot Create Other Subagents

**Decision:** `subagent_*` tools are in every AgentDef's `disallowed_tools` by default. No nesting.

**Rationale:** Simplifies mental model (one supervisor, N workers). Prevents unbounded cost. Prevents supervision confusion.

### DR-006: V1 is Delegation-Only (No Handoff)

**Decision:** V1 supports `delegate()` only. Handoff (permanent control transfer) is Phase 2.

**Rationale:** Handoff requires conversation-level changes (message history transfer, channel routing) orthogonal to work_queue. Delegation covers 95% of use cases.

### DR-007: Main Agent Orchestrates Sequentially (No DAG)

**Decision:** No `depends_on` or `inherit_results` in V1. The main agent orchestrates task sequencing explicitly, passing results in task descriptions.

**Rationale:** A DAG requires a worker pool that claims and runs tasks when dependencies resolve. V1 has no worker pool — the main agent invokes subagents directly. Sequential + parallel invoke covers the use cases; implicit dependency resolution is infrastructure we don't need yet.

### DR-008: Cost Tracking in AgentLoop, Not Separate Middleware

**Decision:** Use `AgentLoop`'s existing `CostTracker` + `RunConfig(max_llm_calls, cost_limit_usd)` for cost enforcement. Write totals to `work_queue.result` on completion.

**Rationale:** No need for a separate `CostLimitMiddleware` that writes to DB on every LLM call. The `CostTracker` already tracks per-loop costs. Writing on completion reduces DB writes.

### DR-009: `parent_id` and Correlation IDs

**Context:** `parent_id` groups tasks under one user request, but who sets it is unclear.

**Decision:** `parent_id` is populated by `SubagentCoordinator.invoke()` when the main agent provides a correlation ID (typically its own run/session ID). When the user directly invokes a subagent without context, `parent_id` is `NULL`.

**Rationale:** Allows the main agent to query `SELECT * FROM work_queue WHERE parent_id = ?` to see all tasks it spawned for a given user request. Without this, the main agent would need to track task IDs itself. The correlation ID is the main agent's current run ID, set automatically by `SubagentCoordinator`.

### DR-010: Subagent Timeout

**Context:** No timeout on `AgentLoop.run()`. If a provider hangs or a subagent loops, the task stays `running` forever.

**Decision:** Add `timeout_seconds: int = 300` to `AgentDef`. `SubagentCoordinator.invoke()` wraps the `AgentLoop.run()` call in `asyncio.wait_for(timeout=agent_def.timeout_seconds)`. On timeout, sets `status = 'failed'` with `error = 'timeout'`.

**Rationale:** `max_llm_calls` limits iterations but not wall-clock time. A single provider call can hang indefinitely. `timeout_seconds` is a hard safety boundary.

### DR-011: Provider Risk — System Messages Mid-Conversation

**Context:** `InstructionMiddleware` injects system messages mid-conversation. Gemini only supports a single `system_instruction` param; later system messages may be dropped or converted to user messages by the `to_gemini()` adapter.

**Decision:** Accept this as a known provider limitation for V1. In `to_gemini()` conversion, system messages after the first should be converted to user messages with `[Supervisor Update]` prefix.

**Rationale:** OpenAI, Anthropic, and Ollama all support multiple system messages. Gemini is the only provider that doesn't. The degradation (supervisor message becomes user message) is acceptable — the subagent still sees it.

---

## Subagent Failure Modes

Every subagent invocation must result in a terminal `work_queue` status. `SubagentCoordinator.invoke()` wraps the entire `AgentLoop.run()` call in a try/except that guarantees this:

| Failure type | Terminal status | `error` value |
|---|---|---|
| Normal completion | `completed` | `None` |
| `TaskCancelledError` (supervisor cancel) | `cancelled` | `"cancelled by supervisor"` |
| `MaxCallsExceededError` (CostTracker limit) | `failed` | `"exceeded max_llm_calls"` |
| `CostLimitExceededError` (CostTracker limit) | `failed` | `"exceeded cost_limit_usd"` |
| `asyncio.TimeoutError` (wall-clock timeout) | `failed` | `"timeout after {timeout_seconds}s"` |
| `GuardrailTripwire` (input/output guardrail) | `failed` | `"guardrail: {tripwire.reason}"` |
| Provider exception (API error, rate limit) | `failed` | `"{exception_type}: {message}"` |
| Tool exception (unhandled in loop) | `failed` | `"{exception_type}: {message}"` |
| Any unexpected exception | `failed` | `"{exception_type}: {message}"` |

Implementation pattern:
```python
async def invoke(self, agent_name: str, task: str, parent_id: str | None = None) -> str:
    task_id = self.db.insert_task(agent_name, task, config, parent_id)
    try:
        result = await asyncio.wait_for(
            loop.run(messages),
            timeout=agent_def.timeout_seconds,
        )
        self.db.set_completed(task_id, result)
    except TaskCancelledError:
        self.db.set_cancelled(task_id)
    except asyncio.TimeoutError:
        self.db.set_failed(task_id, f"timeout after {agent_def.timeout_seconds}s")
    except Exception as e:
        self.db.set_failed(task_id, f"{type(e).__name__}: {e}")
    return task_id
```

**Important:** `SubagentCoordinator.invoke()` uses `AgentLoop.run()`, not `run_stream()`. There is no consumer reading a stream for subagent tasks. Progress is reported via `ProgressMiddleware` writing to `work_queue.progress`, which the main agent polls.

---

## WorkQueueDB Async Contract

`ProgressMiddleware` and `InstructionMiddleware` are async middlewares that run inside the `AgentLoop`. They call `WorkQueueDB` methods. `WorkQueueDB` must use `aiosqlite` (matching existing storage in `src/storage/`), not synchronous `sqlite3`, to avoid blocking the event loop.

All `WorkQueueDB` write operations (`update_progress`, `add_instruction`, `set_completed`, `set_failed`, `set_cancelled`) are async. All read operations (`get_task`, `check_progress`) are async.

---

## Large Result Handling

Subagent output stored in `work_queue.result` (JSON TEXT) is fine for SQLite. But when the main agent reads results back and injects them into its own conversation, large outputs consume tokens rapidly.

For V1: `SubagentResult.output` includes a `truncated: bool` field. If the output exceeds a configurable threshold (e.g., 10000 characters), it is truncated and `truncated = True`. The main agent sees the truncated output and can decide to re-invoke with a more specific task if it needs detail.

This threshold is configurable in `AgentDef` or `SubagentCoordinator` defaults.

---

## 8. Perplexity Computer — Multi-Model Orchestration

**Architecture:** Claude Opus 4.6 as conductor, routes subtasks to 19+ specialized models (Gemini for deep research, GPT-5.2 for long-context, Grok for speed, Nano Banana for images, Veo 3.1 for video). Sub-agents can spawn additional sub-agents for unexpected problems. Communication via filesystem (inspectable). Isolation via Firecracker microVMs (2 vCPU, 8GB RAM, <125ms boot). "Model Council" runs 3 models on same question in parallel, synthesizes agreement/differences.

**Key patterns:**
1. **Task-type model routing** — orchestrator selects model based on task characteristics, not a fixed assignment
2. **Sub-agents spawn sub-agents** — recursive agent creation for unexpected sub-problems
3. **Filesystem as communication channel** — inspectable, durable coordination between agents
4. **Skills = reusable workflow templates** — instruction sets that auto-activate by query type, chainable
5. **Credit-based cost control** — every action costs credits, monthly spending cap enforced

**What we learn from Perplexity:**

| Perplexity Pattern | Applicable to us? | How |
|---|---|---|
| Task-type model routing | ✅ Phase 2 | `AgentDef.model` already supports per-subagent model. Could add automatic routing: research → cheap/fast model, creative → powerful model |
| Sub-agents spawn sub-agents | ❌ V1, 🔲 Phase 3 | DR-005 forbids. Perplexity's filesystem communication validates our work_queue approach — both make interactions inspectable, but SQLite is strictly better (structured queries, atomic, no parsing) |
| MicroVM isolation | ❌ Not needed | Single-process, per-user. Process isolation sufficient. MicroVMs are a cloud/multi-tenant requirement |
| Model Council (ensemble) | 🔲 Phase 3 | Run 3 cheap models on same question, synthesize. Our work_queue naturally supports this: 3 parallel `subagent_start` calls, main agent synthesizes |
| Skills as reusable templates | ✅ Already exists | Our skill system already does this. `AgentDef.skills` injects skills into system prompt |
| Credit-based costing | ✅ Partial | We track `cost_usd` per task in `SubagentResult`. Could add a **credit budget** to `AgentDef` (pre-spending cap) in Phase 2 |
| Filesystem as communication | ✅ Validated our decision | Perplexity chose filesystem because it's inspectable. Our work_queue is the same principle but structurally superior: SQL queries vs file parsing, atomic transactions vs file writes, structured JSON vs markdown |

**Key insight:** Perplexity's filesystem-as-communication is what you get when you don't have a proper coordination layer. We're building the proper coordination layer (SQLite work_queue). They chose filesystem for inspectability; we chose SQLite for inspectability + structure + atomicity.

---

## Open Questions

1. **Streaming from subagents**: V1 uses polling (`subagent_check`). SSE streaming would require a callback/event mechanism — deferred to Phase 2.

2. **Subagent chaining depth**: V1 disallows nesting entirely. Phase 2 could add `AgentDef.worker_type` with depth tracking.

3. **Shared state scope**: V1 — subagents share `user_id`, access same memory/message DB. Phase 2 could add `access_memory` / `access_messages` flags for isolation.

4. **Subagent as MCP tool**: Phase 3. External system → webhook → INSERT INTO work_queue is straightforward once we have webhooks.

5. **Subagent versioning**: Phase 3. Could add `version: int` to `AgentDef` and track config changes.

6. **Session resumption**: Phase 3. Could add `resume_from` to reuse a completed subagent's context.

7. **Webhook authentication**: Phase 3. HMAC with shared secret in `webhook_triggers` table.

---

## External Review — Concerns & Recommendations

> Reviewed by agent on 2026-04-22. Overall verdict: **Approve with minor adjustments.**

### Positive Assessment

- **Scoping Discipline**: Excellent. V1 is delegation-only, no DAG, no background worker pool, no streaming. Complex features are cleanly deferred to Phase 2.
- **Context Isolation**: Correctly identified as the key innovation. Fresh `AgentLoop` with `[Message.user(task)]` prevents parent context pollution.
- **Work Queue over Files/Callbacks**: DR-001 and DR-002 are bulletproof. SQLite provides atomic transitions, crash recovery, and observability — files and in-memory callbacks do not.
- **Cost Enforcement**: Reusing `AgentLoop`'s existing `CostTracker` + `RunConfig` is elegant. No new middleware needed.
- **Freeze-at-Invocation**: Config frozen into `work_queue.config` means amendments don't affect running tasks. This is the right semantics.
- **`disallowed_tools` for Recursion**: `subagent_*` tools in denylist by default simplifies the mental model and prevents runaway cost.

### Identified Concerns — All Addressed

| # | Concern | Resolution |
|---|---|---|
| 1 | Exception handling — `TaskCancelledError` bubbles from `run()` | **Fixed**: `SubagentCoordinator.invoke()` wraps entire `run()` in try/except. See "Subagent Failure Modes" section and implementation pattern. |
| 2 | `parent_id` semantics underspecified | **Fixed**: Added DR-009. `parent_id` = main agent's run ID when orchestrating, NULL when user invokes directly. |
| 3 | `str(tool_args)` hash is brittle | **Fixed**: Changed to `json.dumps(tool_args, sort_keys=True, ensure_ascii=True)` in ProgressMiddleware. |
| 4 | System messages mid-conversation for Gemini | **Fixed**: Added DR-011. `to_gemini()` converts later system messages to user messages with `[Supervisor Update]` prefix. |
| 5 | No subagent-level timeout | **Fixed**: Added `timeout_seconds: int = 300` to `AgentDef`. DR-010. Wrapped in `asyncio.wait_for()`. |
| 6 | Large result handling | **Fixed**: Added `truncated: bool` to `SubagentResult`. See "Large Result Handling" section. |
| 7 | "Parallel agents" misleading in table | **Fixed**: Changed to "multiple concurrent invokes" to clarify it's not a worker pool. |
| 8 | `run()` vs `run_stream()` unclear | **Fixed**: Explicitly stated in "Subagent Failure Modes" section that `invoke()` uses `AgentLoop.run()`, not `run_stream()`. |

### Suggested Additions — All Addressed

| # | Suggestion | Resolution |
|---|---|---|
| 1 | Add DR-009 for `parent_id` | **Done** — DR-009 added. |
| 2 | Add "Subagent Failure Modes" section | **Done** — Full table + implementation pattern added. |
| 3 | Specify `WorkQueueDB` async contract | **Done** — See "WorkQueueDB Async Contract" section. Uses `aiosqlite`. |
| 4 | Fix hash | **Done** — `json.dumps(sort_keys=True)` in ProgressMiddleware. |
| 5 | Add timeout | **Done** — `timeout_seconds` in `AgentDef`, `asyncio.wait_for()` in `invoke()`. |
