const std = @import("std");
const runner = @import("runner");
const native_sdk = @import("native_sdk");

pub const panic = std.debug.FullPanic(native_sdk.debug.capturePanic);

const canvas = native_sdk.canvas;
const geometry = native_sdk.geometry;
const theme = @import("theme.zig");

const canvas_label = "main-canvas";
const window_width: f32 = 1200;
const window_height: f32 = 720;
const cancel_key: u64 = 2;
const approve_key: u64 = 3;
const reject_key: u64 = 4;
const history_key: u64 = 5;
const sessions_key: u64 = 6;
const delete_key: u64 = 7;
const title_key: u64 = 8;
const models_key: u64 = 9;

const ModelOption = struct {
    id: []const u8 = "",
    name: []const u8 = "",
    provider_display: []const u8 = "",
    key_source: []const u8 = "",
};

const max_models = 128;
const first_stream_key: u64 = 100;
pub const message_list_outer_padding: u32 = 0;

const app_permissions = [_][]const u8{ native_sdk.security.permission_command, native_sdk.security.permission_view };
const shell_views = [_]native_sdk.ShellView{
    .{ .label = canvas_label, .kind = .gpu_surface, .fill = true, .role = "Chat canvas", .accessibility_label = "Chat", .gpu_backend = .metal, .gpu_pixel_format = .bgra8_unorm, .gpu_present_mode = .timer, .gpu_alpha_mode = .@"opaque", .gpu_color_space = .srgb, .gpu_vsync = true },
};
const shell_windows = [_]native_sdk.ShellWindow{.{
    .label = "main",
    .title = "EA Chat",
    .width = window_width,
    .height = window_height,
    .restore_state = false,
    .views = &shell_views,
}};
const shell_scene: native_sdk.ShellConfig = .{ .windows = &shell_windows };

const max_messages = 200;
const max_chats = 50;

pub const ThemeMode = enum { dark, light };

pub const ChatMessage = struct {
    id: u64,
    role: []const u8,
    content: []const u8,
    tool_name: []const u8 = "",
    tool_status: []const u8 = "",
    tool_result: []const u8 = "",
    collapsed: bool = false,
    timestamp: []const u8 = "",

    pub fn isUser(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "user");
    }

    pub fn isAssistant(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "assistant");
    }

    pub fn isTool(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "tool");
    }

    pub fn isReasoning(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "reasoning");
    }

    pub fn isEmpty(self: *const ChatMessage) bool {
        return self.content.len == 0;
    }
};

pub const Chat = struct {
    id: u64,
    session_id: [32]u8 = undefined,
    session_id_len: usize = 0,
    title: []const u8 = "New chat",
    draft_text: []const u8 = "",
    _messages: [max_messages]ChatMessage = undefined,
    messages: []ChatMessage = &.{},
    msg_count: usize = 0,
    next_id: u64 = 1,
    unread_count: u32 = 0,
    history_loaded: bool = false,
    history_loading: bool = false,
    streaming: bool = false,
    fetch_key: u64 = 0,
    has_pending: bool = false,
    pending_tool: []const u8 = "",
    pending_call_id: []const u8 = "",
    open_bubble_type: []const u8 = "",
    status_text: []const u8 = "",
    draft_selection: canvas.TextSelection = .{ .anchor = 0, .focus = 0 },
    draft_selection_programmatic: bool = false,
    title_generated: bool = false,
    transcript_scroll_generation: u64 = 0,
    last_textarea_height: f32 = 0,

    pub fn hasUnread(self: *const Chat) bool {
        return self.unread_count > 0;
    }

    pub fn sessionId(self: *const Chat) []const u8 {
        return self.session_id[0..self.session_id_len];
    }

    pub fn isEmpty(self: *const Chat) bool {
        return self.msg_count == 0 and self.draft_text.len == 0;
    }

    pub fn setSessionId(self: *Chat, id: u64) void {
        const buf = std.fmt.bufPrint(&self.session_id, "chat-{d}", .{id}) catch return;
        self.session_id_len = buf.len;
    }

    pub fn setSessionIdStr(self: *Chat, sid: []const u8) void {
        if (sid.len > self.session_id.len) return;
        @memcpy(self.session_id[0..sid.len], sid);
        self.session_id_len = sid.len;
    }
};

pub const Msg = union(enum) {
    input_changed: canvas.TextInputEvent,
    send_message,
    cancel,
    approve,
    reject,
    stream_line: native_sdk.EffectLine,
    stream_done: native_sdk.EffectResponse,
    stream_error: []const u8,
    approve_done: native_sdk.EffectResponse,
    reject_done: native_sdk.EffectResponse,
    cancel_done: native_sdk.EffectResponse,
    new_chat,
    switch_chat: u64,
    delete_chat: u64,
    delete_chat_done: native_sdk.EffectResponse,
    title_generated: native_sdk.EffectResponse,
    models_loaded: native_sdk.EffectResponse,
    cycle_model,
    toggle_theme,
    toggle_bubble: u64,
    tick: native_sdk.EffectTimer,
    search_input: canvas.TextInputEvent,
    suggestion_inbox,
    suggestion_summary,
    suggestion_contacts,
    sidebar_resized: f32,
    history_loaded: native_sdk.EffectResponse,
    chat_history_loaded: native_sdk.EffectResponse,
    sessions_loaded: native_sdk.EffectResponse,
    reached_bottom,

    pub const view_unbound = .{
        "stream_line",
        "stream_done",
        "stream_error",
        "approve_done",
        "reject_done",
        "cancel_done",
        "toggle_bubble",
        "tick",
        "search_input",
        "history_loaded",
        "chat_history_loaded",
        "sessions_loaded",
        "reached_bottom",
        "delete_chat",
        "delete_chat_done",
        "title_generated",
        "models_loaded",
        "cycle_model",
    };
};

pub const Model = struct {
    theme_mode: ThemeMode = .dark,
    chats: [max_chats]Chat = undefined,
    chat_count: usize = 0,
    active_chat_idx: usize = 0,
    active_chat_id: u64 = 0,
    next_chat_id: u64 = 1,
    next_fetch_key: u64 = first_stream_key,
    pulse_phase: f32 = 0,
    search_query: []const u8 = "",
    sidebar_split: f32 = 0.2,
    available_models: [max_models]ModelOption = undefined,
    available_model_count: usize = 0,
    selected_model_idx: usize = 0,
    allocator: std.mem.Allocator = undefined,

    pub const view_unbound = .{
        "chat_count",
        "next_chat_id",
        "next_fetch_key",
        "search_query",
        "allocator",
        "theme_mode",
        "active_chat_idx",
        "chats",
    };

    pub fn activeChat(self: *Model) *Chat {
        return &self.chats[self.active_chat_idx];
    }

    pub fn inputText(self: *const Model) []const u8 {
        return self.chats[self.active_chat_idx].draft_text;
    }

    pub fn selectedModel(self: *const Model) []const u8 {
        if (self.available_model_count == 0) return "agnes:agnes-2.0-flash";
        return self.available_models[self.selected_model_idx].id;
    }

    pub fn selectedModelLabel(self: *const Model, allocator: std.mem.Allocator) []const u8 {
        if (self.available_model_count == 0) return "Agnes · Agnes 2.0 Flash · Hosted";
        const m = self.available_models[self.selected_model_idx];
        const source_label = if (std.mem.eql(u8, m.key_source, "hosted"))
            "Hosted"
        else if (std.mem.eql(u8, m.key_source, "user"))
            "Your key"
        else
            "Env";
        return std.fmt.allocPrint(allocator, "{s} · {s} · {s}", .{ m.provider_display, m.name, source_label }) catch m.name;
    }

    pub fn selectedModelIsHosted(self: *const Model) bool {
        if (self.available_model_count == 0) return true;
        return std.mem.eql(u8, self.available_models[self.selected_model_idx].key_source, "hosted");
    }

    pub fn messages(self: *const Model) []const ChatMessage {
        if (self.chat_count == 0) return &.{};
        return self.chats[self.active_chat_idx].messages;
    }

    pub fn filteredChats(self: *const Model) []const Chat {
        if (self.search_query.len == 0) return self.chats[0..self.chat_count];
        // Filter by title substring match using a static buffer.
        // Safe because buildView is single-threaded and the slice is only used during one render pass.
        const Filtered = struct {
            var buf: [max_chats]Chat = undefined;
        };
        var count: usize = 0;
        for (0..self.chat_count) |i| {
            if (std.mem.indexOf(u8, self.chats[i].title, self.search_query) != null) {
                Filtered.buf[count] = self.chats[i];
                count += 1;
            }
        }
        return Filtered.buf[0..count];
    }

    pub fn anyStreaming(self: *const Model) bool {
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            if (self.chats[i].streaming) return true;
        }
        return false;
    }

    pub fn activeStreaming(self: *const Model) bool {
        return self.chat_count > 0 and self.chats[self.active_chat_idx].streaming;
    }

    pub fn findChatByFetchKey(self: *Model, key: u64) ?*Chat {
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            if (self.chats[i].fetch_key == key and self.chats[i].streaming) {
                return &self.chats[i];
            }
        }
        return null;
    }

    pub fn findChatByHistoryKey(self: *Model, key: u64) ?*Chat {
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            if (self.chats[i].fetch_key == key) {
                return &self.chats[i];
            }
        }
        return null;
    }

    pub fn allocFetchKey(self: *Model) u64 {
        const k = self.next_fetch_key;
        self.next_fetch_key += 1;
        return k;
    }
};

