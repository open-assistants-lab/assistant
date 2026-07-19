# Chat UX Completeness — Fixes & Features

**Date:** 2026-07-19  
**Status:** Design (pre-implementation)

## Overview

Six UX gaps and two backend bugs to fix, plus two features to add. A4 (client-side stream abort) was investigated but needs no change — backend-side cancel is sufficient. Grouped by area.

---

## Part A: UX Gaps

### A1. Multiline composer

**Problem:** Composer is single-line. User wants Enter to send, Shift+Enter for newline, multi-line display.

**SDK constraint:** The Native SDK's submit predicate is hardcoded per widget kind:
- `textField`: Enter submits (no Shift+Enter newline, single-line display)
- `textarea`: Cmd+Enter submits, plain Enter inserts newline, Shift+Enter inserts newline (multi-line display)

The `isSubmitKeyboard` function is internal and cannot be customized. The `widgetKeyboardNewlineTextEditEvent` only fires for `textarea` (not `textField`), and `TextInputEvent` doesn't carry modifier info, so `on_input` can't distinguish plain Enter from Shift+Enter.

**Design — use `textarea` with Cmd+Enter to submit:**
- Cmd+Enter sends the message (macOS convention for multiline fields).
- Enter or Shift+Enter inserts a newline.
- Multi-line display, grows with content up to ~5 lines.
- A hint text "⏎ Cmd+Enter to send" guides the user.

**Implementation:**
- Replace `ui.textField(...)` with `ui.el(.textarea, .{...}, .{})`.
- Set `.on_submit = .send_message` (fires on Cmd+Enter).
- Set `.on_input = AppUi.inputMsg(.input_changed)` (fires on all text changes including newlines).
- Bind `widget.text_selection = active_chat.draft_selection` (same fix as textField — preserves selection across renders).
- Composer height: `.grow = 0`, min 1 line (~36px), grows with content up to ~5 lines.
- Reset after send: `draft_text = ""` clears content; textarea shrinks to 1 line. Also reset `draft_selection = .{ .anchor = 0, .focus = 0 }`.
- Group textarea + model button + Send button in a bordered container (shared with C2 model selector):
  ```
  ┌───────────────────────────────────────────┐
  │ [textarea]                                │
  │ [ollama-cloud · deepseek-v4-flash]   [⏎] │
  └───────────────────────────────────────────┘
  ```
  - Container: `ui.el(.bubble, .{ .style_tokens = .{ .background = .surface_subtle, .border_color = .border } }, ...)`
  - Textarea: no border (`border_color` not set), fills width.
  - Bottom row: model button (left, ghost variant) + `spacer(1)` + Send button (right).
  - Hint text "⏎ Cmd+Enter to send": small text below the container, not inside it.

**Files:** `native-sdk-experiment/src/main.zig`

### A2. Loading indicator on chat switch

**Problem:** When switching to a chat with no loaded history, the screen flashes "How can I help?" before messages populate.

**Design:**
- Add `history_loading: bool = false` to `Chat` struct.
- Set `history_loading = true` when firing the history fetch (in `switch_chat` and `initFx`).
- Set `history_loading = false` in `chat_history_loaded` and `history_loaded` handlers.
- In `buildChatPanel`, when `count == 0`:
  - If `history_loading`: show "Loading..." with a subtle spinner/dots text.
  - If `!history_loading`: show current "How can I help?" empty state.

**Files:** `native-sdk-experiment/src/main.zig`

### A3. Error surfacing

**Problem:** Backend down, stream failures, and network errors all silently fail.

**Design:**
- **Backend down on startup:** If `sessions_loaded` or `history_loaded` response outcome is not `.ok`, add a system message to the active chat: "Unable to connect to server. Is the backend running?"
- **Stream error mid-response:** The `stream_done` handler should check `response.outcome`. If not `.ok`, add a system message with the outcome name: "Stream error: {outcome_name}" (using `@tagName(response.outcome)` since `response.body` is empty for non-ok outcomes). Then finalize.
- **History fetch error:** If `chat_history_loaded` outcome is not `.ok`, add system message to that chat: "Failed to load chat history."
- **Title generation error:** Already handled (silent fallback to first-message title). No change needed.

