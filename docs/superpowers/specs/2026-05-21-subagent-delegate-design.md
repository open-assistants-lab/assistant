# Subagent Delegate — Synchronous Inline Delegation

Date: 2026-05-21

## Problem

The main agent cannot delegate work to a subagent and get the result back in the same turn. The only path is `subagent_start` → fire-and-forget → poll `subagent_check` in a later turn. This multi-turn chain is unreliable — most LLMs forget to poll or poll incorrectly.

Meanwhile, the synchronous path (`coordinator.invoke()`) exists but is dead code — no tool calls it and it skips agent-def validation.

## Design Goals

1. Allow the main agent to invoke a subagent and get the result inline in the same turn
2. Support parallel delegation — multiple `subagent_delegate` calls in one turn run concurrently
3. Preserve existing `subagent_start` for long-running async use cases unchanged
4. Use existing infrastructure — coordinator, agent loop, middleware stack — with minimal new code

## Design

### 1. New Coordinator Method: `delegate()`

Add to `SubagentCoordinator` in `src/sdk/coordinator.py`:

```python
async def delegate(
    self,
    agent_name: str,
    task: str,
    parent_id: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Run a subagent synchronously and return the result string.

    Like invoke() but with agent-def validation and full middleware stack.
    Unlike start(), this blocks until the subagent completes.
    Unlike invoke(), this validates the agent def first.
    No claim_task or heartbeat needed — runs in-process.

    The effective timeout is min(timeout_seconds, agent_def.timeout_seconds).
    """
    agent_def = self.load_def(agent_name)
    if agent_def is None:
        raise ValueError(
            f"Subagent '{agent_name}' not found. "
            f"Create it first with subagent_create."
        )

    errors = validate_agent_def(agent_def, user_id=self.user_id, workspace_id=self.workspace_id)
    if errors:
        raise ValueError("Invalid subagent definition: " + "; ".join(errors))

    effective_timeout = min(
        timeout_seconds or agent_def.timeout_seconds,
        agent_def.timeout_seconds,
    )

    db = await self._get_db()
    task_id = await db.insert_task(agent_name, task, agent_def, parent_id)

    try:
        result: SubagentResult = await asyncio.wait_for(
            self._run_loop(task_id, agent_def, task, db),
            timeout=effective_timeout,
        )
        completed = await db.set_completed(task_id, result)
        if not completed:
            await self._set_cancelled_if_requested(task_id, db)
        return result.output
    except TaskCancelledError:
        await db.set_cancelled(task_id)
        return "Cancelled: subagent was cancelled during execution."
    except TimeoutError:
        failed = await db.set_failed(task_id, f"timeout after {effective_timeout}s")
        if not failed:
            await self._set_cancelled_if_requested(task_id, db)
        return f"Timeout: subagent did not complete within {effective_timeout}s."
    except Exception as e:
        failed = await db.set_failed(task_id, f"{type(e).__name__}: {e}")
        if not failed:
            await self._set_cancelled_if_requested(task_id, db)
        return f"Error: {type(e).__name__}: {e}"
```

**Key differences from `invoke()` (dead code at line 257):**
- Calls `validate_agent_def()` before running
- Uses `_run_loop` (same as `_run_job`), so full middleware stack applies
- No `claim_task()` — runs in-process, no heartbeat needed
- Uses `_set_cancelled_if_requested()` fallback for race conditions (unlike original design — `InstructionMiddleware` can raise `TaskCancelledError` mid-flight, so cancellation IS possible)
- Returns `result.output` string instead of raw task_id

**Key differences from `start()` (line 292):**
- Blocks until completion instead of returning immediately
- No background task or heartbeat
- Writes terminal status to work_queue for audit trail but caller already has the result

### 2. New Tool: `subagent_delegate`

Add to `src/sdk/tools_core/subagent.py`:

```python
@tool
async def subagent_delegate(
    agent_name: str,
    task: str,
    user_id: str,
    workspace_id: str = "personal",
    parent_id: str | None = None,
    timeout_seconds: int = 120,
) -> str:
    """Run a subagent and wait for the result. Returns the subagent's output.

    Unlike subagent_start which fires and forgets, this tool blocks until
    the subagent completes and returns the result inline. Use this when
    you need the subagent's output to continue your work.

    Multiple subagent_delegate calls in the same turn run in parallel.

    For long-running tasks (>2 min), use subagent_start instead so the
    user can continue chatting while the subagent works.

    Args:
        agent_name: Name of the subagent to run
        task: Task description/prompt for the subagent
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        parent_id: Correlation ID to group related tasks
        timeout_seconds: Maximum seconds to wait (default 120, max 600)

    Returns:
        The subagent's output text
    """
    coordinator = get_coordinator(user_id, workspace_id)

    existing = coordinator.load_def(agent_name)
    if existing is None:
        return f"Error: Subagent '{agent_name}' not found. Create it first with subagent_create."

    try:
        result = await coordinator.delegate(
            agent_name, task, parent_id=parent_id,
            timeout_seconds=timeout_seconds,
        )
        return result
    except Exception as e:
        return f"Error running '{agent_name}': {type(e).__name__}: {e}"
```