pub const Effects = native_sdk.UiApp(Model, Msg).Effects;

fn tokensFn(model: *const Model) canvas.DesignTokens {
    return switch (model.theme_mode) {
        .dark => theme.darkTokens(),
        .light => theme.lightTokens(),
    };
}

pub fn update(model: *Model, msg: Msg, fx: *Effects) void {
    switch (msg) {
        .input_changed => |event| {
            const chat = model.activeChat();
            const extra = switch (event) {
                .insert_text => |text| text.len,
                .set_composition => |composition| composition.text.len,
                else => 0,
            };
            const output = model.allocator.alloc(u8, chat.draft_text.len + extra + 8) catch return;
            const next = (canvas.TextEditState{
                .text = chat.draft_text,
                .selection = chat.draft_selection,
            }).apply(event, output) catch return;
            chat.draft_text = next.text;
            chat.draft_selection = next.selection;
            chat.draft_selection_programmatic = (event == .set_selection);
            const new_line_count = std.mem.count(u8, chat.draft_text, "\n") + 1;
            const line_height: f32 = 20;
            const padding: f32 = 8;
            const max_lines = 8;
            const natural_height = @max(36, @as(f32, @floatFromInt(new_line_count)) * line_height + padding);
            const max_height = @as(f32, @floatFromInt(max_lines)) * line_height + padding;
            const new_height = @min(max_height, natural_height);
            if (new_height != chat.last_textarea_height) {
                chat.last_textarea_height = new_height;
                chat.transcript_scroll_generation += 1;
            }
        },
        .toggle_theme => {
            model.theme_mode = switch (model.theme_mode) {
                .dark => .light,
                .light => .dark,
            };
        },
        .new_chat => {
            // Smart new chat: if there's already an empty chat with no messages
            // AND its history has been loaded (so we know it's truly empty), switch to it
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].msg_count == 0 and model.chats[i].history_loaded) {
                    model.active_chat_idx = i;
                    model.active_chat_id = model.chats[i].id;
                    model.chats[i].unread_count = 0;
                    return;
                }
            }
            // No empty chat found — create a new one
            if (model.chat_count >= max_chats) return;
            model.chats[model.chat_count] = .{ .id = model.next_chat_id, .history_loaded = true };
            model.chats[model.chat_count].setSessionId(model.next_chat_id);
            model.next_chat_id += 1;
            model.active_chat_idx = model.chat_count;
            model.active_chat_id = model.chats[model.active_chat_idx].id;
            model.chat_count += 1;
        },
        .switch_chat => |chat_id| {
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].id == chat_id) {
                    model.active_chat_idx = i;
                    model.active_chat_id = model.chats[i].id;
                    model.chats[i].unread_count = 0;
                    // Load history for this chat if not yet loaded
                    if (!model.chats[i].history_loaded and model.chats[i].msg_count == 0) {
                        model.chats[i].history_loaded = true;
                        model.chats[i].history_loading = true;
                        const fetch_key = model.chats[i].id + 1000;
                        model.chats[i].fetch_key = fetch_key;
                        const url = std.fmt.allocPrint(
                            model.allocator,
                            "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat&session_id={s}&limit=100",
                            .{model.chats[i].sessionId()},
                        ) catch return;
                        fx.fetch(.{
                            .key = fetch_key,
                            .url = url,
                            .method = .GET,
                            .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                            .response = .buffered,
                            .on_response = Effects.responseMsg(.chat_history_loaded),
                        });
                    }
                    return;
                }
            }
        },
        .delete_chat => |chat_id| {
            // Don't delete if it's the only chat or if it's currently streaming
            if (model.chat_count <= 1) return;
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].id == chat_id) {
                    if (model.chats[i].streaming) return;
                    // Send DELETE to backend
                    const url = std.fmt.allocPrint(
                        model.allocator,
                        "http://127.0.0.1:8080/conversation/session?user_id=native_sdk_chat&session_id={s}",
                        .{model.chats[i].sessionId()},
                    ) catch return;
                    fx.fetch(.{
                        .key = delete_key,
                        .url = url,
                        .method = .DELETE,
                        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                        .response = .buffered,
                        .on_response = Effects.responseMsg(.delete_chat_done),
                    });
                    // Remove from array by shifting
                    var j = i;
                    while (j + 1 < model.chat_count) : (j += 1) {
                        model.chats[j] = model.chats[j + 1];
                    }
                    model.chat_count -= 1;
                    // Fix active index
                    if (model.active_chat_idx >= model.chat_count) {
                        model.active_chat_idx = model.chat_count - 1;
                    }
                    model.active_chat_id = model.chats[model.active_chat_idx].id;
                    return;
                }
            }
        },
        .delete_chat_done => {},
        .title_generated => |response| {
            if (response.outcome != .ok) return;
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const title_val = root.object.get("title") orelse return;
            const sid_val = root.object.get("session_id") orelse return;
            const title_str = switch (title_val) {
                .string => |s| s,
                else => return,
            };
            const sid_str = switch (sid_val) {
                .string => |s| s,
                else => return,
            };
            // Find the chat by session_id and update its title
            if (findChatBySessionId(model, sid_str)) |chat| {
                chat.title = model.allocator.dupe(u8, title_str) catch return;
                chat.title_generated = true;
            }
        },
        .models_loaded => |response| {
            if (response.outcome != .ok) return;
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const models_arr = root.object.get("models") orelse return;
            const arr = switch (models_arr) {
                .array => |a| a,
                else => return,
            };
            model.available_model_count = 0;
            for (arr.items) |item| {
                if (model.available_model_count >= max_models) break;
                const id_val = item.object.get("id") orelse continue;
                const name_val = item.object.get("name") orelse continue;
                const pd_val = item.object.get("provider_display") orelse continue;
                const key_source_val = item.object.get("key_source");
                const id_str = switch (id_val) { .string => |s| s, else => continue };
                const name_str = switch (name_val) { .string => |s| s, else => continue };
                const pd_str = switch (pd_val) { .string => |s| s, else => continue };
                const key_source_str = if (key_source_val) |v| switch (v) { .string => |s| s, else => "" } else "";
                model.available_models[model.available_model_count] = .{
                    .id = model.allocator.dupe(u8, id_str) catch continue,
                    .name = model.allocator.dupe(u8, name_str) catch continue,
                    .provider_display = model.allocator.dupe(u8, pd_str) catch continue,
                    .key_source = model.allocator.dupe(u8, key_source_str) catch continue,
                };
                model.available_model_count += 1;
            }
        },
        .cycle_model => {
            if (model.available_model_count > 0) {
                model.selected_model_idx = (model.selected_model_idx + 1) % model.available_model_count;
            }
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
        .suggestion_inbox => {
            model.activeChat().draft_text = "Triage my inbox";
        },
        .suggestion_summary => {
            model.activeChat().draft_text = "Draft a weekly summary";
        },
        .suggestion_contacts => {
            model.activeChat().draft_text = "Find contacts in marketing";
        },
        .sidebar_resized => |frac| {
            model.sidebar_split = frac;
        },
        .send_message => {
            const chat = model.activeChat();
            const text = std.mem.trim(u8, chat.draft_text, " ");
            if (text.len == 0 or chat.streaming) return;
            chat.transcript_scroll_generation += 1;
            addMessage(chat, model.allocator, "user", text);
            if (chat.msg_count == 1) {
                chat.title = model.allocator.dupe(u8, text) catch "New chat";
            }
            chat.draft_text = "";
            chat.streaming = true;
            chat.fetch_key = model.allocFetchKey();
            chat.open_bubble_type = "";
            chat.status_text = "Thinking...";
            // Create empty assistant bubble so "typing..." shows while waiting for first token
            addMessage(chat, model.allocator, "assistant", "");
            chat.open_bubble_type = "assistant";

            // Start pulse timer for streaming indicator
            fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });

            const escaped = escapeJsonString(model.allocator, text) catch return;
            const selected_model = model.selectedModel();
            const body = std.fmt.allocPrint(
                model.allocator,
                "{{\"message\":\"{s}\",\"user_id\":\"native_sdk_chat\",\"session_id\":\"{s}\",\"model\":\"{s}\"}}",
                .{ escaped, chat.sessionId(), selected_model },
            ) catch return;
            fx.fetch(.{
                .key = chat.fetch_key,
                .url = "http://127.0.0.1:8080/message/stream",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = body,
                .response = .stream,
                .on_line = Effects.lineMsg(.stream_line),
                .on_response = Effects.responseMsg(.stream_done),
            });
        },
        .cancel => {
            const chat = model.activeChat();
            if (!chat.streaming) return;
            chat.streaming = false;
            // Remove empty typing indicator if present
            if (chat.msg_count > 0) {
                const last = &chat._messages[chat.msg_count - 1];
                if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
                    chat.msg_count -= 1;
                    chat.messages = chat._messages[0..chat.msg_count];
                }
            }

            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"session_id\":\"{s}\"}}", .{chat.sessionId()}) catch return;
            fx.fetch(.{
                .key = cancel_key,
                .url = "http://127.0.0.1:8080/message/cancel",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.cancel_done),
            });
        },
        .approve => {
            const chat = model.activeChat();
            if (!chat.has_pending) return;
            chat.has_pending = false;
            chat.streaming = true;
            chat.fetch_key = model.allocFetchKey();
            chat.open_bubble_type = "";
            chat.status_text = "Resuming...";
            fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
            const selected_model = model.selectedModel();
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\",\"session_id\":\"{s}\",\"model\":\"{s}\"}}", .{ chat.pending_call_id, chat.sessionId(), selected_model }) catch return;
            chat.pending_tool = "";
            chat.pending_call_id = "";
            fx.fetch(.{
                .key = chat.fetch_key,
                .url = "http://127.0.0.1:8080/message/approve",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = body,
                .response = .stream,
                .on_line = Effects.lineMsg(.stream_line),
                .on_response = Effects.responseMsg(.approve_done),
            });
        },
        .reject => {
            const chat = model.activeChat();
            if (!chat.has_pending) return;
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\",\"session_id\":\"{s}\"}}", .{ chat.pending_call_id, chat.sessionId() }) catch return;
            chat.has_pending = false;
            chat.pending_tool = "";
            chat.pending_call_id = "";
            fx.fetch(.{
                .key = reject_key,
                .url = "http://127.0.0.1:8080/message/reject",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.reject_done),
            });
        },
        .stream_line => |line| {
            if (line.line.len == 0) return;
            // Route to the chat that owns this fetch key
            const chat = model.findChatByFetchKey(line.key) orelse return;
            const prefix = "data: ";
            if (!std.mem.startsWith(u8, line.line, prefix)) return;
            const body = line.line[prefix.len..];
            if (std.mem.eql(u8, body, "[DONE]")) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const event_type = root.object.get("type") orelse return;
            const data = root.object.get("data") orelse return;

            if (std.mem.eql(u8, event_type.string, "messages")) {
                const content = data.object.get("content") orelse return;
                if (!std.mem.eql(u8, chat.open_bubble_type, "assistant")) {
                    addMessage(chat, model.allocator, "assistant", trimLeadingMessageWhitespace(content.string));
                    chat.open_bubble_type = "assistant";
                } else {
                    appendToLastMessage(chat, model.allocator, "assistant", content.string);
                }
            } else if (std.mem.eql(u8, event_type.string, "reasoning")) {
                const content = data.object.get("content") orelse return;
                removeTrailingEmptyAssistant(chat);
                if (!std.mem.eql(u8, chat.open_bubble_type, "reasoning")) {
                    addMessage(chat, model.allocator, "reasoning", content.string);
                    chat.open_bubble_type = "reasoning";
                } else {
                    appendToLastMessage(chat, model.allocator, "reasoning", content.string);
                }
            } else if (std.mem.eql(u8, event_type.string, "tool_start")) {
                const tool = data.object.get("tool") orelse return;
                removeTrailingEmptyAssistant(chat);
                const args_val = data.object.get("args");
                const args_str = if (args_val) |v| blk: {
                    switch (v) {
                        .object => |obj| {
                            if (obj.count() == 0) break :blk "";
                            break :blk std.fmt.allocPrint(model.allocator, "{any}", .{v}) catch "";
                        },
                        .string => |s| break :blk s,
                        else => break :blk "",
                    }
                } else "";
                addToolMessage(chat, model.allocator, tool.string, args_str);
                chat.open_bubble_type = "tool";
                chat.status_text = std.fmt.allocPrint(model.allocator, "{s}: running...", .{tool.string}) catch tool.string;
            } else if (std.mem.eql(u8, event_type.string, "tool_result")) {
                const result = data.object.get("result") orelse return;
                const tool = data.object.get("tool") orelse return;
                if (findRunningToolBubble(chat)) |tb| {
                    tb.tool_status = model.allocator.dupe(u8, "done") catch return;
                    tb.tool_result = model.allocator.dupe(u8, result.string) catch return;
                    tb.collapsed = true; // collapsed by default — one-line preview
                }
                chat.status_text = std.fmt.allocPrint(model.allocator, "{s}: done", .{tool.string}) catch tool.string;
            } else if (std.mem.eql(u8, event_type.string, "interrupt")) {
                const tool = data.object.get("tool") orelse return;
                const call_id = data.object.get("call_id") orelse return;
                chat.has_pending = true;
                chat.pending_tool = model.allocator.dupe(u8, tool.string) catch return;
                chat.pending_call_id = model.allocator.dupe(u8, call_id.string) catch return;
            } else if (std.mem.eql(u8, event_type.string, "cancelled")) {
                chat.streaming = false;
                chat.open_bubble_type = "";
                chat.status_text = "";
                // Remove empty assistant typing indicator
                if (chat.msg_count > 0) {
                    const last = &chat._messages[chat.msg_count - 1];
                    if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
                        chat.msg_count -= 1;
                        chat.messages = chat._messages[0..chat.msg_count];
                    }
                }
            } else if (std.mem.eql(u8, event_type.string, "error")) {
                const content = data.object.get("content") orelse return;
                if (chat.open_bubble_type.len > 0 and chat.msg_count > 0) {
                    appendToLastMessage(chat, model.allocator, chat.open_bubble_type, content.string);
                } else {
                    addMessage(chat, model.allocator, "system", content.string);
                }
            }
        },
        .stream_done => |response| {
            const chat = model.findChatByFetchKey(response.key) orelse {
                // Fallback: finalize the active chat if it's streaming
                const ac = model.activeChat();
                if (ac.streaming) finalizeStream(ac);
                return;
            };
            // A3: Surface stream errors
            if (response.outcome != .ok) {
                const err_msg = std.fmt.allocPrint(model.allocator, "Stream error: {s}", .{@tagName(response.outcome)}) catch "Stream error";
                addMessage(chat, model.allocator, "system", err_msg);
                finalizeStream(chat);
                return;
            }
            finalizeStream(chat);
            queueHistoryFetch(model, chat, fx);
            // Mark this chat as unread if it's not the active one
            if (&model.chats[model.active_chat_idx] != chat) {
                chat.unread_count += 1;
            }
            // Generate title if this was the first exchange (exactly 1 user message)
            const user_count = countUserMessages(chat);
            if (user_count == 1 and chat.title.len >= 5) {
                const body = std.fmt.allocPrint(
                    model.allocator,
                    "{{\"user_id\":\"native_sdk_chat\",\"session_id\":\"{s}\"}}",
                    .{chat.sessionId()},
                ) catch return;
                fx.fetch(.{
                    .key = title_key,
                    .url = "http://127.0.0.1:8080/conversation/title",
                    .method = .POST,
                    .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                    .body = body,
                    .response = .buffered,
                    .on_response = Effects.responseMsg(.title_generated),
                });
            }
        },
        .stream_error => |err| {
            // Attach error to whichever chat is streaming (best-effort: active chat)
            const chat = model.activeChat();
            addMessage(chat, model.allocator, "system", err);
            chat.streaming = false;
        },
        .approve_done => |response| {
            const chat = model.findChatByFetchKey(response.key) orelse {
                const ac = model.activeChat();
                if (ac.streaming) finalizeStream(ac);
                return;
            };
            finalizeStream(chat);
        },
        .reject_done, .cancel_done => {},
        .reached_bottom => {},
        .toggle_bubble => |msg_id| {
            const chat = model.activeChat();
            var i: usize = 0;
            while (i < chat.msg_count) : (i += 1) {
                if (chat._messages[i].id == msg_id) {
                    chat._messages[i].collapsed = !chat._messages[i].collapsed;
                    return;
                }
            }
        },
        .tick => {
            // Advance pulse phase for streaming dot animation
            model.pulse_phase += 0.15;
            if (model.pulse_phase > std.math.tau) model.pulse_phase -= std.math.tau;
            // Reschedule timer if any chat is still streaming
            if (model.anyStreaming()) {
                fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
            }
        },
        .history_loaded => |response| {
            if (response.outcome != .ok) {
                // A3: surface backend connection error
                const chat = model.activeChat();
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Unable to connect to server. Is the backend running?");
                return;
            }
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const messages_arr = root.object.get("messages") orelse return;
            const arr = switch (messages_arr) {
                .array => |a| a,
                else => return,
            };
            const chat = model.findChatByHistoryKey(response.key) orelse model.activeChat();
            chat.history_loaded = true;
            chat.history_loading = false;
            chat.msg_count = 0;
            chat.messages = chat._messages[0..0];
            for (arr.items) |item| {
                addHistoryMessage(chat, model.allocator, item);
            }
            if (chat.msg_count > 0 and !chat.title_generated) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
            chat.fetch_key = 0;
        },
        .chat_history_loaded => |response| {
            if (response.outcome != .ok) {
                // A3: surface history fetch error
                const chat = model.findChatByHistoryKey(response.key) orelse return;
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Failed to load chat history.");
                return;
            }
            const body = response.body;
            if (body.len == 0) return;
            const chat = model.findChatByHistoryKey(response.key) orelse return;
            chat.history_loading = false;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const messages_arr = root.object.get("messages") orelse return;
            const arr = switch (messages_arr) {
                .array => |a| a,
                else => return,
            };
            chat.history_loaded = true;
            chat.msg_count = 0;
            chat.messages = chat._messages[0..0];
            for (arr.items) |item| {
                addHistoryMessage(chat, model.allocator, item);
            }
            if (chat.msg_count > 0 and !chat.title_generated) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
            chat.fetch_key = 0;
        },
        .sessions_loaded => |response| {
            if (response.outcome != .ok) {
                // A3: surface backend connection error
                const chat = model.activeChat();
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Unable to connect to server. Is the backend running?");
                return;
            }
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const sessions_arr = root.object.get("sessions") orelse return;
            const arr = switch (sessions_arr) {
                .array => |a| a,
                else => return,
            };

            // The initial chat (id=1, session "chat-1") is already in the model.
            // For each session from the API, create a chat entry with a unique id
            // derived from hashing the session_id string (avoids collisions between
            // sessions like "chat-1" and "sse-1" that would share numeric id 1).
            const initial_session_id = "chat-1";
            for (arr.items) |item| {
                const sid_val = item.object.get("session_id") orelse continue;
                const title_val = item.object.get("title") orelse continue;
                const sid = switch (sid_val) {
                    .string => |s| s,
                    else => continue,
                };
                const title = switch (title_val) {
                    .string => |s| s,
                    else => continue,
                };
                if (std.mem.eql(u8, sid, initial_session_id)) {
                    // Update the title of the initial chat
                    model.chats[0].title = model.allocator.dupe(u8, title) catch "New chat";
                    continue;
                }
                if (model.chat_count >= max_chats) break;

                // Use a hash of the session_id string as the unique chat id
                const hash = std.hash.Wyhash.hash(0, sid);
                model.chats[model.chat_count] = .{ .id = hash };
                model.chats[model.chat_count].setSessionIdStr(sid);
                model.chats[model.chat_count].title = model.allocator.dupe(u8, title) catch "New chat";
                model.chats[model.chat_count].history_loaded = false;
                model.chat_count += 1;
            }
            // Ensure next_chat_id won't collide with any existing chat-N session
            model.next_chat_id = model.chat_count + 100;
        },
    }
}

