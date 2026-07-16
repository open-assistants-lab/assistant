# Linear-Like Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Linear-like design system (teal accent, dark+light, sidebar, chat panel, composer, HITL bar, RHS placeholder) for the Native SDK Assistant app.

**Architecture:** Token Overlay — custom `ColorTokens` + `RadiusTokens` structs in a new `theme.zig` file, wired via `tokens_fn`. Markup rewritten in `app.native` to full layout (sidebar + chat + RHS placeholder). Model expanded with theme state, chat list, search, scroll tracking.

**Tech Stack:** Zig 0.16, Native SDK 0.4.2, Native markup (`.native`), `std.json`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/theme.zig` | Create | Dark/light `ColorTokens`, `RadiusTokens`, `DesignTokens` builder, `tokens_fn` |
| `src/main.zig` | Rewrite | Expanded Model (theme, chats, search, scroll), expanded Msg, update logic |
| `src/app.native` | Rewrite | Full layout: split sidebar + main + RHS placeholder, chat list, search, composer, HITL bar |
| `src/tests.zig` | Rewrite | Tests for theme switching, chat list, search filter, scroll, input, streaming, HITL |

---

### Task 1: Create theme.zig with dark/light color tokens

**Files:**
- Create: `native-sdk-experiment/src/theme.zig`
- Test: `native-sdk-experiment/src/tests.zig` (appended test)

- [ ] **Step 1: Write the failing test in tests.zig**

Add to the end of `src/tests.zig`:

```zig
test "theme: dark tokens have teal accent" {
    const theme = @import("theme.zig");
    const tokens = theme.darkTokens();
    try testing.expectEqual(@as(u8, 0x14), tokens.colors.accent.r);
    try testing.expectEqual(@as(u8, 0xb8), tokens.colors.accent.g);
    try testing.expectEqual(@as(u8, 0xa6), tokens.colors.accent.b);
}

test "theme: light tokens have darker teal accent" {
    const theme = @import("theme.zig");
    const tokens = theme.lightTokens();
    try testing.expectEqual(@as(u8, 0x0d), tokens.colors.accent.r);
    try testing.expectEqual(@as(u8, 0x94), tokens.colors.accent.g);
    try testing.expectEqual(@as(u8, 0x88), tokens.colors.accent.b);
}

