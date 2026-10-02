# Agent Skills Compliance & Async Subagents — Analysis

## Part 1: Agent Skills Spec Compliance

Source: [agentskills.io/specification](https://agentskills.io/specification)

### Compliance Matrix

| Spec requirement | Assistant's implementation | Status |
|-----------------|---------------------|--------|
| `SKILL.md` with YAML frontmatter | `parse_skill_file()` in `src/skills/models.py:37` | ✅ |
| `name` (1-64 chars, lowercase+hyphens) | `_is_valid_skill_name()` line 104-116 | ✅ |
| `description` (max 1024 chars) | Required, no length validation | ⚠️ |
| `license` (optional) | Parsed at line 90 | ✅ |
| `compatibility` (optional, max 500 chars) | Parsed at line 92, no length validation | ⚠️ |
| `metadata` (optional, dict[str, str]) | Parsed at line 94 | ✅ |
| `allowed-tools` (optional, hyphenated) | Reads `allowed_tools` (underscore) at line 97 | ❌ Bug |
| Directory name matches `name` field | Not enforced at parse time | ⚠️ |
| `scripts/`, `references/`, `assets/` dirs | Not validated, but not blocked | ⚠️ |
| Progressive disclosure | SkillMiddleware loads SKILL.md on activation | ✅ |

### Bug: `allowed-tools` Hyphen Not Recognized

The agent skills spec uses `allowed-tools` (hyphenated) in YAML frontmatter. Assistant's `parse_skill_file()` reads `metadata.get("allowed_tools")` at line 97 — with an underscore. YAML preserves hyphens, so `allowed-tools` in the file becomes key `allowed-tools` in the parsed dict, not `allowed_tools`. Assistant's lookup always returns None.

**Fix:** Change line 97 from `metadata.get("allowed_tools")` to `metadata.get("allowed-tools")`.

### Recommended Additions

| # | What | Why |
|---|------|-----|
| 1 | Validate `description` length ≤ 1024 chars in `parse_skill_file()` | Spec constraint; currently unbounded |
| 2 | Validate `compatibility` length ≤ 500 chars | Spec constraint; currently unbounded |
| 3 | Enforce directory name = `name` field at parse time | Spec constraint; currently checked via `item.name != name` in `storage.py:28` (skips skill, doesn't warn) |
| 4 | Add `allowed-tools` to `SkillMetadata` TypedDict | Frontmatter field defined in spec but missing from type |

---

## Part 2: Async Subagents — What to Adopt

Source: [Deep Agents async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents)

### Current State: Assistant's SubagentCoordinator

| Aspect | EA (current) | Deep Agents (async) |
|--------|-------------|---------------------|
| **Invocation** | legacy start blocked parent until done | `start_async_task` returns task ID immediately |
| **Parent behavior** | Waits (or times out) | Continues interacting with user |
| **Mid-task steering** | `InstructionMiddleware` (next LLM call) | `update_async_task` sends new instructions anytime |
| **Cancellation** | `cancel_requested` flag polled before LLM calls | `cancel_async_task` cancels server-side run |
| **Status check** | `subagent_check` queries work_queue table | `check_async_task` queries Agent Protocol server |
| **State channel** | Task IDs in tool messages → lost on summarization | Dedicated `async_tasks` state channel survives compaction |
| **Execution target** | In-process `AgentLoop.run()` in same Python process | Separate Agent Protocol server (ASGI or HTTP) |
| **Concurrency** | Sequential (single start at a time) | Parallel — launch multiple, check later |
| **Horizonal scaling** | None (in-process) | HTTP transport to remote servers |

### What EA Should Adopt

### Priority 1: Dedicated Task State Channel

Assistant's task state lives in `work_queue.db` (SQLite per-user) AND in tool messages in conversation history. When `SummarizationMiddleware` compacts the history, task IDs in old messages are lost — the parent can't reference or check tasks it launched earlier in a long session.

Deep Agents stores task metadata in a dedicated `async_tasks` state channel on the supervisor's graph, separate from messages. This survives any number of summarization rounds.

**How to adopt:** Add an `_active_tasks` dict to `ChatState` (or the agent's state) that maps `task_id → {agent_name, thread_id, status, created_at}`. The `SubagentCoordinator` writes to it on launch. The `SummarizationMiddleware` skips it during compaction. The `list_tasks` tool reads from it. This is a data-model change, not a protocol change.

**Lines:** ~80. **Effort:** ½ day.

### Priority 2: Non-Blocking Invoke

Assistant's legacy start path blocked — the parent agent stopped talking to the user while the subagent ran. For tasks that take 30+ seconds (research, coding, data analysis), this creates a dead-air experience.

Deep Agents' `start_async_task` returns a task ID immediately. The parent can say "I've started researching that — it'll take a few minutes. Anything else while we wait?" and continue the conversation.

**How to adopt:** Add `subagent_start(task)` to `SubagentCoordinator`. Wraps `AgentLoop.run()` in `asyncio.create_task()`, saves the Future to the task state channel, returns the task ID immediately. Add `subagent_check(task_id)` to poll the Future.

**Lines:** ~120. **Effort:** 1 day.

### Priority 3: Five-Tool Interface

Deep Agents provides a clean five-tool interface that EA should mirror:

| Deep Agents tool | EA equivalent | Status |
|-----------------|---------------|--------|
| `start_async_task` | `subagent_start` | To build |
| `check_async_task` | `subagent_check` | Rename + enhance |
| `update_async_task` | `subagent_instruct` | Exists ✅ |
| `cancel_async_task` | `subagent_cancel` | Exists ✅ |
| `list_async_tasks` | `subagent_list` (lists agent defs + tasks) | Exists ✅ |

The rename is cosmetic but important: "progress" implies passive observation; "check" implies active polling. The semantic shift from sync-blocking to async-polling should be reflected in the tool names.

### Priority 4: Polling Guardrails (P3)

Deep Agents' middleware injects system prompt rules to prevent the supervisor from polling in a tight loop after launch ("Never call check_async_task immediately after launch"). Assistant's InstructionMiddleware already does course-correction injection — the same mechanism can inject polling guardrails.

**How to adopt:** Add to the subagent system prompt:
```
After launching a subagent, ALWAYS return control to the user.
Never call check_task immediately after invoke_async.
Task statuses in conversation history are always stale — always call check_task or list_tasks before reporting status.
```

**Lines:** ~5. **Effort:** Minutes.

### Implementation Summary

| Priority | Feature | Lines | Days | Impact |
|----------|---------|-------|------|--------|
| **P1** | Dedicated task state channel | ~80 | 0.5 | Survives summarization, enables long sessions |
| **P2** | Non-blocking `subagent_start` | ~120 | 1 | Eliminates dead air during long subagent runs |
| **P2** | Add `subagent_check` | ~20 | 0.2 | Cleaner async semantics |
| **P3** | Polling guardrails in system prompt | ~5 | — | Prevents tight polling loops |
| **P3** | Agent Protocol transport | ~300 | 2 | Remote subagents, independent scaling (Phase 2) |