fn escapeJsonString(allocator: std.mem.Allocator, s: []const u8) ![]u8 {
    var extra: usize = 0;
    for (s) |c| {
        switch (c) {
            '"', '\\' => extra += 1,
            '\n', '\r', '\t' => extra += 1,
            0...8, 11, 12, 14...31 => extra += 5,
            else => {},
        }
    }
    if (extra == 0) return allocator.dupe(u8, s);
    const buf = try allocator.alloc(u8, s.len + extra);
    var i: usize = 0;
    for (s) |c| {
        switch (c) {
            '"' => { buf[i] = '\\'; buf[i + 1] = '"'; i += 2; },
            '\\' => { buf[i] = '\\'; buf[i + 1] = '\\'; i += 2; },
            '\n' => { buf[i] = '\\'; buf[i + 1] = 'n'; i += 2; },
            '\r' => { buf[i] = '\\'; buf[i + 1] = 'r'; i += 2; },
            '\t' => { buf[i] = '\\'; buf[i + 1] = 't'; i += 2; },
            0...8, 11, 12, 14...31 => {
                buf[i] = '\\';
                buf[i + 1] = 'u';
                buf[i + 2] = '0';
                buf[i + 3] = '0';
                const hex = "0123456789abcdef";
                buf[i + 4] = hex[c >> 4];
                buf[i + 5] = hex[c & 0xf];
                i += 6;
            },
            else => { buf[i] = c; i += 1; },
        }
    }
    return buf;
}