test "theme: radius tokens are comfortable values" {
    const theme = @import("theme.zig");
    const tokens = theme.darkTokens();
    try testing.expectEqual(@as(f32, 8.0), tokens.radius.sm);
    try testing.expectEqual(@as(f32, 12.0), tokens.radius.md);
    try testing.expectEqual(@as(f32, 14.0), tokens.radius.lg);
    try testing.expectEqual(@as(f32, 18.0), tokens.radius.xl);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL — `theme.zig` does not exist yet

- [ ] **Step 3: Create theme.zig with dark/light tokens**

Create `native-sdk-experiment/src/theme.zig`:

```zig
const std = @import("std");
const native_sdk = @import("native_sdk");

const canvas = native_sdk.canvas;
const Color = canvas.Color;

pub const dark_tokens: canvas.DesignTokens = .{
    .colors = .{
        .background = Color.rgb8(8, 9, 12),
        .surface = Color.rgb8(17, 19, 25),
        .surface_subtle = Color.rgb8(14, 16, 21),
        .surface_pressed = Color.rgb8(26, 29, 36),
        .text = Color.rgb8(244, 244, 245),
        .text_muted = Color.rgb8(139, 141, 152),
        .border = Color.rgb8(29, 30, 34),
        .accent = Color.rgb8(20, 184, 166),
        .accent_text = Color.rgb8(4, 47, 46),
        .destructive = Color.rgb8(248, 113, 113),
        .destructive_text = Color.rgb8(26, 10, 10),
        .success = Color.rgb8(74, 222, 128),
        .success_text = Color.rgb8(5, 46, 26),
        .warning = Color.rgb8(251, 191, 36),
        .warning_text = Color.rgb8(26, 22, 6),
        .info = Color.rgb8(96, 165, 250),
        .info_text = Color.rgb8(10, 22, 40),
        .focus_ring = Color.rgb8(20, 184, 166),
        .shadow = Color.rgba8(0, 0, 0, 150),
        .scrim = Color.rgba8(0, 0, 0, 26),
        .disabled = Color.rgb8(58, 61, 68),
    },
    .radius = .{
        .sm = 8,
        .md = 12,
        .lg = 14,
        .xl = 18,
    },
};

pub const light_tokens: canvas.DesignTokens = .{
    .colors = .{
        .background = Color.rgb8(255, 255, 255),
        .surface = Color.rgb8(249, 250, 251),
        .surface_subtle = Color.rgb8(243, 244, 246),
        .surface_pressed = Color.rgb8(229, 231, 235),
        .text = Color.rgb8(24, 24, 27),
        .text_muted = Color.rgb8(113, 113, 122),
        .border = Color.rgb8(229, 231, 235),
        .accent = Color.rgb8(13, 148, 136),
        .accent_text = Color.rgb8(255, 255, 255),
        .destructive = Color.rgb8(220, 38, 38),
        .destructive_text = Color.rgb8(255, 255, 255),
        .success = Color.rgb8(22, 163, 74),
        .success_text = Color.rgb8(255, 255, 255),
        .warning = Color.rgb8(217, 119, 6),
        .warning_text = Color.rgb8(255, 255, 255),
        .info = Color.rgb8(37, 99, 235),
        .info_text = Color.rgb8(255, 255, 255),
        .focus_ring = Color.rgb8(13, 148, 136),
        .shadow = Color.rgba8(0, 0, 0, 26),
        .scrim = Color.rgba8(0, 0, 0, 26),
        .disabled = Color.rgb8(212, 212, 216),
    },
    .radius = .{
        .sm = 8,
        .md = 12,
        .lg = 14,
        .xl = 18,
    },
};

pub fn darkTokens() canvas.DesignTokens {
    return dark_tokens;
}

pub fn lightTokens() canvas.DesignTokens {
    return light_tokens;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd native-sdk-experiment && native test`
Expected: PASS — 3 new theme tests pass

- [ ] **Step 5: Commit**

```bash
cd native-sdk-experiment
git add src/theme.zig src/tests.zig
git commit -m "feat: add theme.zig with dark/light color tokens and radius tokens"
```

---

### Task 2: Expand Model with theme state, chat list, search

**Files:**
- Modify: `native-sdk-experiment/src/main.zig` (Model + Msg expansion)
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing tests for theme toggle and chat list**

Add to `src/tests.zig`:

```zig
test "theme: toggle switches dark to light" {
    var model = main.initialModel();
    model.allocator = testing.allocator;
    try testing.expectEqual(main.ThemeMode.dark, model.theme_mode);
    main.update(&model, .toggle_theme, &noopFx(testing.allocator));
    try testing.expectEqual(main.ThemeMode.light, model.theme_mode);
    main.update(&model, .toggle_theme, &noopFx(testing.allocator));
    try testing.expectEqual(main.ThemeMode.dark, model.theme_mode);
}

test "chat list: new chat creates empty chat and sets active" {
    var model = main.initialModel();
    model.allocator = testing.allocator;
    var fx = noopFx(testing.allocator);
    try testing.expectEqual(@as(usize, 0), model.chat_count);
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);
    try testing.expectEqual(@as(usize, 0), model.active_chat_idx);
    try testing.expectEqual(@as(usize, 0), model.chats[0].msg_count);
}

test "chat list: switch chat sets active index" {
    var model = main.initialModel();
    model.allocator = testing.allocator;
    var fx = noopFx(testing.allocator);
    main.update(&model, .new_chat, &fx);
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 1), model.active_chat_idx);
    main.update(&model, .{ .switch_chat = 0 }, &fx);
    try testing.expectEqual(@as(usize, 0), model.active_chat_idx);
}

test "search: filters chat list by title" {
    var model = main.initialModel();
    model.allocator = testing.allocator;
    var fx = noopFx(testing.allocator);
    main.update(&model, .new_chat, &fx);
    model.chats[0].title = "Triage inbox";
    main.update(&model, .new_chat, &fx);
    model.chats[1].title = "Plan launch";
    main.update(&model, .{ .search_input = .{ .insert_text = "tri" } }, &fx);
    try testing.expectEqualStrings("tri", model.search_query);
    const filtered = model.filteredChats();
    try testing.expectEqual(@as(usize, 1), filtered.len);
    try testing.expectEqualStrings("Triage inbox", filtered[0].title);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL — `ThemeMode`, `toggle_theme`, `new_chat`, `switch_chat`, `search_input`, `filteredChats` not defined

- [ ] **Step 3: Expand Model and Msg in main.zig**

Replace the Model, Msg, and related sections of `src/main.zig`:

```zig
const max_messages = 200;
const max_chats = 50;

pub const ThemeMode = enum { dark, light };

pub const ChatMessage = struct {
    id: u64,
    role: []const u8,
    content: []const u8,
};

pub const Chat = struct {
    id: u64,
    title: []const u8 = "New chat",
    _messages: [max_messages]ChatMessage = undefined,
    messages: []ChatMessage = &.{},
    msg_count: usize = 0,
    next_id: u64 = 1,
    unread_count: u32 = 0,
};

pub const Msg = union(enum) {
    input_changed: canvas.TextInputEvent,
    search_input: canvas.TextInputEvent,
    send_message,
    new_chat,
    switch_chat: usize,
    toggle_theme,
    cancel,
    approve,
    reject,
    stream_line: native_sdk.EffectLine,
    stream_done: native_sdk.EffectResponse,
    stream_error: []const u8,
    approve_done: native_sdk.EffectResponse,
    reject_done: native_sdk.EffectResponse,
    cancel_done: native_sdk.EffectResponse,

    pub const view_unbound = .{
        "stream_line",
        "stream_done",
        "stream_error",
        "approve_done",
        "reject_done",
        "cancel_done",
    };
};

pub const Model = struct {
    theme_mode: ThemeMode = .dark,
    chats: [max_chats]Chat = undefined,
    chat_count: usize = 0,
    active_chat_idx: usize = 0,
    next_chat_id: u64 = 1,
    search_query: []const u8 = "",
    input_text: []const u8 = "",
    streaming: bool = false,
    has_pending: bool = false,
    pending_tool: []const u8 = "",
    pending_call_id: []const u8 = "",
    allocator: std.mem.Allocator = undefined,

    pub const view_unbound = .{
        "chat_count",
        "next_chat_id",
        "search_query",
        "pending_call_id",
        "streaming",
        "allocator",
    };

    pub fn activeChat(self: *Model) *Chat {
        return &self.chats[self.active_chat_idx];
    }

    pub fn filteredChats(self: *const Model) []const Chat {
        if (self.search_query.len == 0) return self.chats[0..self.chat_count];
        var count: usize = 0;
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            if (std.mem.indexOf(u8, self.chats[i].title, self.search_query) != null) {
                count += 1;
            }
        }
        return self.chats[0..self.chat_count];
    }
};
```

- [ ] **Step 4: Add update logic for new Msg variants**

In the `update` function, add these cases:

```zig
        .toggle_theme => {
            model.theme_mode = switch (model.theme_mode) {
                .dark => .light,
                .light => .dark,
            };
        },
        .new_chat => {
            if (model.chat_count >= max_chats) return;
            model.chats[model.chat_count] = .{ .id = model.next_chat_id };
            model.next_chat_id += 1;
            model.active_chat_idx = model.chat_count;
            model.chat_count += 1;
            model.input_text = "";
        },
        .switch_chat => |idx| {
            if (idx >= model.chat_count) return;
            model.active_chat_idx = idx;
            model.chats[idx].unread_count = 0;
            model.input_text = "";
        },
        .search_input => |event| {
            switch (event) {
                .insert_text => |text| {
                    model.search_query = std.fmt.allocPrint(
                        model.allocator,
                        "{s}{s}",
                        .{ model.search_query, text },
                    ) catch return;
                },
                .clear => model.search_query = "",
                .delete_backward => {
                    if (model.search_query.len > 0) {
                        model.search_query = model.search_query[0 .. model.search_query.len - 1];
                    }
                },
                else => {},
            }
        },
