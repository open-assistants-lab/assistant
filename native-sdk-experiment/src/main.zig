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
const stream_key: u64 = 1;
const cancel_key: u64 = 2;
const approve_key: u64 = 3;
const reject_key: u64 = 4;

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

    pub fn isUser(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "user");
    }

    pub fn isAssistant(self: *const ChatMessage) bool {
        return std.mem.eql(u8, self.role, "assistant");
    }
};

pub const Chat = struct {
    id: u64,
    title: []const u8 = "New chat",
    _messages: [max_messages]ChatMessage = undefined,
    messages: []ChatMessage = &.{},
    msg_count: usize = 0,
    next_id: u64 = 1,
    unread_count: u32 = 0,

    pub fn hasUnread(self: *const Chat) bool {
        return self.unread_count > 0;
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
    search_input: canvas.TextInputEvent,
    suggestion_inbox,
    suggestion_summary,
    suggestion_contacts,
    sidebar_resized: f32,

    pub const view_unbound = .{
        "stream_line",
        "stream_done",
        "stream_error",
        "approve_done",
        "reject_done",
        "cancel_done",
        "search_input",
    };
};

pub const Model = struct {
    theme_mode: ThemeMode = .dark,
    chats: [max_chats]Chat = undefined,
    chat_count: usize = 0,
    active_chat_idx: usize = 0,
    active_chat_id: u64 = 0,
    next_chat_id: u64 = 1,
    search_query: []const u8 = "",
    input_text: []const u8 = "",
    streaming: bool = false,
    has_pending: bool = false,
    pending_tool: []const u8 = "",
    pending_call_id: []const u8 = "",
    sidebar_split: f32 = 0.2,
    allocator: std.mem.Allocator = undefined,

    pub const view_unbound = .{
        "chat_count",
        "next_chat_id",
        "search_query",
        "pending_call_id",
        "streaming",
        "allocator",
        "theme_mode",
        "active_chat_idx",
        "chats",
    };

    pub fn activeChat(self: *Model) *Chat {
        return &self.chats[self.active_chat_idx];
    }

    pub fn messages(self: *const Model) []const ChatMessage {
        if (self.chat_count == 0) return &.{};
        return self.chats[self.active_chat_idx].messages;
    }

    pub fn filteredChats(self: *const Model) []const Chat {
        if (self.search_query.len == 0) return self.chats[0..self.chat_count];
        return self.chats[0..self.chat_count];
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
            switch (event) {
                .insert_text => |text| {
                    model.input_text = std.fmt.allocPrint(
                        model.allocator,
                        "{s}{s}",
                        .{ model.input_text, text },
                    ) catch return;
                },
                .clear => model.input_text = "",
                .delete_backward => {
                    if (model.input_text.len > 0) {
                        model.input_text = model.input_text[0 .. model.input_text.len - 1];
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
            if (model.chat_count >= max_chats) return;
            model.chats[model.chat_count] = .{ .id = model.next_chat_id };
            model.next_chat_id += 1;
            model.active_chat_idx = model.chat_count;
            model.active_chat_id = model.chats[model.active_chat_idx].id;
            model.chat_count += 1;
            model.input_text = "";
        },
        .switch_chat => |chat_id| {
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].id == chat_id) {
                    model.active_chat_idx = i;
                    model.active_chat_id = model.chats[i].id;
                    model.chats[i].unread_count = 0;
                    model.input_text = "";
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
            model.input_text = "Triage my inbox";
        },
        .suggestion_summary => {
            model.input_text = "Draft a weekly summary";
        },
        .suggestion_contacts => {
            model.input_text = "Find contacts in marketing";
        },
        .sidebar_resized => |frac| {
            model.sidebar_split = frac;
        },
        .send_message => {
            const text = std.mem.trim(u8, model.input_text, " ");
            if (text.len == 0 or model.streaming) return;
            const chat = model.activeChat();
            addMessage(chat, model.allocator, "user", text);
            if (chat.msg_count == 1) {
                chat.title = model.allocator.dupe(u8, text) catch "New chat";
            }
            model.input_text = "";
            model.streaming = true;

            const escaped = escapeJsonString(model.allocator, text) catch return;
            const body = std.fmt.allocPrint(
                model.allocator,
                "{{\"message\":\"{s}\",\"user_id\":\"native_sdk_chat\",\"model\":\"deepseek:deepseek-v4-flash\"}}",
                .{escaped},
            ) catch return;
            fx.fetch(.{
                .key = stream_key,
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
            model.streaming = false;
            fx.fetch(.{
                .key = cancel_key,
                .url = "http://127.0.0.1:8080/message/cancel",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = "{\"user_id\":\"native_sdk_chat\"}",
                .response = .buffered,
                .on_response = Effects.responseMsg(.cancel_done),
            });
        },
        .approve => {
            if (!model.has_pending) return;
            model.has_pending = false;
            model.streaming = true;
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\"}}", .{model.pending_call_id}) catch return;
            fx.fetch(.{
                .key = approve_key,
                .url = "http://127.0.0.1:8080/message/approve",
                .method = .POST,
                .headers = &.{.{
                    .name = "Content-Type",
                    .value = "application/json",
                }},
                .body = body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.approve_done),
            });
        },
        .reject => {
            if (!model.has_pending) return;
            const body = std.fmt.allocPrint(model.allocator, "{{\"user_id\":\"native_sdk_chat\",\"call_id\":\"{s}\"}}", .{model.pending_call_id}) catch return;
            model.has_pending = false;
            model.pending_tool = "";
            model.pending_call_id = "";
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
        .stream_done => {
            model.streaming = false;
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (i != model.active_chat_idx and model.chats[i].msg_count > 0) {
                    model.chats[i].unread_count += 1;
                }
            }
        },
        .stream_error => |err| {
            addMessage(model.activeChat(), model.allocator, "system", err);
            model.streaming = false;
        },
        .approve_done, .reject_done, .cancel_done => {},
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

pub const AppUi = canvas.Ui(Msg);
pub const app_markup = @embedFile("app.native");

const ChatApp = native_sdk.UiApp(Model, Msg);

pub fn initialModel() Model {
    var m: Model = .{};
    m.chats[0] = .{ .id = 1 };
    m.chat_count = 1;
    m.active_chat_idx = 0;
    m.active_chat_id = 1;
    m.next_chat_id = 2;
    return m;
}

pub fn main(init: std.process.Init) !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const allocator = arena.allocator();

    const app_state = try ChatApp.create(allocator, .{
        .name = "native-sdk-experiment",
        .scene = shell_scene,
        .canvas_label = canvas_label,
        .update_fx = update,
        .tokens_fn = tokensFn,
        .markup = .{ .source = app_markup, .watch_path = "src/app.native", .io = init.io },
    });
    app_state.model = initialModel();
    app_state.model.allocator = allocator;

    try runner.runWithOptions(app_state.app(), .{
        .app_name = "native-sdk-experiment",
        .window_title = "EA Chat",
        .bundle_id = "dev.native_sdk.native-sdk-experiment",
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