fn currentTimestamp(allocator: std.mem.Allocator) []const u8 {
    var ts: std.posix.timespec = undefined;
    switch (std.posix.errno(std.posix.system.clock_gettime(.REALTIME, &ts))) {
        .SUCCESS => {
            const total_secs: i64 = @intCast(ts.sec);
            const secs_per_day: i64 = 86400;
            const secs_per_hour: i64 = 3600;
            const secs_per_min: i64 = 60;
            // UTC time of day
            const day_secs = @mod(total_secs, secs_per_day);
            const hour: u8 = @intCast(@divTrunc(day_secs, secs_per_hour));
            const min: u8 = @intCast(@divTrunc(@mod(day_secs, secs_per_hour), secs_per_min));
            return std.fmt.allocPrint(allocator, "{d:0>2}:{d:0>2}", .{ hour, min }) catch "";
        },
        else => return "",
    }
}

fn removeTrailingEmptyAssistant(chat: *Chat) void {
    if (chat.msg_count == 0) return;
    const last = &chat._messages[chat.msg_count - 1];
    if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
        chat.msg_count -= 1;
        chat.messages = chat._messages[0..chat.msg_count];
    }
}

fn trimLeadingMessageWhitespace(content: []const u8) []const u8 {
    var start: usize = 0;
    while (start < content.len and (content[start] == ' ' or content[start] == '\n' or content[start] == '\r' or content[start] == '\t')) {
        start += 1;
    }
    return content[start..];
}

