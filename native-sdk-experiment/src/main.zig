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
const first_stream_key: u64 = 100;

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
    streaming: bool = false,
    fetch_key: u64 = 0,
    has_pending: bool = false,
    pending_tool: []const u8 = "",
    pending_call_id: []const u8 = "",
    open_bubble_type: []const u8 = "",
    status_text: []const u8 = "",

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
    toggle_theme,
    toggle_bubble: u64,
    search_input: canvas.TextInputEvent,
    suggestion_inbox,
    suggestion_summary,
    suggestion_contacts,
    sidebar_resized: f32,
    history_loaded: native_sdk.EffectResponse,
    chat_history_loaded: native_sdk.EffectResponse,
    reached_bottom,

    pub const view_unbound = .{
        "stream_line",
        "stream_done",
        "stream_error",
        "approve_done",
        "reject_done",
        "cancel_done",
        "toggle_bubble",
        "search_input",
        "history_loaded",
        "chat_history_loaded",
        "reached_bottom",
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
    search_query: []const u8 = "",
    sidebar_split: f32 = 0.2,
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

    pub fn messages(self: *const Model) []const ChatMessage {
        if (self.chat_count == 0) return &.{};
        return self.chats[self.active_chat_idx].messages;
    }

    pub fn filteredChats(self: *const Model) []const Chat {
        if (self.search_query.len == 0) return self.chats[0..self.chat_count];
        return self.chats[0..self.chat_count];
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
            switch (event) {
                .insert_text => |text| {
                    chat.draft_text = std.fmt.allocPrint(
                        model.allocator,
                        "{s}{s}",
                        .{ chat.draft_text, text },
                    ) catch return;
                },
                .clear => chat.draft_text = "",
                .delete_backward => {
                    if (chat.draft_text.len > 0) {
                        chat.draft_text = chat.draft_text[0 .. chat.draft_text.len - 1];
                    }
                },
                else => {},
            }
        },
        .toggle_theme => {
            model.theme_mode = switch (model.theme_mode) {
                .dark => .light,
                .light => .dark,
            };
        },
        .new_chat => {
            // Smart new chat: if there's already an empty chat with no messages, switch to it
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].msg_count == 0) {
                    model.active_chat_idx = i;
                    model.active_chat_id = model.chats[i].id;
                    model.chats[i].unread_count = 0;
                    return;
                }
            }
            // No empty chat found — create a new one
            if (model.chat_count >= max_chats) return;
            model.chats[model.chat_count] = .{ .id = model.next_chat_id };
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
                        const url = std.fmt.allocPrint(
                            model.allocator,
                            "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat&session_id={s}&limit=100",
                            .{model.chats[i].sessionId()},
                        ) catch return;
                        fx.fetch(.{
                            .key = history_key,
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
            addMessage(chat, model.allocator, "user", text);
            if (chat.msg_count == 1) {
                chat.title = model.allocator.dupe(u8, text) catch "New chat";
            }
            chat.draft_text = "";
            chat.streaming = true;
            chat.fetch_key = model.allocFetchKey();
            chat.open_bubble_type = "";
            chat.status_text = "Thinking...";

            const escaped = escapeJsonString(model.allocator, text) catch return;
            const body = std.fmt.allocPrint(
                model.allocator,
                "{{\"message\":\"{s}\",\"user_id\":\"native_sdk_chat\",\"session_id\":\"{s}\",\"model\":\"deepseek:deepseek-v4-flash\"}}",
                .{ escaped, chat.sessionId() },
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
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\",\"session_id\":\"{s}\",\"model\":\"deepseek:deepseek-v4-flash\"}}", .{chat.pending_call_id, chat.sessionId()}) catch return;
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
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\"}}", .{chat.pending_call_id}) catch return;
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
                    addMessage(chat, model.allocator, "assistant", content.string);
                    chat.open_bubble_type = "assistant";
                } else {
                    appendToLastMessage(chat, model.allocator, "assistant", content.string);
                }
            } else if (std.mem.eql(u8, event_type.string, "reasoning")) {
                const content = data.object.get("content") orelse return;
                if (!std.mem.eql(u8, chat.open_bubble_type, "reasoning")) {
                    addMessage(chat, model.allocator, "reasoning", content.string);
                    chat.open_bubble_type = "reasoning";
                } else {
                    appendToLastMessage(chat, model.allocator, "reasoning", content.string);
                }
            } else if (std.mem.eql(u8, event_type.string, "tool_start")) {
                const tool = data.object.get("tool") orelse return;
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
            finalizeStream(chat);
            // Increment unread for other chats
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (&model.chats[i] != chat and model.chats[i].msg_count > 0) {
                    model.chats[i].unread_count += 1;
                }
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
        .history_loaded => |response| {
            if (response.outcome != .ok) return;
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
            const chat = model.activeChat();
            chat.history_loaded = true;
            for (arr.items) |item| {
                const role_val = item.object.get("role") orelse continue;
                const content_val = item.object.get("content") orelse continue;
                const role_str = switch (role_val) {
                    .string => |s| s,
                    else => continue,
                };
                const content_str = switch (content_val) {
                    .string => |s| s,
                    else => continue,
                };
                if (std.mem.eql(u8, role_str, "tool")) {
                    // Tool messages from history: populate tool fields for rendering
                    if (chat.msg_count >= max_messages) continue;
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
                        .role = model.allocator.dupe(u8, "tool") catch continue,
                        .content = model.allocator.dupe(u8, content_str) catch continue,
                        .tool_name = model.allocator.dupe(u8, tool_name) catch continue,
                        .tool_status = model.allocator.dupe(u8, "done") catch continue,
                        .tool_result = model.allocator.dupe(u8, content_str) catch continue,
                    };
                    chat.next_id += 1;
                    chat.msg_count += 1;
                    chat.messages = chat._messages[0..chat.msg_count];
                } else {
                    addMessage(chat, model.allocator, role_str, content_str);
                }
            }
            if (chat.msg_count > 0) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
        },
        .chat_history_loaded => |response| {
            if (response.outcome != .ok) return;
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
            const chat = model.activeChat();
            for (arr.items) |item| {
                const role_val = item.object.get("role") orelse continue;
                const content_val = item.object.get("content") orelse continue;
                const role_str = switch (role_val) {
                    .string => |s| s,
                    else => continue,
                };
                const content_str = switch (content_val) {
                    .string => |s| s,
                    else => continue,
                };
                addMessage(chat, model.allocator, role_str, content_str);
            }
            if (chat.msg_count > 0) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
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

pub fn addMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
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
    // If last message is empty, replace with first token
    if (last.content.len == 0) {
        last.content = allocator.dupe(u8, content) catch return;
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
    return ui.split(.{
        .value = model.sidebar_split,
        .on_resize = AppUi.valueMsg(.sidebar_resized),
        .style_tokens = .{ .background = .surface, .border_color = .border },
    }, .{
        buildSidebar(ui, model),
        buildChatPanel(ui, model),
    });
}

fn buildSidebar(ui: *AppUi, model: *const Model) AppUi.Node {
    // Top section: New chat button + search
    var top_nodes: [2]AppUi.Node = undefined;
    top_nodes[0] = ui.button(.{
        .on_press = .new_chat,
        .variant = .secondary,
        .icon = "plus",
        .grow = 1,
    }, "New chat");
    top_nodes[1] = ui.textField(.{
        .text = model.search_query,
        .placeholder = "Search chats...",
        .on_input = AppUi.inputMsg(.search_input),
        .semantics = .{ .label = "Search chats" },
    });

    // Chat list
    const chats = model.filteredChats();
    var chat_nodes: [max_chats]AppUi.Node = undefined;
    var chat_count: usize = 0;
    for (chats) |*chat| {
        const is_active = chat.id == model.active_chat_id;
        // Fixed-width dot slot (8px) so titles stay aligned regardless of indicator state.
        // Streaming → accent dot, unread → accent dot, idle → empty space.
        const show_dot = chat.streaming or chat.hasUnread();
        // Fixed 8px slot keeps titles aligned with or without the dot.
        const dot_node: AppUi.Node = if (show_dot) blk: {
            break :blk ui.el(.card, .{
                .width = 6,
                .height = 6,
                .key = .{ .int = 0xD07BA5E ^ chat.id },
                .style_tokens = .{
                    .background = .accent,
                    .radius = .sm,
                },
            }, .{
                ui.text(.{}, ""),
            });
        } else blk: {
            break :blk ui.el(.card, .{
                .width = 6,
                .height = 6,
            }, .{
                ui.text(.{}, ""),
            });
        };
        chat_nodes[chat_count] = ui.row(.{
            .gap = 8,
            .padding = 8,
            .style_tokens = .{
                .background = if (is_active) .surface_pressed else null,
                .radius = .sm,
            },
            .cross = .center,
            .on_press = .{ .switch_chat = chat.id },
            .semantics = .{ .role = .listitem, .label = chat.title },
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
            .padding = 8,
            .gap = 2,
            .style_tokens = .{ .background = .surface },
        }, inner_col);
    } else {
        sidebar_children[sidebar_count] = ui.scroll(.{
            .grow = 1,
            .padding = 8,
            .gap = 2,
            .style_tokens = .{ .background = .surface },
        }, ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
            ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "circle-dot"),
            ui.text(.{ .size = .sm, .grow = 1, .style_tokens = .{ .foreground = .text_muted } }, "No chats found"),
        }));
    }
    sidebar_count += 1;

    // Bottom nav: Tools, Skills, Subagents
    var nav_nodes: [3]AppUi.Node = undefined;
    nav_nodes[0] = ui.row(.{ .gap = 8, .padding = 8, .style_tokens = .{ .radius = .sm }, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "wrench"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Tools"),
    });
    nav_nodes[1] = ui.row(.{ .gap = 8, .padding = 8, .style_tokens = .{ .radius = .sm }, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "file-text"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Skills"),
    });
    nav_nodes[2] = ui.row(.{ .gap = 8, .padding = 8, .style_tokens = .{ .radius = .sm }, .cross = .center }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "git-branch"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Subagents"),
    });
    const nav_slice: []const AppUi.Node = nav_nodes[0..3];
    sidebar_children[sidebar_count] = ui.column(.{ .gap = 2, .padding = 8, .style_tokens = .{ .background = .surface } }, nav_slice);
    sidebar_count += 1;

    // Settings + theme toggle
    sidebar_children[sidebar_count] = ui.row(.{ .gap = 4, .padding = 8, .cross = .center, .style_tokens = .{ .background = .surface } }, .{
        ui.row(.{ .gap = 8, .padding = 8, .style_tokens = .{ .radius = .sm }, .cross = .center, .grow = 1 }, .{
            ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "settings"),
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Settings"),
        }),
        ui.button(.{
            .on_press = .toggle_theme,
            .variant = .ghost,
            .size = .sm,
            .icon = "moon",
            .semantics = .{ .label = "Toggle theme" },
        }, ""),
    });
    sidebar_count += 1;

    const sidebar_slice: []const AppUi.Node = sidebar_children[0..sidebar_count];
    return ui.column(.{
        .style_tokens = .{ .background = .surface, .border_color = .border },
        .gap = 0,
        .min_width = 160,
    }, sidebar_slice);
}