```

Also update `send_message`, `stream_line`, `addMessage`, `appendToLastMessage` to operate on `model.activeChat()` instead of the model's own message array. Replace the old `Model` message fields usage:

```zig
        .send_message => {
            const text = std.mem.trim(u8, model.input_text, " ");
            if (text.len == 0 or model.streaming) return;
            const chat = model.activeChat();
            addMessage(chat, model.allocator, "user", text);
            if (chat.msg_count == 1) {
                chat.title = model.allocator.dupe(u8, text) catch text;
            }
            model.input_text = "";
            model.streaming = true;

            const body = std.fmt.allocPrint(
                model.allocator,
                "{{\"message\":\"{s}\",\"user_id\":\"native_sdk_chat\",\"model\":\"deepseek:deepseek-v4-flash\"}}",
                .{text},
            ) catch return;
            fx.fetch(.{
                .key = stream_key,
                .url = "http://127.0.0.1:8080/message/stream",
                .method = .POST,
                .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                .body = body,
                .response = .stream,
                .on_line = Effects.lineMsg(.stream_line),
                .on_response = Effects.responseMsg(.stream_done),
            });
        },
```

Update `addMessage` and `appendToLastMessage` to take a `*Chat`:

```zig
fn addMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
    if (chat.msg_count >= max_messages) return;
    chat._messages[chat.msg_count] = .{
        .id = chat.next_id,
        .role = allocator.dupe(u8, role) catch return,
        .content = allocator.dupe(u8, content) catch return,
    };
    chat.next_id += 1;
    chat.msg_count += 1;
    chat.messages = chat._messages[0..chat.msg_count];
}