pub fn addMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
    if (chat.msg_count >= max_messages) return;
    chat._messages[chat.msg_count] = .{
        .id = chat.next_id,
        .role = allocator.dupe(u8, role) catch return,
        .content = allocator.dupe(u8, content) catch return,
        .timestamp = currentTimestamp(allocator),
    };
    chat.next_id += 1;
    chat.msg_count += 1;
    chat.messages = chat._messages[0..chat.msg_count];
}

fn addToolMessage(chat: *Chat, allocator: std.mem.Allocator, tool_name: []const u8, args: []const u8) void {
    if (chat.msg_count >= max_messages) return;
    const status_summary = std.fmt.allocPrint(allocator, "{s}({s})", .{ tool_name, args }) catch tool_name;
    chat._messages[chat.msg_count] = .{
        .id = chat.next_id,
        .role = allocator.dupe(u8, "tool") catch return,
        .content = status_summary,
        .tool_name = allocator.dupe(u8, tool_name) catch return,
        .tool_status = allocator.dupe(u8, "running") catch return,
    };
    chat.next_id += 1;
    chat.msg_count += 1;
    chat.messages = chat._messages[0..chat.msg_count];
}

fn findRunningToolBubble(chat: *Chat) ?*ChatMessage {
    var i: usize = chat.msg_count;
    while (i > 0) {
        i -= 1;
        const msg = &chat._messages[i];
        if (msg.isTool() and std.mem.eql(u8, msg.tool_status, "running")) {
            return msg;
        }
    }
    return null;
}

fn countUserMessages(chat: *const Chat) usize {
    var count: usize = 0;
    for (0..chat.msg_count) |i| {
        if (chat._messages[i].isUser()) count += 1;
    }
    return count;
}

fn findChatBySessionId(model: *Model, sid: []const u8) ?*Chat {
    var i: usize = 0;
    while (i < model.chat_count) : (i += 1) {
        if (std.mem.eql(u8, model.chats[i].sessionId(), sid)) {
            return &model.chats[i];
        }
    }
    return null;
}

fn finalizeStream(chat: *Chat) void {
    chat.streaming = false;
    chat.fetch_key = 0;
    chat.open_bubble_type = "";
    chat.status_text = "";
    // Remove empty assistant typing indicator
    if (chat.msg_count > 0) {
        const last = &chat._messages[chat.msg_count - 1];
        if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
            chat.msg_count -= 1;
            chat.messages = chat._messages[0..chat.msg_count];
        }
    }
    // Collapse all reasoning bubbles
    var i: usize = 0;
    while (i < chat.msg_count) : (i += 1) {
        if (chat._messages[i].isReasoning()) {
            chat._messages[i].collapsed = true;
        }
    }
}

fn queueHistoryFetch(model: *Model, chat: *Chat, fx: *Effects) void {
    const fetch_key = model.allocFetchKey();
    chat.fetch_key = fetch_key;
    chat.history_loading = true;
    const url = std.fmt.allocPrint(
        model.allocator,
        "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat&session_id={s}&limit=100",
        .{chat.sessionId()},
    ) catch return;
    fx.fetch(.{
        .key = fetch_key,
        .url = url,
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.chat_history_loaded),
    });
}

fn appendToLastMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
    if (chat.msg_count == 0) {
        addMessage(chat, allocator, role, content);
        return;
    }
    const last = &chat._messages[chat.msg_count - 1];
    if (!std.mem.eql(u8, last.role, role)) {
        addMessage(chat, allocator, role, content);
        return;
    }
    // If last message is empty, replace with first token (trimmed)
    if (last.content.len == 0) {
        last.content = allocator.dupe(u8, std.mem.trim(u8, content, " \n\r\t")) catch return;
        return;
    }
    const new_len = last.content.len + content.len;
    const buf = allocator.alloc(u8, new_len) catch return;
    @memcpy(buf[0..last.content.len], last.content);
    @memcpy(buf[last.content.len..], content);
    last.content = buf;
}

pub const AppUi = canvas.Ui(Msg);

const ChatApp = native_sdk.UiApp(Model, Msg);

// ── View builders (Zig view replacing markup) ──────────────────────────────

pub fn buildView(ui: *AppUi, model: *const Model) AppUi.Node {
    const split = ui.split(.{
        .value = model.sidebar_split,
        .on_resize = AppUi.valueMsg(.sidebar_resized),
        .style_tokens = .{ .background = .surface, .border_color = .border },
        .grow = 1,
    }, .{
        buildSidebar(ui, model),
        buildChatPanel(ui, model),
    });

    var root = ui.el(.card, .{
        .grow = 1,
        .style = .{ .radius = 0 },
        .style_tokens = .{ .background = .surface },
    }, .{split});
    root.widget.layout.padding = .{ .top = 0, .right = 0, .bottom = 0, .left = 0 };
    return root;
}

