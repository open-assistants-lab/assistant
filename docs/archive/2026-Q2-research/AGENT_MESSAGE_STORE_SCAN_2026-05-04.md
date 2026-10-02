# Agent–MessageStore Interaction — Scan Findings

**Date:** 2026-05-04
**Files:** `src/http/routers/conversation.py` (369 lines), `src/sdk/runner.py:259-285`, `src/cli/`

---

## The Data Flow

```
User message
    │
    ▼
HTTP router (conversation.py)
    │
    ├── conversation.add_message("user", ...)     ← persist to MessageStore
    ├── conversation.get_messages_with_summary(50) ← load history
    ├── _messages_from_conversation(...)           ← convert to SDK Messages
    ├── run_sdk_agent(messages)                    ← execute agent loop
    │       │
    │       └── AgentState (in-memory only)
    │
    └── conversation.add_message("assistant", ...) ← persist result
        conversation.add_message("tool", ...)       ← persist tool calls (not always!)
```

## Issue 1: Tool messages silently dropped on conversation reload (MAJOR)

**File:** `src/sdk/runner.py:276-277`

```python
def _messages_from_conversation(messages):
    ...
    elif role == "tool":
        continue  # "Tool metadata is for audit/display, not LLM context"
```

Every tool message stored in the conversation is discarded when converting to SDK format
for a new session. The agent has zero context about past tool calls — what tools were called,
what arguments were used, what results were returned.

### Example scenario

```
Session 1:
  User: "List my unread emails"
  Agent: calls email_list → returns 5 emails
  Storage: user message ✓, assistant response ✓, tool result ✓

Session 2:
  User: "What were those emails you found last time?"
  Agent: reloads conversation → tool messages dropped at line 277
  Agent: has no idea it ever called email_list, has no results
```

### Impact

- Agent cannot reference or build on past tool executions
- Follow-up questions about previous tool results fail
- Summarization (which drops old messages for compact context) compounds the loss
- Only the current session's in-memory `AgentState` tool calls survive

### Fix options

**Option A** (simple): Convert tool messages to system messages with truncated content:
```python
elif role == "tool":
    meta = getattr(m, "metadata", {}) or {}
    tool_name = meta.get("tool_name", "unknown")
    sdk_messages.append(Message.system(f"[Tool: {tool_name}] {content[:500]}"))
```

**Option B** (full): Treat tool messages as first-class context, cap at N most recent.

---

## Issue 2: Verbose/streaming mode never persists tool messages

**File:** `src/http/routers/conversation.py`

Three code paths exist for the `/message` endpoint:

| Path | Trigger | Persists tool messages? |
|---|---|---|
| Non-verbose | `not req.verbose`, line 157 | Yes — line 165: `conversation.add_message("tool", ...)` |
| Verbose | `req.verbose`, line 85 | **No** — tool events captured in `tool_events` list but never persisted |
| SSE stream | `/message/stream`, line 231 | **Partial** — line 290: `add_message("tool", "", metadata)` with empty content |

### Verbose path (lines 84-155)

```python
if req.verbose:
    async for chunk in run_sdk_agent_stream(...):
        if ... tool_start/tool_end/tool_result:
            tool_events.append({...})   # captured for verbose_data only
    ...
    conversation.add_message("assistant", response, metadata=assistant_metadata)
    # No conversation.add_message("tool", ...) anywhere
```

The `tool_events` list feeds into `verbose_data` → `assistant_metadata` on the *assistant*
message (line 208). The tool data lives as nested metadata on the assistant, not as separate
tool messages. On reload, this metadata is available but `_messages_from_conversation` drops
it anyway (Issue 1).

### SSE stream path (line 290)

```python
conversation.add_message("tool", "", metadata=tm)
```

Content is empty string. Only the tool name survives in metadata. The actual tool output
is captured in `tool_results` but appended to the assistant response string, not stored
as a tool message.

### Fix

Add `conversation.add_message("tool", tool_output, metadata={"tool_name": name})` in
both the verbose and SSE stream paths, matching the non-verbose behavior.

---

## Issue 3: CLI path never persists messages

**File:** `src/cli/`

Searching `src/cli/` for `get_message_store` or `add_message` returns zero results.
The CLI calls `AgentLoop.run()` / `run_stream()` directly. Messages live in `AgentState`
(in-memory) and are lost on exit.

### Impact

- CLI conversations leave no history
- `get_messages_with_summary()` returns nothing from CLI-only users
- Memory extraction (`/conversation/extract-memories`) has no source data from CLI sessions
- The `middleware_memory.py` extraction pipeline depends on persisted messages

### Fix

Add message persistence to the CLI loop. After each user input and each agent response,
call the message store. The CLI already has a `user_id` — the store is a single import away:

```python
from src.storage.messages import get_message_store
store = get_message_store(user_id, workspace_id)
store.add_message("user", user_input)
# ... run agent ...
store.add_message("assistant", response)
```

---

## Issue 4: Dead `role == "reasoning"` handler

**File:** `src/sdk/runner.py:278-279`

```python
elif role == "reasoning":
    pending_reasoning = content or None
```

The `MessageStore.Message` dataclass has no constraint on `role`, but no code path
ever calls `conversation.add_message("reasoning", ...)`. This handler exists for a
future or removed feature. Dead code.

---

## Issue 5: `run_sdk_agent` potentially called twice for verbose fallback

**File:** `src/http/routers/conversation.py:136-154`

```python
if not response:
    if not tool_events:
        result_messages = await run_sdk_agent(
            user_id=user_id, messages=sdk_messages, ...
        )
```

When streaming produces neither AI content nor tool events, it falls back to a
second `run_sdk_agent` call with the same input. The first call (`run_sdk_agent_stream`)
already executed tool calls. The second call runs the agent AGAIN with the tool results
already in state — producing a duplicate execution of the full agent loop.

### Impact

Double LLM cost for streams that produce no visible output. The first execution's
tool calls already ran and their results are in state. The second execution re-processes
from the same state.

---

## Summary

| # | Type | Severity | Impact |
|---|---|---|---|
| 1 | Bug | **HIGH** | Tool context lost across sessions |
| 2 | Bug | MEDIUM | Verbose/SSE don't persist tool messages |
| 3 | Gap | MEDIUM | CLI has no message persistence |
| 4 | Dead code | LOW | Unused `reasoning` role handler |
| 5 | Bug | LOW | Double LLM call in verbose fallback |

---

## 2026-05-07 Re-evaluation Verdicts

| # | Verdict | Action |
|---|---|---|
| 1 | Agreed | `_messages_from_conversation()` now preserves stored tool messages as bounded system context (`[Tool: name] result`). |
| 2 | Agreed | Verbose and SSE paths now persist tool result content as standalone `tool` messages with `tool_name` and `tool_call_id` metadata. |
| 3 | Obsolete | No CLI command/path exists in the current app entrypoint; `ea` only starts HTTP. No CLI persistence fix applied. |
| 4 | No change | The `reasoning` role handler is harmless and may support imported/future histories; removing it has no functional benefit. |
| 5 | Agreed | Verbose streaming no longer falls back to a second `run_sdk_agent()` call when the stream yields no visible output. |

Regression coverage was added in `tests/api/test_agent_message_store.py`.