**Note:** `EffectResponse.body` is empty for non-ok outcomes (per SDK docs). Use `@tagName(response.outcome)` for the error message string (e.g., "rejected", "connect_failed", "timeout").

**Files:** `native-sdk-experiment/src/main.zig`

### A4. Client-side stream abort

**Problem:** Stop button POSTs to `/message/cancel` but doesn't abort the in-flight fetch. If backend is slow, the stream keeps coming.

**Design:**
- **Verified:** The Native SDK's `Effects` does NOT have a `cancelFetch` method. There is no client-side fetch abort API.
- **Current behavior is acceptable:** The `/message/cancel` POST signals the backend to stop. The backend checks `_cancel_flags` between chunks and breaks out of the stream loop. The stream fetch completes naturally when the backend stops sending chunks.
- **No change needed** — the cancel flow works correctly via backend-side termination. The stream fetch will complete shortly after the cancel POST, and `stream_done` fires with `.ok` outcome (the stream simply ends early). The latency between cancel and stream end is one chunk (~50ms).

**Files:** None (no change needed — backend-side cancel is sufficient)

### A5. Collapse historical reasoning bubbles

**Problem:** Reasoning messages loaded from history appear expanded. Should be collapsed like live ones after stream completion.

**Design:**
- In `addHistoryMessage`, add explicit handling for `role == "reasoning"`:
  ```zig
  if (std.mem.eql(u8, role_str, "reasoning")) {
      // Same as addMessage but with collapsed = true
      addMessage(chat, allocator, role_str, content_str);
      chat._messages[chat.msg_count - 1].collapsed = true;
      return;
  }
  ```
- Currently `addHistoryMessage` only checks for "tool", everything else goes to `addMessage` without setting `collapsed`.

**Files:** `native-sdk-experiment/src/main.zig`

---

## Part B: Backend Bugs

### B1. Loop cache memory leak

**Problem:** `_loop_cache` in `runner.py` grows unboundedly. No eviction when sessions are closed/deleted.

**Design:**
- Add `reset_sdk_loop` call when a session is deleted. The delete endpoint (`DELETE /conversation/session`) should call `reset_sdk_loop(user_id, session_id=session_id)`.
- Add a max cache size (e.g., 50 entries) with simple LRU eviction: when cache exceeds limit, evict the oldest entry.
- No TTL needed — desktop app, sessions are long-lived.

**Implementation:**
```python
import collections

_MAX_LOOP_CACHE = 50
_loop_cache: collections.OrderedDict[str, AgentLoop] = collections.OrderedDict()

# On cache insertion:
_loop_cache[cache_key] = loop
_loop_cache.move_to_end(cache_key)  # mark as most-recently-used
if len(_loop_cache) > _MAX_LOOP_CACHE:
    _loop_cache.popitem(last=False)  # evict least-recently-used
```

Change `_loop_cache` from `dict` to `OrderedDict`. On every cache hit (read), call `move_to_end` to maintain LRU order.

**Files:** `src/sdk/runner.py`, `src/http/routers/conversation.py` (call `reset_sdk_loop` in delete endpoint)

### B2. WS path persists empty tool content

**Problem:** WebSocket handler persists tool messages with empty content (`""`). Tool results are lost from history.