**Annotations:**

```python
subagent_delegate.annotations = ToolAnnotations(
    title="Run Subagent (wait for result)",
    read_only=True,
    idempotent=True,
    open_world=True,
)
```

**Why `read_only=True, idempotent=True`:**
- The tool itself doesn't modify state — it delegates work and returns the result
- The subagent's own tool calls may be destructive, but those are the subagent's responsibility
- This classification makes it parallel-safe, so multiple delegates run concurrently via `asyncio.gather()`

### 3. Registration

Add to the tool definitions in `subagent.py` alongside existing tools. It's exported through the same `get_tool_definitions()` list used by `native_tools.py`.

### 4. Async Tool Support

`subagent_delegate` is a native async function (no `_run_async` bridge). The agent loop at `loop.py:244` already branches on `tool_def._coroutine`:

```python
if tool_def._coroutine:
    result = await tool_def.ainvoke(tc.arguments)
else:
    result = tool_def.invoke(tc.arguments)
```

No changes needed to the agent loop.

> Superseded (2026-09-19): `_coroutine` no longer exists, and the loop routes
> every tool through `ainvoke` — `ToolDefinition.ainvoke` decides dispatch from
> the current callable instead of a construction-time flag (see `85bf4a8e`).
> The snippet above is kept only as a historical record of the design at the
> time.

### 5. Timeout Handling

The tool has a `timeout_seconds` parameter defaulting to 120s. The effective timeout is `min(timeout_seconds, agent_def.timeout_seconds)` — the tool can request a shorter timeout than the agent's definition allows, but cannot exceed it. This prevents the LLM from overriding the agent owner's configured limit.

`coordinator.delegate()` enforces the effective timeout via `asyncio.wait_for()`. If the subagent times out, the tool returns a descriptive error string rather than crashing the main agent's turn.

If the main agent's LLM turn has a shorter remaining timeout, the LLM should prefer `subagent_start` for long tasks. The tool description hints at this: "For long-running tasks (>2 min), use subagent_start instead."

### 6. Parallel Execution Behavior

When the LLM issues multiple `subagent_delegate` calls in one turn:

```
subagent_delegate(agent_name="scraper_a", task="scrape site A")
subagent_delegate(agent_name="scraper_b", task="scrape site B")
subagent_delegate(agent_name="scraper_c", task="scrape site C")
```

The agent loop's `_classify_tool_calls()` classifies all as parallel-safe (read-only). `_execute_tool_batch()` runs them concurrently via `asyncio.gather()`. All three results are returned together, and the LLM can reference all three in its response.

If one subagent fails (timeout, error, invalid agent def), the others still complete. The error is in that tool's result, and `_execute_tool_batch()` handles exceptions via `return_exceptions=True`.

## State Changes

### `SubagentCoordinator`
- Add `delegate()` method as described above
- `invoke()` remains untouched (still dead code, mark as deprecated)

### `SubagentPanelState` (Flutter)
- No changes — the panel uses REST API, not subagent tools

### Work Queue
- `delegate()` writes to the same work_queue tables for audit trail
- Jobs started via `delegate()` are visible in `GET /subagents/jobs` as completed (or failed)
- This means duplicate execution: `delegate()` writes, completes, and the record stays

## File Changes

| File | Change |
|------|--------|
| `src/sdk/coordinator.py` | Add `delegate()` method after `invoke()`; add `# TODO: deprecate — use delegate() instead` to `invoke()` |
| `src/sdk/tools_core/subagent.py` | Add `subagent_delegate` tool function + annotations |
| `src/sdk/native_tools.py` | Import and register `subagent_delegate` |

## Non-Goals

- No REST endpoint changes (panel uses existing async paths)
- No Flutter changes
- No work_queue schema changes
- No `claim_task` or heartbeat for synchronous path
- No changes to existing `subagent_start` behavior
- `invoke()` is left as-is with a deprecation comment

## Future Considerations

- If `subagent_delegate` sees heavy use, consider adding a result cache so identical (agent, task) pairs within a session skip re-execution
- The 120s default timeout could be configurable per-agent-def in the future
- If users frequently fall back from delegate to start due to timeouts, a hybrid pattern could auto-promote: try delegate with timeout, if it hits timeout auto-convert to background start

## Acceptance Criteria

- `subagent_delegate` returns the subagent's output for a valid agent+task
- `subagent_delegate` returns a descriptive error for a nonexistent agent
- `subagent_delegate` returns a timeout error when the subagent exceeds the timeout
- Multiple `subagent_delegate` calls in one LLM turn run concurrently (not sequentially)
- The result of a delegate'd subagent is visible in `GET /subagents/jobs` as completed
- Existing `subagent_start` behavior is unchanged
- All existing subagent tests pass
- A regression test verifies parallel execution of two delegates