fn buildSidebar(ui: *AppUi, model: *const Model) AppUi.Node {
    // Top section: New chat button + search
    var top_nodes: [2]AppUi.Node = undefined;
    top_nodes[0] = ui.row(.{
        .on_press = .new_chat,
        .gap = 8,
        .padding = 12,
        .cross = .center,
        .grow = 1,
        .style_tokens = .{ .background = .surface_pressed, .radius = .md },
        .semantics = .{ .role = .button, .label = "New chat" },
    }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text } }, "plus"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text } }, "New chat"),
    });
    top_nodes[1] = ui.textField(.{
        .text = model.search_query,
        .placeholder = "Search chats...",
        .on_input = AppUi.inputMsg(.search_input),
        .semantics = .{ .label = "Search chats" },
        .style_tokens = .{ .background = .surface_subtle, .radius = .md },
    });

    // Chat list
    const chats = model.filteredChats();
    var chat_nodes: [max_chats]AppUi.Node = undefined;
    var chat_count: usize = 0;
    for (chats) |*chat| {
        const is_active = chat.id == model.active_chat_id;
        // Fixed-width dot slot (8px) so titles stay aligned regardless of indicator state.
        // Streaming → accent dot (pulsing), unread → solid accent dot, idle → empty space.
        const show_dot = chat.streaming or chat.hasUnread();
        // Pulsing opacity for streaming dots: 0.15 + 0.85 * (0.5 + 0.5 * cos(phase))
        const dot_opacity: f32 = if (chat.streaming)
            0.15 + 0.85 * (0.5 + 0.5 * @cos(model.pulse_phase))
        else
            1.0;
        // Streaming → accent dot (pulsing), unread → solid accent dot, idle → empty
        const dot_bg: canvas.ColorTokenName = .accent;
        const dot_node: AppUi.Node = if (show_dot) blk: {
            break :blk ui.el(.card, .{
                .width = 6,
                .height = 6,
                .key = .{ .int = 0xD07BA5E ^ chat.id },
                .opacity = dot_opacity,
                .style_tokens = .{
                    .background = dot_bg,
                    .radius = .sm,
                },
            }, .{
                ui.text(.{}, ""),
            });
        } else blk: {
            // Idle: empty 8px slot — fixed width, no background, no children
            break :blk ui.el(.stack, .{ .width = 8, .height = 8 }, .{});
        };
        const row_content = ui.row(.{
            .gap = 8,
            .cross = .center,
            .grow = 1,
        }, .{
            ui.row(.{ .width = 8, .height = 8, .cross = .center, .main = .center }, .{dot_node}),
            ui.text(.{
                .size = .sm,
                .grow = 1,
                .style_tokens = .{
                    .foreground = if (is_active) .text else .text_muted,
                },
            }, chat.title),
        });

        if (is_active) {
            // Active row: wrap in a card for visible pill background
            var pill = ui.el(.bubble, .{
                .grow = 1,
                .style_tokens = .{ .background = .surface_pressed, .radius = .md },
            }, .{
                ui.row(.{
                    .gap = 8,
                    .cross = .center,
                    .padding = 8,
                }, .{
                    ui.row(.{ .width = 8, .height = 8, .cross = .center, .main = .center }, .{dot_node}),
                    ui.text(.{
                        .size = .sm,
                        .grow = 1,
                        .style_tokens = .{
                            .foreground = .text,
                        },
                    }, chat.title),
                }),
            });
            pill.widget.layout.padding = .{ .top = 0, .right = 0, .bottom = 0, .left = 0 };
            // Wrap in a row with on_press + context_menu
            chat_nodes[chat_count] = ui.row(.{
                .on_press = .{ .switch_chat = chat.id },
                .context_menu = if (model.chat_count > 1)
                    &.{.{ .label = "Delete", .msg = .{ .delete_chat = chat.id } }}
                else
                    &.{},
                .semantics = .{ .role = .listitem, .label = chat.title },
            }, .{pill});
        } else {
            // Inactive row: plain row, no background
            var chat_row = ui.row(.{
                .gap = 8,
                .cross = .center,
                .on_press = .{ .switch_chat = chat.id },
                .context_menu = if (model.chat_count > 1)
                    &.{.{ .label = "Delete", .msg = .{ .delete_chat = chat.id } }}
                else
                    &.{},
                .semantics = .{ .role = .listitem, .label = chat.title },
            }, .{row_content});
            chat_row.widget.layout.padding = .{ .top = 8, .bottom = 8, .left = 12, .right = 12 };
            chat_nodes[chat_count] = chat_row;
        }
        chat_count += 1;
    }

    var sidebar_children: [5]AppUi.Node = undefined;
    var sidebar_count: usize = 0;

    // Top section
    const top_slice: []const AppUi.Node = top_nodes[0..2];
    sidebar_children[sidebar_count] = ui.column(.{ .padding = 12, .gap = 8, .style_tokens = .{ .background = .surface } }, top_slice);
    sidebar_count += 1;

    // Chat list scroll
    if (chat_count > 0) {
        const chat_slice: []const AppUi.Node = chat_nodes[0..chat_count];
        const inner_col = ui.column(.{ .gap = 2, .style_tokens = .{ .background = .surface } }, chat_slice);
        sidebar_children[sidebar_count] = ui.scroll(.{
            .grow = 1,
            .padding = 12,
            .gap = 2,
            .style_tokens = .{ .background = .surface },
        }, inner_col);
    } else {
        sidebar_children[sidebar_count] = ui.scroll(.{
            .grow = 1,
            .padding = 12,
            .gap = 2,
            .style_tokens = .{ .background = .surface },
        }, ui.row(.{ .gap = 8, .padding = 12, .cross = .center }, .{
            ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "circle-dot"),
            ui.text(.{ .size = .sm, .grow = 1, .style_tokens = .{ .foreground = .text_muted } }, "No chats found"),
        }));
    }
    sidebar_count += 1;

    // Bottom nav: Tools, Skills, Subagents
    var nav_nodes: [3]AppUi.Node = undefined;
    nav_nodes[0] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "wrench"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Tools"),
    });
    nav_nodes[1] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "file-text"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Skills"),
    });
    nav_nodes[2] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "git-branch"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Subagents"),
    });
    const nav_slice: []const AppUi.Node = nav_nodes[0..3];
    sidebar_children[sidebar_count] = ui.column(.{ .gap = 2, .padding = 12, .style_tokens = .{ .background = .surface } }, nav_slice);
    sidebar_count += 1;

    // Settings + theme toggle
    sidebar_children[sidebar_count] = ui.row(.{ .gap = 4, .padding = 12, .cross = .center, .style_tokens = .{ .background = .surface } }, .{
        ui.row(.{ .gap = 8, .padding = 8, .cross = .center, .grow = 1 }, .{
            ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "settings"),
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Settings"),
        }),
        ui.button(.{
            .on_press = .toggle_theme,
            .variant = .ghost,
            .size = .sm,
            .icon = switch (model.theme_mode) {
                .dark => "sun",
                .light => "moon",
            },
            .semantics = .{ .label = "Toggle theme" },
        }, ""),
    });
    sidebar_count += 1;

    const sidebar_slice: []const AppUi.Node = sidebar_children[0..sidebar_count];
    return ui.column(.{
        .style_tokens = .{ .background = .surface },
        .gap = 0,
        .min_width = 160,
    }, sidebar_slice);
}

fn groupExtentEstimate(context: ?*const anyopaque, index: u64) f32 {
    const chat: *const Chat = @ptrCast(@alignCast(@constCast(context)));
    const count = chat.msg_count;
    var group_idx: u64 = 0;
    var i: usize = 0;
    while (i < count) {
        const msg = &chat._messages[i];
        if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
            if (group_idx == index) {
                // Bubble padding (16) + text + timestamp (16.25) + group gap (8)
                return 40 + @as(f32, @floatFromInt(msg.content.len)) * 0.6;
            }
            group_idx += 1;
            i += 1;
        } else {
            var group_height: f32 = 28;
            while (i < count and !chat._messages[i].isUser() and !std.mem.eql(u8, chat._messages[i].role, "system")) : (i += 1) {
                const m = &chat._messages[i];
                if (m.isTool()) {
                    group_height += 60 + @as(f32, @floatFromInt(m.content.len)) * 0.3;
                } else if (m.isReasoning()) {
                    group_height += 48;
                } else {
                    // Bubble padding (16) + text + timestamp (16.25) + "Assistant" label (22.25) + group gap (8)
                    group_height += 62 + @as(f32, @floatFromInt(m.content.len)) * 0.6;
                }
            }
            if (group_idx == index) return group_height;
            group_idx += 1;
        }
    }
    return 80;
}

fn extractTimestamp(item: std.json.Value, allocator: std.mem.Allocator) []const u8 {
    const ts_val = item.object.get("timestamp") orelse return "";
    const ts_str = switch (ts_val) {
        .string => |s| s,
        else => return "",
    };
    // Extract HH:MM from ISO format (chars 11-15, i.e. ts_str[11..16])
    if (ts_str.len >= 16) {
        return allocator.dupe(u8, ts_str[11..16]) catch "";
    }
    return "";
}

