# Separate Bubbles + Streaming Indicators

**Date:** 2026-07-17
**Status:** Design (approved, pre-implementation)

## Problem

The native app renders an agent turn as a single assistant bubble. Reasoning, tool calls, tool results, and the final answer all get concatenated into one card. This makes it impossible to see the agent's step-by-step process, and when a tool fails the user sees a blank response or a wall of concatenated text.

The sidebar has no indicator for which chats are actively streaming or have unread responses.

## Goal

1. Break an agent turn into chronologically-stacked bubbles: reasoning, tool calls, and assistant text — each its own visible card.
2. Show a streaming indicator in the sidebar (pulsing dot) and a status bar in the chat panel.
3. Show unread indicators (solid dot) for chats that finished while the user was in another chat.

## Design

### Message roles

**Persisted to conversation history (backend):** `user`, `assistant`, `tool` only.

**UI-only bubble types (native app `ChatMessage.role`):** `user`, `assistant`, `tool`, `reasoning`

- `reasoning` is ephemeral — shown during/after streaming, never persisted, never loaded on reload, never sent to the LLM.
- `tool` bubbles are persisted (the backend already stores tool results as `tool` role messages).

### `ChatMessage` struct changes

```zig
pub const ChatMessage = struct {
    id: u64,
    role: []const u8,        // "user" | "assistant" | "tool" | "reasoning" | "system"
    content: []const u8,
    // Tool-specific fields (empty for non-tool roles)
    tool_name: []const u8 = "",
    tool_status: []const u8 = "",   // "running" | "done" | "error"
    tool_result: []const u8 = "",   // preview text, expandable
    // Reasoning-specific field
    collapsed: bool = false,       // reasoning bubbles collapse post-stream
};
```

### `Chat` struct additions

```zig
open_bubble_type: []const u8 = "",  // "", "reasoning", "assistant", "tool" — which bubble is receiving deltas
status_text: []const u8 = "",      // latest tool/status text for the status bar
```

### SSE event → bubble mapping

| SSE event | Bubble behavior |
|---|---|
| `reasoning` (new `type: "reasoning"`) | If `open_bubble_type != "reasoning"`, close previous and new `reasoning` bubble. Append deltas to current open reasoning bubble. Set `open_bubble_type = "reasoning"`. |
| `messages` (non-reasoning) | Close any open reasoning bubble. If `open_bubble_type != "assistant"`, new `assistant` bubble. Append deltas. Set `open_bubble_type = "assistant"`. |
| `tool_start` (with args) | Close any open reasoning bubble. New `tool` bubble (status: running, tool_name set). Set `open_bubble_type = "tool"`. Set `chat.status_text = tool_name + args summary`. |
| `tool_result` | Find most recent `tool` bubble with `tool_status == "running"`, update `tool_status = "done"` and `tool_result` in place (no new bubble, no position change). Do NOT change `open_bubble_type` — a new `text_delta` or `reasoning` will open the next bubble. |
| `interrupt` | HITL approval bar (unchanged) |
| `done` | Finalize all open bubbles, set `open_bubble_type = ""`, clear `status_text` |
| `error` | If `open_bubble_type` is set, append error to current bubble. Else new `system` bubble with error text. |
| `cancelled` | Finalize, set `open_bubble_type = ""`, clear `status_text` |

**"Open bubble" rule:** A bubble is "open" (receiving deltas) until a different event type arrives. When a new event type arrives, the previous bubble is closed (marked final) and a new one is created if needed.

Example turn timeline:
```
[user bubble]
[reasoning bubble]       ← reasoning_start + deltas
[tool bubble: running]   ← tool_input_start
[tool bubble: done]      ← tool_result (same bubble, updated in place)
[reasoning bubble]       ← new reasoning_start + deltas
[tool bubble: running]   ← new tool_input_start
[tool bubble: done]      ← tool_result
[assistant bubble]       ← text_delta + deltas
```

### On reload from history