fn appendToLastMessage(chat: *Chat, allocator: std.mem.Allocator, content: []const u8) void {
    if (chat.msg_count == 0) {
        addMessage(chat, allocator, "assistant", content);
        return;
    }
    const last = &chat._messages[chat.msg_count - 1];
    if (!std.mem.eql(u8, last.role, "assistant")) {
        addMessage(chat, allocator, "assistant", content);
        return;
    }
    const new_len = last.content.len + content.len;
    const buf = allocator.alloc(u8, new_len) catch return;
    @memcpy(buf[0..last.content.len], last.content);
    @memcpy(buf[last.content.len..], content);
    last.content = buf;
}
```

Update `stream_line` to use `model.activeChat()`:

```zig
        .stream_line => |line| {
            if (line.line.len == 0) return;
            const prefix = "data: ";
            if (!std.mem.startsWith(u8, line.line, prefix)) return;
            const body = line.line[prefix.len..];
            if (std.mem.eql(u8, body, "[DONE]")) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const event_type = root.object.get("type") orelse return;
            const data = root.object.get("data") orelse return;
            const chat = model.activeChat();
            if (std.mem.eql(u8, event_type.string, "messages")) {
                const content = data.object.get("content") orelse return;
                appendToLastMessage(chat, model.allocator, content.string);
            } else if (std.mem.eql(u8, event_type.string, "updates")) {
                const content = data.object.get("content") orelse return;
                appendToLastMessage(chat, model.allocator, content.string);
            } else if (std.mem.eql(u8, event_type.string, "interrupt")) {
                const tool = data.object.get("tool") orelse return;
                const call_id = data.object.get("call_id") orelse return;
                model.has_pending = true;
                model.pending_tool = model.allocator.dupe(u8, tool.string) catch return;
                model.pending_call_id = model.allocator.dupe(u8, call_id.string) catch return;
            }
        },
```

Update `stream_error`:

```zig
        .stream_error => |err| {
            addMessage(model.activeChat(), model.allocator, "system", err);
            model.streaming = false;
        },
```

- [ ] **Step 5: Add tokens_fn and wire it into ChatApp.create**

Add after the imports:

```zig
const theme = @import("theme.zig");

fn tokensFn(model: *const Model) canvas.DesignTokens {
    return switch (model.theme_mode) {
        .dark => theme.darkTokens(),
        .light => theme.lightTokens(),
    };
}
```

In `main()`, update the `ChatApp.create` call:

```zig
    const app_state = try ChatApp.create(allocator, .{
        .name = "native-sdk-experiment",
        .scene = shell_scene,
        .canvas_label = canvas_label,
        .update_fx = update,
        .tokens_fn = tokensFn,
        .markup = .{ .source = app_markup, .watch_path = "src/app.native", .io = init.io },
    });
```

Update window dimensions:

```zig
const window_width: f32 = 1200;
const window_height: f32 = 720;
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd native-sdk-experiment && native test`
Expected: PASS — all tests including new theme/chat/search tests

- [ ] **Step 7: Commit**

```bash
cd native-sdk-experiment
git add src/main.zig src/theme.zig src/tests.zig
git commit -m "feat: expand model with theme, chat list, search, and tokens_fn"
```

---

### Task 3: Rewrite app.native with full layout (sidebar + chat + RHS placeholder)

**Files:**
- Rewrite: `native-sdk-experiment/src/app.native`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing test for new layout structure**

Add to `src/tests.zig`:

```zig
test "layout: renders New chat button" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .button, "New chat");
}

test "layout: renders search input placeholder" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "Search chats...");
}

test "layout: renders empty state welcome" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    main.update(&model, .new_chat, &fx);

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "How can I help?");
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL — old markup doesn't have "New chat", "Search chats...", or "How can I help?"

- [ ] **Step 3: Rewrite app.native**