fn addHistoryMessage(chat: *Chat, allocator: std.mem.Allocator, item: std.json.Value) void {
    const role_val = item.object.get("role") orelse return;
    const content_val = item.object.get("content") orelse return;
    const role_str = switch (role_val) {
        .string => |s| s,
        else => return,
    };
    const content_str = switch (content_val) {
        .string => |s| s,
        else => return,
    };
    if (std.mem.eql(u8, role_str, "tool")) {
        if (chat.msg_count >= max_messages) return;
        const metadata = item.object.get("metadata");
        const tool_name = if (metadata) |m| blk: {
            const name_val = m.object.get("tool_name") orelse m.object.get("name");
            if (name_val) |v| {
                switch (v) {
                    .string => |s| break :blk s,
                    else => {},
                }
            }
            break :blk "tool";
        } else "tool";
        chat._messages[chat.msg_count] = .{
            .id = chat.next_id,
            .role = allocator.dupe(u8, "tool") catch return,
            .content = allocator.dupe(u8, content_str) catch return,
            .tool_name = allocator.dupe(u8, tool_name) catch return,
            .tool_status = allocator.dupe(u8, "done") catch return,
            .tool_result = allocator.dupe(u8, content_str) catch return,
            .collapsed = true,
            .timestamp = extractTimestamp(item, allocator),
        };
        chat.next_id += 1;
        chat.msg_count += 1;
        chat.messages = chat._messages[0..chat.msg_count];
    } else if (std.mem.eql(u8, role_str, "reasoning")) {
        if (content_str.len == 0) return;
        addMessage(chat, allocator, role_str, content_str);
        chat._messages[chat.msg_count - 1].collapsed = true;
        chat._messages[chat.msg_count - 1].timestamp = extractTimestamp(item, allocator);
    } else {
        const trimmed = std.mem.trim(u8, content_str, " \n\r\t");
        if (trimmed.len == 0) return;
        addMessage(chat, allocator, role_str, trimmed);
        chat._messages[chat.msg_count - 1].timestamp = extractTimestamp(item, allocator);
    }
}

fn buildChatPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    const chat = &model.chats[model.active_chat_idx];
    const count = chat.msg_count;

    var children: [4]AppUi.Node = undefined;
    var child_count: usize = 0;

    // Message list or empty state
    if (count == 0) {
        if (chat.history_loading) {
            // A2: Loading indicator
            children[child_count] = ui.column(.{
                .grow = 1,
                .padding = 32,
                .gap = 16,
                .cross = .center,
                .main = .center,
                .style_tokens = .{ .background = .surface },
            }, .{
                ui.text(.{ .size = .heading, .style_tokens = .{ .foreground = .text_muted } }, "Loading..."),
            });
        } else {
            children[child_count] = ui.column(.{
                .grow = 1,
                .padding = 32,
                .gap = 16,
                .cross = .center,
                .main = .center,
                .style_tokens = .{ .background = .surface },
            }, .{
                ui.text(.{ .size = .heading }, "How can I help?"),
                ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Ask me anything, or try one of these:"),
                ui.row(.{ .gap = 8 }, .{
                    ui.button(.{ .on_press = .suggestion_inbox, .variant = .ghost }, "Triage my inbox"),
                    ui.button(.{ .on_press = .suggestion_summary, .variant = .ghost }, "Draft a weekly summary"),
                    ui.button(.{ .on_press = .suggestion_contacts, .variant = .ghost }, "Find contacts in marketing"),
                }),
            });
        }
    } else {
        // Group messages: user bubbles are standalone; consecutive assistant/tool/reasoning
        // messages are grouped under a single "Assistant" label.
        var group_count: usize = 0;
        var i: usize = 0;
        while (i < count) {
            const msg = &chat._messages[i];
            if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
                group_count += 1;
                i += 1;
            } else {
                group_count += 1;
                while (i < count and !chat._messages[i].isUser() and !std.mem.eql(u8, chat._messages[i].role, "system")) {
                    i += 1;
                }
            }
        }

        const list_id = std.fmt.allocPrint(
            ui.arena,
            "chat-messages-{d}-{d}",
            .{ chat.id, chat.transcript_scroll_generation },
        ) catch "chat-messages";
        const options = AppUi.VirtualListOptions{
            .id = list_id,
            .item_count = group_count,
            .item_extent = 0,
            .extent_estimate = groupExtentEstimate,
            .extent_context = chat,
            .gap = 8,
            .anchor = .trailing,
            .overscan = 3,
            .grow = 1,
            .padding = message_list_outer_padding,
            .style_tokens = .{ .background = .surface },
        };
        const window = ui.virtualWindow(options);

        // Build group nodes for visible range only.
        const max_visible = 64;
        var msg_nodes: [max_visible]AppUi.Node = undefined;
        var node_count: usize = 0;

        var group_idx: usize = 0;
        var msg_start: usize = 0;
        i = 0;
        while (i < count and node_count < max_visible) {
            const msg = &chat._messages[i];
            if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
                if (group_idx >= window.start_index and group_idx < window.end_index) {
                    msg_nodes[node_count] = buildMessageBubble(ui, msg);
                    node_count += 1;
                }
                group_idx += 1;
                msg_start = i + 1;
                i += 1;
            } else {
                msg_start = i;
                while (i < count and !chat._messages[i].isUser() and !std.mem.eql(u8, chat._messages[i].role, "system")) {
                    i += 1;
                }
                const msg_end = i;
                if (group_idx >= window.start_index and group_idx < window.end_index) {
                    msg_nodes[node_count] = buildAssistantGroup(ui, chat, msg_start, msg_end);
                    node_count += 1;
                }
                group_idx += 1;
            }
        }

        children[child_count] = ui.virtualList(options, window, msg_nodes[0..node_count]);
    }
    child_count += 1;

    // HITL bar (if pending for the active chat)
    const active_chat = &model.chats[model.active_chat_idx];
    if (active_chat.has_pending) {
        const approve_text = std.fmt.allocPrint(ui.arena, "Approve: {s}?", .{active_chat.pending_tool}) catch "Approve?";
        children[child_count] = ui.row(.{
            .gap = 12,
            .padding = 12,
            .cross = .center,
            .style_tokens = .{ .background = .surface, .radius = .md },
        }, .{
            ui.text(.{ .grow = 1 }, approve_text),
            ui.button(.{ .on_press = .approve, .style_tokens = .{ .foreground = .success } }, "Approve"),
            ui.button(.{ .on_press = .reject, .variant = .ghost, .style_tokens = .{ .foreground = .destructive } }, "Reject"),
        });
        child_count += 1;
    }

    // Composer: bordered container with textarea + model button + Send/Stop button
    const model_label = model.selectedModelLabel(ui.arena);
    const model_button: AppUi.Node = if (model.available_model_count > 0 and !active_chat.streaming)
        ui.button(.{ .on_press = .cycle_model, .variant = .ghost, .style_tokens = .{ .foreground = .text_muted } }, model_label)
    else if (model.available_model_count > 0)
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, model_label)
    else
        ui.text(.{}, "");

    const textarea_height: f32 = active_chat.last_textarea_height;

    const composer_textarea = blk: {
        var field = ui.el(.textarea, .{
            .text = model.inputText(),
            .placeholder = "Type a message... (Enter to send, Shift+Enter for newline)",
            .on_input = AppUi.inputMsg(.input_changed),
            .on_submit = .send_message,
            .semantics = .{ .label = "Message" },
            .height = textarea_height,
            .style_tokens = .{ .background = .surface_subtle, .border_color = .surface_subtle },
        }, .{});
        if (active_chat.draft_selection_programmatic) {
            field.widget.text_selection = active_chat.draft_selection;
        }
        field.widget.style.radius = 8;
        field.widget.style.focus_ring = canvas.Color.rgba8(0, 0, 0, 0);
        break :blk field;
    };

    const send_button: AppUi.Node = if (active_chat.streaming)
        ui.button(.{ .on_press = .cancel, .variant = .ghost }, "Stop")
    else
        ui.button(.{ .on_press = .send_message, .variant = .primary }, "Send");

    const hosted_button: AppUi.Node = if (model.selectedModelIsHosted())
        ui.button(.{ .on_press = .cycle_model, .variant = .secondary, .style_tokens = .{ .foreground = .text_muted } }, "Hosted — tap to change")
    else
        ui.text(.{}, "");

    const composer_bottom_row = ui.row(.{ .gap = 8, .cross = .center, .grow = 0 }, .{
        model_button,
        hosted_button,
        ui.spacer(1),
        send_button,
    });

    children[child_count] = ui.el(.card, .{
        .padding = 12,
        .height = textarea_height + 6 + 32 + 24,
        .style_tokens = .{ .background = .surface_subtle, .radius = .md },
    }, .{
        ui.column(.{ .gap = 6 }, .{
            composer_textarea,
            composer_bottom_row,
        }),
    });
    child_count += 1;

    const children_slice: []const AppUi.Node = children[0..child_count];
    return ui.column(.{
        .style_tokens = .{ .background = .surface },
        .gap = 8,
        .min_width = 320,
        .padding = 12,
    }, children_slice);
}