- Only `user`, `assistant`, `tool` bubbles load from the backend `/conversation` endpoint.
- `tool` bubbles load with `tool_status = "done"` and `tool_result` from the stored tool output.
- No `reasoning` bubbles appear on reload (never persisted).

### Bubble rendering

**User** — right-aligned card, surface_subtle background, rounded. (unchanged)

**Assistant** — left-aligned card with "Assistant" label, surface background, border. (unchanged)

**Reasoning** — distinct card, italic text, muted accent border, "Thinking" header in small accent text. Collapsed by default after streaming completes (shows first line + click to expand). Expanded during streaming (showing all accumulated text).

**Tool** — compact card with:
- Tool icon (derived from tool name prefix: `web_` → search, `files_` → file, `email_` → mail, `time_` → clock, `shell_` → terminal, default → wrench)
- Tool name in accent text
- Status line: "running" (with pulsing dot) or result preview (muted, truncated to one line)
- Expandable: click to show full result text

### Sidebar streaming indicator

Each chat row currently has a `circle-dot` icon. Replace with:

| State | Indicator |
|---|---|
| Idle, no unread | Nothing (clean title, no dot) |
| Streaming | Pulsing accent-colored dot (opacity oscillation via `sin(frame_time)`) |
| Unread | Solid accent-colored dot |

The pulsing animation uses the GPU frame timer — `opacity = 0.4 + 0.6 * abs(sin(t * 3.0))` where `t` is seconds since stream start. No animation framework needed.

The `unread_count` field already exists on `Chat` — it gets incremented when a stream finishes in a non-active chat. Switching to that chat resets it to 0. The solid dot shows when `unread_count > 0 && !streaming`.

### Chat panel status bar

A thin bar appears above the composer when `active_chat.streaming == true`:

```
[•] web_search: searching for tokyo weather...
```

- Pulsing accent dot
- Text from `chat.status_text` (updated on `tool_start` events)
- Disappears when streaming finishes (`streaming == false`)

### Backend changes

1. **`reasoning` event type** — emit `reasoning_start`/`reasoning_delta` as `type: "reasoning"` events instead of `type: "messages"` with `[Reasoning]` prefix:

```python
# Before
yield f"data: {json.dumps({'type': 'messages', 'data': {'content': f'[Reasoning] {chunk.content}'}})}\n\n"

# After
yield f"data: {json.dumps({'type': 'reasoning', 'data': {'content': chunk.content}})}\n\n"
```

2. **`tool_input_start` with args** — include tool arguments in the event for the compact card display:

```python
# Before
yield f"data: {json.dumps({'type': 'updates', 'data': {'content': f'Using tool: {chunk.tool}'}})}\n\n"

# After
yield f"data: {json.dumps({'type': 'tool_start', 'data': {'tool': chunk.tool, 'call_id': chunk.call_id, 'args': chunk.args}})}\n\n"
```

3. **`tool_result` event** — emit as a distinct `type: "tool_result"` event with structured data instead of `type: "updates"`:

```python
# Before
yield f"data: {json.dumps({'type': 'updates', 'data': {'content': output}})}\n\n"

# After
yield f"data: {json.dumps({'type': 'tool_result', 'data': {'tool': chunk.tool, 'call_id': chunk.call_id, 'result': output}})}\n\n"
```

### Native app `stream_line` handler changes

The `stream_line` handler in `main.zig` currently has three branches: `messages`, `updates`, `interrupt`. New branches:

| Event type | Handler logic |
|---|---|
| `reasoning` | If `open_bubble_type != "reasoning"`, new `reasoning` bubble. Else append to current reasoning bubble. Set `open_bubble_type = "reasoning"`. |
| `messages` | Close open reasoning bubble. If `open_bubble_type != "assistant"`, new `assistant` bubble. Else append. Set `open_bubble_type = "assistant"`. |
| `tool_start` | Close open reasoning bubble. `addMessage(chat, "tool", "")` with `tool_name` and `tool_status = "running"`. Set `open_bubble_type = "tool"`. Set `chat.status_text`. |
| `tool_result` | Find most recent tool bubble with `tool_status == "running"`, update `tool_status = "done"` and `tool_result` in place. Do NOT change `open_bubble_type`. |
| `interrupt` | Set `chat.has_pending` (unchanged). |
| `done` | Finalize all open bubbles, set `open_bubble_type = ""`, clear `status_text`. |
| `error` | If `open_bubble_type` is set, append error to current bubble. Else new `system` bubble. |