Replace entire `src/app.native`:

```html
<split value="0.18" on-resize="sidebar_resized">
  <column background="surface" border-color="border">
    <row padding="12" gap="8" cross="center">
      <button on-press="new_chat" variant="secondary" radius="sm" grow="1">
        <icon name="plus"/>
        New chat
      </button>
    </row>
    <input text="{search_query}" placeholder="Search chats..." on-input="search_input" padding="8" />
    <scroll grow="1" padding="8" gap="2">
      <for each="filteredChats" key="id" as="chat">
        <row gap="8" padding="8" radius="sm" background="{active_chat_idx == chat_idx | if-active}" cross="center" on-press="switch_chat:{chat_idx}">
          <if test="{chat_idx == active_chat_idx}">
            <text size="sm" foreground="accent">●</text>
          </if>
          <text size="sm" grow="1" foreground="{chat_idx == active_chat_idx | if-active-text}">{chat.title}</text>
          <if test="{chat.unread_count > 0}">
            <badge variant="info" size="sm">{chat.unread_count}</badge>
          </if>
        </row>
      </for>
    </scroll>
    <column gap="2" padding="8" border-color="border">
      <row gap="8" padding="8" radius="sm" cross="center" foreground="text_muted">
        <icon name="wrench"/>
        <text size="sm">Tools</text>
      </row>
      <row gap="8" padding="8" radius="sm" cross="center" foreground="text_muted">
        <icon name="bolt"/>
        <text size="sm">Skills</text>
      </row>
      <row gap="8" padding="8" radius="sm" cross="center" foreground="text_muted">
        <icon name="users"/>
        <text size="sm">Subagents</text>
      </row>
    </column>
    <row gap="4" padding="8" cross="center">
      <row gap="8" padding="8" radius="sm" cross="center" grow="1">
        <icon name="settings"/>
        <text size="sm" foreground="text_muted">Settings</text>
      </row>
      <button on-press="toggle_theme" variant="ghost" size="sm">
        <icon name="moon"/>
      </button>
    </row>
  </column>

  <column background="background" gap="0">
    <scroll grow="1" padding="16" gap="12">
      <for each="activeChat().messages" key="id" as="msg">
        <if test="{msg.role == user}">
          <row main="end">
            <card background="surface_subtle" radius="lg" padding="12" grow="0">
              <text>{msg.content}</text>
            </card>
          </row>
        </if>
        <else>
          <column gap="4">
            <text size="sm" foreground="accent">{msg.role}</text>
            <card background="surface" radius="lg" padding="12" border-color="border">
              <text>{msg.content}</text>
            </card>
          </column>
        </else>
      </for>
      <else>
        <column gap="16" padding="32" cross="center" main="center">
          <text size="heading">How can I help?</text>
          <text foreground="text_muted">Ask me anything, or try one of these:</text>
          <row gap="8">
            <button variant="ghost" on-press="suggestion_inbox">Triage my inbox</button>
            <button variant="ghost" on-press="suggestion_summary">Draft a weekly summary</button>
            <button variant="ghost" on-press="suggestion_contacts">Find contacts in marketing</button>
          </row>
        </column>
      </else>
    </scroll>

    <if test="{streaming}">
      <text foreground="text_muted" padding="8" size="sm">Receiving response...</text>
    </if>
    <if test="{has_pending}">
      <row background="surface" radius="md" padding="12" gap="12" cross="center">
        <text grow="1">Approve: <span mono foreground="accent">{pending_tool}</span>?</text>
        <button on-press="approve" foreground="success">Approve</button>
        <button on-press="reject" variant="ghost" foreground="destructive">Reject</button>
      </row>
    </if>
    <row gap="8" padding="12" cross="center">
      <input text="{input_text}" placeholder="Type a message..." grow="1" on-input="input_changed" on-submit="send_message" />
      <if test="{streaming}">
        <button on-press="cancel" variant="ghost">Stop</button>
      </if>
      <else>
        <button on-press="send_message" variant="primary">Send</button>
      </else>
    </row>
  </column>
</split>
```

Note: The RHS panel placeholder is intentionally omitted from markup for now — it requires a second `<split>` and a `WebViewSource` view which adds complexity. The spec notes it as a placeholder. Add a comment in markup or defer to a follow-up task.

- [ ] **Step 4: Add missing Msg variants for suggestions**

In `src/main.zig`, add to the `Msg` union:

```zig
    suggestion_inbox,
    suggestion_summary,
    suggestion_contacts,
    sidebar_resized: f32,
```