fn toolIconName(tool_name: []const u8) []const u8 {
    if (std.mem.startsWith(u8, tool_name, "web_")) return "search";
    if (std.mem.startsWith(u8, tool_name, "files_")) return "file-text";
    if (std.mem.startsWith(u8, tool_name, "email_")) return "send";
    if (std.mem.startsWith(u8, tool_name, "time_")) return "clock";
    if (std.mem.startsWith(u8, tool_name, "shell_")) return "terminal";
    if (std.mem.startsWith(u8, tool_name, "contacts_")) return "eye";
    if (std.mem.startsWith(u8, tool_name, "todos_")) return "check-circle";
    if (std.mem.startsWith(u8, tool_name, "memory_")) return "folder";
    if (std.mem.startsWith(u8, tool_name, "browser_")) return "search";
    return "wrench";
}

fn buildAssistantGroup(ui: *AppUi, chat: *const Chat, start: usize, end: usize) AppUi.Node {
    const len = end - start;
    const child_nodes = ui.arena.alloc(AppUi.Node, len) catch return ui.text(.{}, "");
    for (0..len) |j| {
        child_nodes[j] = buildChildBubble(ui, &chat._messages[start + j], chat.status_text);
    }
    return ui.column(.{ .gap = 6 }, .{
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, "Assistant"),
        ui.column(.{ .gap = 8 }, child_nodes),
    });
}

fn buildChildBubble(ui: *AppUi, msg: *const ChatMessage, status_text: []const u8) AppUi.Node {
    if (msg.isReasoning()) {
        if (msg.content.len == 0) return ui.text(.{}, "");
        const display_text: []const u8 = if (msg.collapsed) blk: {
            const newline = std.mem.indexOf(u8, msg.content, "\n");
            break :blk if (newline) |n| msg.content[0..n] else msg.content;
        } else msg.content;
        const expand_label: []const u8 = if (msg.collapsed) "Expand" else "Collapse";
        return ui.el(.bubble, .{
            .padding = 8,
            .style_tokens = .{ .background = .surface_subtle, .border_color = .border, .radius = .md },
        }, .{
            ui.row(.{ .gap = 8, .cross = .center, .on_press = .{ .toggle_bubble = msg.id } }, .{
                ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, "Thinking"),
                ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, expand_label),
            }),
            ui.text(.{ .wrap = true, .style_tokens = .{ .foreground = .text_muted } }, display_text),
        });
    } else if (msg.isTool()) {
        const icon = toolIconName(msg.tool_name);
        if (std.mem.eql(u8, msg.tool_status, "running")) {
            return ui.el(.bubble, .{
                .padding = 8,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border, .radius = .md },
            }, .{
                ui.column(.{ .gap = 4 }, .{
                    ui.row(.{ .gap = 8, .cross = .center }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, icon),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, msg.tool_name),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "running..."),
                    }),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, msg.content),
                }),
            });
        } else {
            const display_result: []const u8 = if (msg.collapsed) blk: {
                const newline = std.mem.indexOf(u8, msg.tool_result, "\n");
                break :blk if (newline) |n| msg.tool_result[0..n] else msg.tool_result;
            } else msg.tool_result;
            return ui.el(.bubble, .{
                .padding = 8,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border, .radius = .md },
            }, .{
                ui.column(.{ .gap = 4 }, .{
                    ui.row(.{ .gap = 8, .cross = .center, .on_press = .{ .toggle_bubble = msg.id } }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, icon),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, msg.tool_name),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, msg.tool_status),
                    }),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, display_result),
                }),
            });
        }
    } else {
        // Assistant text bubble
        if (msg.isEmpty()) {
            const typing_text: []const u8 = if (status_text.len > 0) status_text else "typing...";
            return ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, typing_text);
        } else {
            return ui.el(.bubble, .{
                .padding = 8,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border, .radius = .md },
            }, .{
                ui.column(.{ .gap = 0 }, .{
                    ui.text(.{ .wrap = true }, msg.content),
                    if (msg.timestamp.len > 0)
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, msg.timestamp)
                    else
                        ui.text(.{}, ""),
                }),
            });
        }
    }
}

fn buildMessageBubble(ui: *AppUi, msg: *const ChatMessage) AppUi.Node {
    if (msg.isUser()) {
        // User message: right-aligned, no role label
        const ts_node: AppUi.Node = if (msg.timestamp.len > 0)
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, msg.timestamp)
        else
            ui.text(.{}, "");
        return ui.row(.{
            .main = .end,
            .cross = .start,
        }, .{
            ui.el(.bubble, .{
                .padding = 8,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border, .radius = .md },
            }, .{
                ui.column(.{ .gap = 0 }, .{
                    ui.text(.{ .wrap = true }, msg.content),
                    ts_node,
                }),
            }),
        });
    } else if (std.mem.eql(u8, msg.role, "system")) {
        // System/error: muted, no card, no role label
        return ui.text(.{
            .size = .sm,
            .style_tokens = .{ .foreground = .text_muted },
        }, msg.content);
    } else {
        // Fallback for any standalone assistant/tool/reasoning (shouldn't normally happen)
        return buildChildBubble(ui, msg, "");
    }
}

pub fn initialModel() Model {
    var m: Model = .{};
    m.chats[0] = .{ .id = 1, .history_loaded = true };
    m.chats[0].setSessionId(1);
    m.chat_count = 1;
    m.active_chat_idx = 0;
    m.active_chat_id = 1;
    m.next_chat_id = 2;
    return m;
}

fn initFx(model: *Model, fx: *Effects) void {
    // Fetch all sessions to restore the sidebar
    fx.fetch(.{
        .key = sessions_key,
        .url = "http://127.0.0.1:8080/conversation/sessions?user_id=native_sdk_chat",
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.sessions_loaded),
    });
    // Fetch available models
    fx.fetch(.{
        .key = models_key,
        .url = "http://127.0.0.1:8080/models?user_id=native_sdk_chat",
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.models_loaded),
    });
    // Also load the active chat's history
    const chat = model.activeChat();
    chat.history_loaded = true;
    chat.history_loading = true;
    const init_fetch_key = chat.id + 1000;
    chat.fetch_key = init_fetch_key;
    const url = std.fmt.allocPrint(
        model.allocator,
        "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat&session_id={s}&limit=100",
        .{chat.sessionId()},
    ) catch return;
    fx.fetch(.{
        .key = init_fetch_key,
        .url = url,
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.history_loaded),
    });
}

pub fn main(init: std.process.Init) !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const allocator = arena.allocator();

    const app_state = try ChatApp.create(allocator, .{
        .name = "assistant",
        .scene = shell_scene,
        .canvas_label = canvas_label,
        .update_fx = update,
        .init_fx = initFx,
        .tokens_fn = tokensFn,
        .view = buildView,
    });
    app_state.model = initialModel();
    app_state.model.allocator = allocator;

    try runner.runWithOptions(app_state.app(), .{
        .app_name = "assistant",
        .window_title = "Assistant",
        .bundle_id = "dev.assistant.app",
        .icon_path = "assets/icon.png",
        .default_frame = geometry.RectF.init(0, 0, window_width, window_height),
        .restore_state = false,
        .js_window_api = false,
        .security = .{
            .permissions = &app_permissions,
            .navigation = .{ .allowed_origins = &.{ "zero://inline", "zero://app" } },
        },
    }, init);
}

test {
    _ = @import("tests.zig");
}