**Design:**
- In `ws.py`, accumulate tool results during streaming into a `tool_results: dict[str, str]` dict (keyed by `call_id`), then use them when persisting:
  ```python
  # At the top of the handler, alongside tool_metadata_list:
  tool_results: dict[str, str] = {}

  # In the tool_result handler (line ~124):
  result_preview = chunk.result_preview or ""
  tool_results[call_id] = result_preview[:500]

  # When persisting (line ~168):
  for tm in tool_metadata_list:
      call_id = tm.get("tool_call_id", "")
      result_content = tool_results.get(call_id, "")
      conversation.add_message(
          "tool", result_content, metadata={**tm, "workspace_id": workspace_id}
      )
  ```

**Files:** `src/http/routers/ws.py`

---

## Part C: New Features

### C1. Timestamps on messages

**Problem:** No timestamps shown on chat messages. Hard to tell when a message was sent.

**Design:**
- Each `ChatMessage` gets a `timestamp: []const u8 = ""` field (formatted as "HH:MM" for today, "MMM DD" for older).
- **For live messages:** The native app has no real-time clock API. Instead, use the `stream_done` response timestamp from the backend. The SSE `done` event doesn't currently include a timestamp, so we'll use the `timestamp` field from the `GET /conversation` history response on next load.
- **For history messages:** The backend's `GET /conversation` response includes a `timestamp` field (ISO format: `2026-07-17T15:31:16.778017+00:00`). Parse the first 5 chars of the time portion (`HH:MM`) in `addHistoryMessage`.
- **For live messages (simpler approach):** Don't timestamp live messages during streaming. When the user reloads the app, timestamps appear from history. This avoids needing a clock API.
- **Display:** Show timestamp below each user and assistant bubble in `text_muted` + `size = .xs`. Not shown for tool/reasoning bubbles.
- **Parsing:** In `addHistoryMessage`, extract `timestamp` from the JSON item:
  ```zig
  const ts_val = item.object.get("timestamp");
  const ts_str = switch (ts_val) {
      .string => |s| s,
      else => "",
  };
  // Extract HH:MM from ISO format (chars 11-15, i.e. ts_str[11..16] in Zig)
  const time_str = if (ts_str.len >= 16) ts_str[11..16] else "";
  ```
  Set `msg.timestamp = time_str` (arena-allocated).

**Backend change:** None needed. The `GET /conversation` response already includes `timestamp` field. The `POST /message` and SSE endpoints don't need changes since we rely on history reload for timestamps.

**Files:** `native-sdk-experiment/src/main.zig`

### C2. Model selector in UI

**Problem:** No way to change the model from the UI. Model is hardcoded to `deepseek:deepseek-v4-flash`.

**UX:** A small model name button in the composer group, same row as the Send button, aligned to the left. The Send button stays on the right. Clicking the model button cycles to the next available model.

Future: a proper dropdown once the SDK exposes builder methods for `select` or `dropdown_menu`. For now, cycle-button is the simplest approach that works with the available widgets.

```
┌───────────────────────────────────────────┐
│ [textarea]                                │
│ [ollama-cloud · deepseek-v4-flash]   [⏎] │
└───────────────────────────────────────────┘
```

The bottom row contains: model button (left), spacer, Send button (right). The "⏎ Cmd+Enter to send" hint is shown as the Send button's tooltip or as small text below the container — not inside it, to keep the row clean.

The model button shows `{provider_display} · {model_name}` (e.g., "Ollama Cloud · deepseek-v4-flash"). Clicking cycles to the next available model.

**Bottom row layout:** `ui.row(.{ .gap = 8, .cross = .center }, .{ model_button, ui.spacer(1), send_button })`.
- `model_button`: left-aligned, `variant = .ghost`, shows `{provider_display} · {model_name}`.
- `ui.spacer(1)`: grows to push Send to the right.
- `send_button`: right-aligned.

The model button and Send button share the bottom row of the bordered composer container.