**"Open bubble" tracking** — the chat tracks which bubble type is currently open via a field `open_bubble_type: []const u8` (empty = none open). When a different event type arrives, set the previous bubble's content as final and open a new one.

### `finalizeStream` changes

```zig
fn finalizeStream(chat: *Chat) void {
    chat.streaming = false;
    chat.fetch_key = 0;
    chat.open_bubble_type = "";
    chat.status_text = "";
    // Remove empty assistant bubble if created but never received content
    if (chat.msg_count > 0) {
        const last = &chat._messages[chat.msg_count - 1];
        if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
            chat.msg_count -= 1;
            chat.messages = chat._messages[0..chat.msg_count];
        }
    }
    // Collapse all reasoning bubbles (set collapsed = true for rendering)
    var i: usize = 0;
    while (i < chat.msg_count) : (i += 1) {
        if (std.mem.eql(u8, chat._messages[i].role, "reasoning")) {
            chat._messages[i].collapsed = true;
        }
    }
}
```

### Theme tokens

Reasoning bubbles use existing tokens — no new tokens needed:
- Background: `surface` (same as assistant, but with italic text)
- Border: `border` with accent left border (4px accent on left edge)
- Text: `text_muted`, italic
- Header: `accent`, small size

Tool bubbles:
- Background: `surface_subtle`
- Border: `border`
- Tool name: `accent`, small, bold
- Status: `text_muted`, small
- Result: `text_muted`, small, truncated

### Files changed

**Backend:**
- `src/http/routers/conversation.py` — SSE event restructuring: emit `type: "reasoning"`, `type: "tool_start"`, `type: "tool_result"` instead of the current `type: "messages"` (with `[Reasoning]` prefix) and `type: "updates"`. Apply to both `message_stream` and `approve` endpoints.

**Native app:**
- `native-sdk-experiment/src/main.zig`:
  - `ChatMessage` struct — add `tool_name`, `tool_status`, `tool_result`, `collapsed` fields
  - `Chat` struct — add `open_bubble_type`, `status_text` fields (add to `view_unbound` if needed)
  - `stream_line` handler — new event branches for `reasoning`, `tool_start`, `tool_result`
  - `buildMessageBubble` — render `reasoning` (italic card with "Thinking" header, collapsed/expanded) and `tool` (compact card with icon, name, status) bubble types
  - `buildSidebar` — replace `circle-dot` icon with streaming/unread dot indicator
  - Status bar rendering — new bar above composer when `streaming == true`
  - `finalizeStream` — collapse reasoning bubbles, clear `open_bubble_type` and `status_text`
- `native-sdk-experiment/src/theme.zig` — (no changes, existing tokens sufficient)
- `native-sdk-experiment/src/tests.zig` — update tests for new bubble types and event handling

### Testing

1. **Unit tests** (`native test`):
   - `reasoning` event creates reasoning bubble
   - `tool_start` event creates tool bubble with running status
   - `tool_result` event updates tool bubble in place
   - Multiple reasoning segments create separate bubbles
   - `done` finalizes all open bubbles
   - Sidebar indicator state: idle (no dot), streaming (pulsing), unread (solid)

2. **E2E test** (`native automate`):
   - Send a message that triggers tool calls
   - Assert reasoning bubbles appear
   - Assert tool bubbles appear with running then done status
   - Assert assistant bubble appears after tool calls
   - Assert pulsing dot in sidebar during streaming
   - Switch to another chat, assert solid dot appears when stream finishes

3. **Backend test**:
   - `curl` SSE stream and verify `type: "reasoning"`, `type: "tool_start"`, `type: "tool_result"` events are emitted correctly