Add update cases:

```zig
        .suggestion_inbox => {
            model.input_text = model.allocator.dupe(u8, "Triage my inbox") catch return;
        },
        .suggestion_summary => {
            model.input_text = model.allocator.dupe(u8, "Draft a weekly summary") catch return;
        },
        .suggestion_contacts => {
            model.input_text = model.allocator.dupe(u8, "Find contacts in marketing") catch return;
        },
        .sidebar_resized => |frac| {
            _ = frac;
        },
```

- [ ] **Step 5: Run native check to validate markup**

Run: `cd native-sdk-experiment && native check`
Expected: PASS — `src/app.native: ok`

If there are markup errors, fix the attribute names per the native-ui skill's validation messages.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd native-sdk-experiment && native test`
Expected: PASS — layout tests find "New chat", "Search chats...", "How can I help?"

- [ ] **Step 7: Commit**

```bash
cd native-sdk-experiment
git add src/app.native src/main.zig src/tests.zig
git commit -m "feat: rewrite markup with sidebar, chat list, search, composer, and empty state"
```

---

### Task 4: Update existing tests to work with new model structure

**Files:**
- Modify: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Update existing tests to use chat-based messages**

The old tests reference `model._messages`, `model.msg_count`, `model._messages[0]`, etc. These now live on `Chat`. Update all test assertions:

For tests that send messages and check content, wrap in a chat:

```zig
test "send message adds user message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    try testing.expectEqualStrings("Hello", model.input_text);

    main.update(&model, .send_message, &fx);
    const chat = model.activeChat();
    try testing.expectEqual(@as(usize, 1), chat.msg_count);
    try testing.expectEqualStrings("user", chat._messages[0].role);
    try testing.expectEqualStrings("Hello", chat._messages[0].content);
    try testing.expect(model.streaming);
    try testing.expectEqualStrings("", model.input_text);
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/message/stream", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, "deepseek:deepseek-v4-flash") != null);
}
```

Similarly update `stream_line`, `interrupt`, `approve`, `reject` tests to create a chat first and assert on `model.activeChat()._messages`.

- [ ] **Step 2: Run test to verify it passes**

Run: `cd native-sdk-experiment && native test`
Expected: PASS — all tests pass with new chat-based structure

- [ ] **Step 3: Commit**

```bash
cd native-sdk-experiment
git add src/tests.zig
git commit -m "test: update existing tests for chat-based model structure"
```

---

### Task 5: Add unread badge and chat switching scroll behavior

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing test for unread badge increment**

```zig
test "unread badge: increments for non-active chat on stream_done" {
    var model = main.initialModel();
    model.allocator = testing.allocator;
    var fx = noopFx(testing.allocator);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .new_chat, &fx);
    model.active_chat_idx = 1;

    model.chats[0].title = "First chat";
    model.chats[1].title = "Second chat";

    main.update(&model, .stream_done, &fx);
    try testing.expectEqual(@as(u32, 1), model.chats[0].unread_count);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL — unread_count not incremented

- [ ] **Step 3: Implement unread badge logic in stream_done**

Update `stream_done` in `update`:

```zig
        .stream_done => {
            model.streaming = false;
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (i != model.active_chat_idx and model.chats[i].msg_count > 0) {
                    model.chats[i].unread_count += 1;
                }
            }
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd native-sdk-experiment && native test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd native-sdk-experiment
git add src/main.zig src/tests.zig
git commit -m "feat: add unread badge increment on stream_done for non-active chats"
```

---

### Task 6: Final verification and smoke test

**Files:**
- All files

- [ ] **Step 1: Run native test**

Run: `cd native-sdk-experiment && native test`
Expected: PASS — all tests pass

- [ ] **Step 2: Run native check**

Run: `cd native-sdk-experiment && native check`
Expected: PASS — `src/app.native: ok`

- [ ] **Step 3: Run ruff on backend changes**

Run: `cd /Users/eddy/Developer/Python/assistant && uv run ruff check src/http/routers/conversation.py`
Expected: PASS

- [ ] **Step 4: Launch app smoke test**

Run: `cd native-sdk-experiment && native dev`
Expected: App window opens with sidebar (dark theme, teal accents), New chat button, search input, chat list, and empty state "How can I help?" message visible

- [ ] **Step 5: Commit final state**

```bash
cd native-sdk-experiment
git add -A
git commit -m "feat: complete Linear-like design system with theme, sidebar, chat panel"
```