**Design:**
- **Backend:** Add `GET /models` endpoint that returns models from providers with configured API keys:
  ```python
  _PROVIDER_DISPLAY = {
      "ollama-cloud": "Ollama Cloud",
      "anthropic": "Anthropic",
      "openai": "OpenAI",
  }

  @router.get("/models")
  async def list_available_models() -> dict[str, list[dict[str, str]]]:
      import os
      from src.sdk.registry import list_models
      models = []
      configured = []
      if os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_BASE_URL"):
          configured.append("ollama-cloud")
      if os.environ.get("ANTHROPIC_API_KEY"):
          configured.append("anthropic")
      if os.environ.get("OPENAI_API_KEY"):
          configured.append("openai")
      for provider in configured:
          for m in list_models(provider=provider):
              models.append({
                  "id": f"{provider}:{m.id}",
                  "name": m.name,
                  "provider": provider,
                  "provider_display": _PROVIDER_DISPLAY.get(provider, provider.title()),
              })
      return {"models": models}
  ```
  Returns ~20-100 models. Each entry includes `provider` (machine name) and `provider_display` (human-readable, e.g., "Ollama Cloud", "Anthropic", "OpenAI").
- **Native:** Fetch models on startup (`initFx`). Store in `Model.available_models` (array of `ModelOption`) and `Model.selected_model_idx: usize = 0`.
- `ModelOption` struct: `{ id: []const u8, name: []const u8, provider_display: []const u8 }`.
- A `Msg.cycle_model` message cycles `selected_model_idx = (idx + 1) % available_models.len`.
- Model button label: `std.fmt.allocPrint(ui.arena, "{s} · {s}", .{ model.provider_display, model.name })`.
- Pass `available_models[selected_model_idx].id` in the `/message/stream` POST body instead of the hardcoded model.
- If `available_models` is empty (backend down), fall back to the hardcoded default and hide the model button.
- **During streaming:** Disable the model button (can't change model mid-stream). The Send button becomes Stop, the model button stays visible but non-interactive.
- **Persistence:** Not in v1 — model resets to first on restart.

**Files:** `src/http/routers/conversation.py` (new `GET /models`), `native-sdk-experiment/src/main.zig`

---

## Implementation Order

1. **A5: Collapse historical reasoning** (small fix)
2. **B2: WS tool content** (1 line fix)
3. **A2: Loading indicator** (small, high impact)
4. **A3: Error surfacing** (medium, essential)
5. **B1: Loop cache eviction** (small)
6. **C1: Timestamps** (medium)
7. **A1: Multiline composer** (textarea swap + bordered group container)
8. **C2: Model selector** (larger — needs backend endpoint + UI)

**A4 not implemented** — no `cancelFetch` API in the SDK. Backend-side cancel (`/message/cancel`) already stops the stream within ~50ms. This is sufficient.

## What this spec does NOT cover

- Message edit/retry
- Copy button on messages
- Settings panel
- Per-chat model selection
- Markdown rendering (reverted for performance, needs separate investigation)

## Test Plan

**Native tests (`tests.zig`):**
- A2: `history_loading` flag set on switch, cleared on load complete
- A3: System message added when `stream_done` outcome is not `.ok`
- A5: Historical reasoning messages have `collapsed = true`
- C1: `addHistoryMessage` parses `timestamp` field from JSON
- C2: Model list fetched on startup, `cycle_model` advances index, selected model passed to stream request
- A1: Composer uses `textarea` widget, `on_submit` fires on Cmd+Enter, `text_selection` bound to `draft_selection`

**Backend tests:**
- B2: WS handler persists tool result content (not empty string)
- B1: Loop cache evicts oldest entry when exceeding 50
- C2: `GET /models` returns only models from configured providers

## Files affected

**Backend:**
- `src/http/routers/conversation.py` — `GET /models` endpoint, `reset_sdk_loop` in delete endpoint
- `src/http/routers/ws.py` — tool content fix
- `src/sdk/runner.py` — loop cache eviction

**Native:**
- `native-sdk-experiment/src/main.zig` — all UX fixes + model selector + timestamps
- `native-sdk-experiment/src/tests.zig` — tests for new behaviors