fn buildChatPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    const chat = &model.chats[model.active_chat_idx];
    const count = chat.msg_count;

    var children: [5]AppUi.Node = undefined;
    var child_count: usize = 0;

    // Message list or empty state
    if (count == 0) {
        children[child_count] = ui.column(.{
            .grow = 1,
            .padding = 32,
            .gap = 16,
            .cross = .center,
            .main = .center,
            .style_tokens = .{ .background = .background },
        }, .{
            ui.text(.{ .size = .heading }, "How can I help?"),
            ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Ask me anything, or try one of these:"),
            ui.row(.{ .gap = 8 }, .{
                ui.button(.{ .on_press = .suggestion_inbox, .variant = .ghost }, "Triage my inbox"),
                ui.button(.{ .on_press = .suggestion_summary, .variant = .ghost }, "Draft a weekly summary"),
                ui.button(.{ .on_press = .suggestion_contacts, .variant = .ghost }, "Find contacts in marketing"),
            }),
        });
    } else {
        // Group messages: user bubbles are standalone; consecutive assistant/tool/reasoning
        // messages are grouped under a single "Assistant" label.
        // First, compute the group count for the virtual list.
        var group_count: usize = 0;
        var i: usize = 0;
        while (i < count) {
            const msg = &chat._messages[i];
            if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
                group_count += 1;
                i += 1;
            } else {
                // Skip all consecutive non-user, non-system messages as one group
                group_count += 1;
                while (i < count and !chat._messages[i].isUser() and !std.mem.eql(u8, chat._messages[i].role, "system")) {
                    i += 1;
                }
            }
        }

        const options = AppUi.VirtualListOptions{
            .id = "chat-messages",
            .item_count = group_count,
            .item_extent = 100,
            .gap = 12,
            .anchor = .trailing,
            .grow = 1,
            .padding = 16,
            .style_tokens = .{ .background = .background },
        };
        const window = ui.virtualWindow(options);

        // Build group nodes for visible range
        var msg_nodes: [max_messages]AppUi.Node = undefined;
        var node_count: usize = 0;

        // Map group indices to message ranges, then build only visible groups
        var group_idx: usize = 0;
        var msg_start: usize = 0;
        i = 0;
        while (i < count) {
            const msg = &chat._messages[i];
            if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
                // Single-message group
                if (group_idx >= window.start_index and group_idx < window.end_index) {
                    msg_nodes[node_count] = buildMessageBubble(ui, msg);
                    node_count += 1;
                }
                group_idx += 1;
                msg_start = i + 1;
                i += 1;
            } else {
                // Find end of this assistant group
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

    // Status bar (only when streaming)
    if (active_chat.streaming and active_chat.status_text.len > 0) {
        children[child_count] = ui.row(.{
            .gap = 8,
            .padding = 8,
            .cross = .center,
            .style_tokens = .{ .background = .surface_subtle },
        }, .{
            ui.icon(.{ .style_tokens = .{ .foreground = .accent } }, "circle-dot"),
            ui.text(.{ .size = .sm, .grow = 1, .style_tokens = .{ .foreground = .text_muted } }, active_chat.status_text),
        });
        child_count += 1;
    }

    // Composer
    if (active_chat.streaming) {
        children[child_count] = ui.row(.{
            .gap = 8,
            .padding = 12,
            .cross = .center,
            .style_tokens = .{ .background = .background },
        }, .{
            ui.textField(.{
                .text = model.inputText(),
                .placeholder = "Type a message...",
                .grow = 1,
                .on_input = AppUi.inputMsg(.input_changed),
                .on_submit = .send_message,
                .semantics = .{ .label = "Message" },
            }),
            ui.button(.{ .on_press = .cancel, .variant = .ghost }, "Stop"),
        });
    } else {
        children[child_count] = ui.row(.{
            .gap = 8,
            .padding = 12,
            .cross = .center,
            .style_tokens = .{ .background = .background },
        }, .{
            ui.textField(.{
                .text = model.inputText(),
                .placeholder = "Type a message...",
                .grow = 1,
                .on_input = AppUi.inputMsg(.input_changed),
                .on_submit = .send_message,
                .semantics = .{ .label = "Message" },
            }),
            ui.button(.{ .on_press = .send_message, .variant = .primary }, "Send"),
        });
    }
    child_count += 1;

    const children_slice: []const AppUi.Node = children[0..child_count];
    return ui.column(.{
        .style_tokens = .{ .background = .background },
        .gap = 0,
        .min_width = 320,
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
    var child_nodes: [max_messages]AppUi.Node = undefined;
    var child_count: usize = 0;
    var i = start;
    while (i < end) : (i += 1) {
        child_nodes[child_count] = buildChildBubble(ui, &chat._messages[i]);
        child_count += 1;
    }
    return ui.column(.{ .gap = 6 }, .{
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, "Assistant"),
        ui.column(.{ .gap = 8 }, child_nodes[0..child_count]),
    });
}

fn buildChildBubble(ui: *AppUi, msg: *const ChatMessage) AppUi.Node {
    if (msg.isReasoning()) {
        const display_text: []const u8 = if (msg.collapsed) blk: {
            const newline = std.mem.indexOf(u8, msg.content, "\n");
            break :blk if (newline) |n| msg.content[0..n] else msg.content;
        } else msg.content;
        const expand_label: []const u8 = if (msg.collapsed) "Expand" else "Collapse";
        return ui.el(.card, .{
            .padding = 12,
            .style_tokens = .{ .background = .surface, .radius = .md, .border_color = .accent },
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
            return ui.el(.card, .{
                .padding = 12,
                .style_tokens = .{ .background = .surface_subtle, .radius = .md, .border_color = .border },
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
            return ui.el(.card, .{
                .padding = 12,
                .style_tokens = .{ .background = .surface_subtle, .radius = .md, .border_color = .border },
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
            return ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "typing...");
        } else {
            return ui.el(.card, .{
                .padding = 12,
                .style_tokens = .{ .background = .surface, .radius = .lg, .border_color = .border },
            }, .{
                ui.text(.{ .wrap = true }, msg.content),
            });
        }
    }
}

fn buildMessageBubble(ui: *AppUi, msg: *const ChatMessage) AppUi.Node {
    if (msg.isUser()) {
        // User message: right-aligned, no role label
        return ui.row(.{
            .main = .end,
            .cross = .start,
        }, .{
            ui.el(.card, .{
                .padding = 12,
                .style_tokens = .{ .background = .surface_subtle, .radius = .lg },
            }, .{
                ui.text(.{ .wrap = true }, msg.content),
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
        return buildChildBubble(ui, msg);
    }
}

pub fn initialModel() Model {
    var m: Model = .{};
    m.chats[0] = .{ .id = 1 };
    m.chats[0].setSessionId(1);
    m.chat_count = 1;
    m.active_chat_idx = 0;
    m.active_chat_id = 1;
    m.next_chat_id = 2;
    return m;
}

fn initFx(model: *Model, fx: *Effects) void {
    const chat = model.activeChat();
    const url = std.fmt.allocPrint(
        model.allocator,
        "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat&session_id={s}&limit=100",
        .{chat.sessionId()},
    ) catch return;
    fx.fetch(.{
        .key = history_key,
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
