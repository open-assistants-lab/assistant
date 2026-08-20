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
const settings_key: u64 = 10;
const settings_general_key: u64 = 11;
const grader_prompt_key: u64 = 12;

const max_providers = 128;
const max_provider_models = 512;
const max_pending_key_deletes = 16;

const KeyDeletePending = struct {
    key: u64 = 0,
    provider_id: []const u8 = "",
};

const ProviderInfo = struct {
    id: []const u8 = "",
    name: []const u8 = "",
    has_key: bool = false,
    via_env: bool = false,
    key_source: []const u8 = "none",
    key_input: []const u8 = "",
    key_visible: bool = false,
    adding_key: bool = false,
    testing: bool = false,
    test_error: []const u8 = "",
    model_indices: [max_provider_models]usize = undefined,
    model_count: usize = 0,
};

const SettingsSection = enum { providers_models, general };

const ToolsSection = enum { builtin, connections };

const ToolsState = struct {
    visible: bool = false,
    section: ToolsSection = .builtin,
    loading: bool = false,
};

/// Which model role the catalog picker is currently editing.
const ModelRole = enum { agent, grader, title, summarization };

const SettingsState = struct {
    visible: bool = false,
    section: SettingsSection = .providers_models,
    model_role: ModelRole = .agent,
    loading: bool = false,
    search_text: []const u8 = "",
    search_selection: canvas.TextSelection = .{ .anchor = 0, .focus = 0 },
    search_selection_programmatic: bool = false,
    search_runtime_text_synced: bool = false,
    default_model_id: []const u8 = "",
    grader_model_id: []const u8 = "",
    title_model_id: []const u8 = "",
    summarization_model_id: []const u8 = "",
    providers: [max_providers]ProviderInfo = undefined,
    provider_count: usize = 0,
    selected_model_idx: usize = 0,
    saving_model: bool = false,
    model_error: []const u8 = "",
    key_modal_visible: bool = false,
    pending_provider_idx: usize = 0,
    pending_provider_id: []const u8 = "",
    pending_model_idx: usize = 0,
    pending_model_id: []const u8 = "",
    pending_model_name: []const u8 = "",
    key_input: []const u8 = "",
    key_visible: bool = false,
    key_error: []const u8 = "",
    key_testing: bool = false,
    pending_key_deletes: [max_pending_key_deletes]KeyDeletePending = [_]KeyDeletePending{.{}} ** max_pending_key_deletes,
    pending_key_delete_count: usize = 0,
    rubric_enabled: bool = false,
    rubric_max_iterations: u32 = 3,
    grader_prompt: []const u8 = "",
    grader_prompt_revision: u32 = 0,
    grader_prompt_loading: bool = false,
    saving_general: bool = false,
    reduced_motion: bool = false,
};

const ModelOption = struct {
    id: []const u8 = "",
    name: []const u8 = "",
    provider: []const u8 = "",
    provider_display: []const u8 = "",
    key_source: []const u8 = "",
};

const max_models = 8192;
const max_visible_settings_rows = 120;
const first_stream_key: u64 = 100;
pub const message_list_outer_padding: u32 = 0;

/// Stream watchdog deadline: the fetch timeout is 120s; if a chat is still
/// streaming this long after it started, the terminal event was lost and the
/// UI force-finalizes (see the .tick handler).
pub const stream_watchdog_ms: i64 = 130_000;

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

/// Initial per-chat message capacity. The buffer grows on demand via
/// `ensureMessageCapacity`, so long transcripts (stress tests, deep history)
/// are not truncated at this number.
pub const default_message_capacity = 200;
const max_chats = 100;

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

pub const ContextInfo = struct {
    model: []const u8 = "",
    input_tokens: u32 = 0,
    output_tokens: u32 = 0,
    context_window: u32 = 0,
    context_percentage: f32 = 0,
    freshness: []const u8 = "live",
};

pub const Chat = struct {
    id: u64,
    session_id: [32]u8 = undefined,
    session_id_len: usize = 0,
    title: []const u8 = "New chat",
    draft_text: []const u8 = "",
    _messages: []ChatMessage = &.{},
    _message_capacity: usize = 0,
    messages: []ChatMessage = &.{},
    msg_count: usize = 0,
    next_id: u64 = 1,
    unread_count: u32 = 0,
    history_loaded: bool = false,
    history_loading: bool = false,
    streaming: bool = false,
    stream_started_at: i64 = 0,
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
    last_textarea_height: f32 = 36,
    created_at: []const u8 = "",
    rubric_attempts: u32 = 0,
    compression_animation_ticks: u32 = 0,
    context_info: ContextInfo = .{},
    // Cached group extents for the virtual list. Recomputed when messages change.
    // group_extents[i] = height of group i. group_count = number of groups.
    _group_extents: []f32 = &.{},
    _group_extents_count: usize = 0,
    _group_extents_msg_count: usize = 0,  // msg_count when cache was built
    _group_extents_scroll_gen: u64 = 0,   // scroll_gen when cache was built

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
    retry,
    cancel,
    toggle_model_menu,
    close_model_menu,
    model_menu_search_input: canvas.TextInputEvent,
    model_menu_select: usize,
    model_menu_manage,
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
    open_settings,
    close_settings,
    open_tools,
    close_tools,
    tools_tab_builtin,
    tools_tab_connections,
    settings_providers_models,
    settings_general,
    settings_loaded: native_sdk.EffectResponse,
    settings_general_loaded: native_sdk.EffectResponse,
    grader_prompt_loaded: native_sdk.EffectResponse,
    settings_general_saved: native_sdk.EffectResponse,
    grader_prompt_saved: native_sdk.EffectResponse,
    settings_search: canvas.TextInputEvent,
    set_model_role: ModelRole,
    select_model: usize,
    model_selected: native_sdk.EffectResponse,
    add_key_expand: usize,
    add_key_cancel: usize,
    add_key_input: canvas.TextInputEvent,
    toggle_key_visibility: usize,
    add_key_submit: usize,
    key_tested: native_sdk.EffectResponse,
    key_saved: native_sdk.EffectResponse,
    key_deleted: native_sdk.EffectResponse,
    remove_key: usize,
    toggle_rubric,
    toggle_reduced_motion,
    rubric_iterations_increment,
    rubric_iterations_decrement,
    save_general_settings,
    grader_prompt_input: canvas.TextInputEvent,

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
        "settings_general_loaded",
        "grader_prompt_loaded",
        "settings_general_saved",
        "grader_prompt_saved",
    };
};

pub const Model = struct {
    theme_mode: ThemeMode = .dark,
    /// Stress-test mode: seeded synthetic transcript, no backend fetches.
    stress_mode: bool = false,
    chats: [max_chats]Chat = undefined,
    chat_count: usize = 0,
    active_chat_idx: usize = 0,
    active_chat_id: u64 = 0,
    next_chat_id: u64 = 1,
    next_fetch_key: u64 = first_stream_key,
    pulse_phase: f32 = 0,
    // Entrance animation progress (0..1, 1 = settled). Advanced by the tick
    // timer; the view maps each to opacity/transform. Reset to 0 when the
    // surface (re)appears so it fades in instead of teleporting.
    settings_entrance: f32 = 1,
    hitl_entrance: f32 = 1,
    empty_entrance: f32 = 1,
    composer_entrance: f32 = 0,
    search_query: []const u8 = "",
    sidebar_split: f32 = 0.2,
    available_models: [max_models]ModelOption = undefined,
    available_model_count: usize = 0,
    selected_model_idx: usize = 0,
    model_menu_open: bool = false,
    model_menu_search: []const u8 = "",
    model_menu_search_selection: canvas.TextSelection = .{ .anchor = 0, .focus = 0 },
    settings: SettingsState = .{},
    tools: ToolsState = .{},
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
        if (self.available_model_count == 0) return "ollama-cloud:deepseek-v4-flash:0731";
        return self.available_models[self.selected_model_idx].id;
    }

    pub fn selectedModelLabel(self: *const Model, allocator: std.mem.Allocator) []const u8 {
        if (self.available_model_count == 0) return "Ollama Cloud · DeepSeek V4 Flash 0731";
        const m = self.available_models[self.selected_model_idx];
        return std.fmt.allocPrint(allocator, "{s} · {s}", .{ m.provider_display, m.name }) catch m.name;
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

    pub fn anyCompressionAnimation(self: *const Model) bool {
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            if (self.chats[i].compression_animation_ticks > 0) return true;
        }
        return false;
    }

    pub fn activeStreaming(self: *const Model) bool {
        return self.chat_count > 0 and self.chats[self.active_chat_idx].streaming;
    }

    pub fn findChatByFetchKey(self: *Model, key: u64) ?*Chat {
        var i: usize = 0;
        while (i < self.chat_count) : (i += 1) {
            // No `streaming` check: the terminal event must always find its
            // chat so finalizeStream runs, even if the flag was already
            // cleared by a race. Fetch keys are unique and monotonically
            // increasing, so a stale key can never match a new fetch.
            if (self.chats[i].fetch_key == key) {
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

fn jsonString(v: std.json.Value) ?[]const u8 {
    return switch (v) {
        .string => |s| s,
        else => null,
    };
}

fn jsonObject(v: std.json.Value) ?std.json.ObjectMap {
    return switch (v) {
        .object => |o| o,
        else => null,
    };
}

fn jsonCount(v: std.json.Value) ?u32 {
    switch (v) {
        .integer => |n| {
            if (n < 0 or n > std.math.maxInt(u32)) return null;
            return @intCast(n);
        },
        .float => |f| {
            if (f < 0 or f > @as(f64, std.math.maxInt(u32))) return null;
            return @as(u32, @intFromFloat(f));
        },
        else => return null,
    }
}

fn tokensFn(model: *const Model) canvas.DesignTokens {
    return switch (model.theme_mode) {
        .dark => theme.darkTokens(),
        .light => theme.lightTokens(),
    };
}

/// Process a single SSE event body (the JSON after `data: `).
/// Used by both stream_line (live streaming) and stream_done (buffered fallback).
fn processSSEEvent(model: *Model, chat: *Chat, sse_body: []const u8, fx: *Effects) void {
    const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, sse_body, .{}) catch return;
    defer parsed.deinit();
    const root_obj = jsonObject(parsed.value) orelse return;
    const event_type = jsonString(root_obj.get("type") orelse return) orelse return;
    const data = jsonObject(root_obj.get("data") orelse return) orelse return;

    if (std.mem.eql(u8, event_type, "text_delta")) {
        const delta = jsonString(data.get("delta") orelse return) orelse return;
        if (!std.mem.eql(u8, chat.open_bubble_type, "assistant")) {
            addMessage(chat, model.allocator, "assistant", trimLeadingMessageWhitespace(delta));
            chat.open_bubble_type = "assistant";
        } else {
            appendToLastMessage(chat, model.allocator, "assistant", delta);
        }
    } else if (std.mem.eql(u8, event_type, "reasoning_delta")) {
        const delta = jsonString(data.get("delta") orelse return) orelse return;
        removeTrailingEmptyAssistant(chat);
        if (!std.mem.eql(u8, chat.open_bubble_type, "reasoning")) {
            addMessage(chat, model.allocator, "reasoning", delta);
            chat.open_bubble_type = "reasoning";
        } else {
            appendToLastMessage(chat, model.allocator, "reasoning", delta);
        }
    } else if (std.mem.eql(u8, event_type, "tool_input_start")) {
        const name = jsonString(data.get("name") orelse return) orelse return;
        removeTrailingEmptyAssistant(chat);
        const args_val = data.get("args");
        const args_str = if (args_val) |v| toolArgsString(model, v) else "";
        addToolMessage(chat, model.allocator, name, args_str);
        chat.open_bubble_type = "tool";
        chat.status_text = std.fmt.allocPrint(model.allocator, "{s}: running...", .{name}) catch name;
    } else if (std.mem.eql(u8, event_type, "tool_input_end")) {
        // Main stream path: arguments arrive on tool_input_end (RunEvent
        // ToolEndData.arguments), not on tool_input_start.
        if (findRunningToolBubble(chat)) |tb| {
            const args_val = data.get("arguments");
            const args_str = if (args_val) |v| toolArgsString(model, v) else "";
            const summary = std.fmt.allocPrint(model.allocator, "{s}({s})", .{ tb.tool_name, args_str }) catch return;
            tb.content = summary;
        }
    } else if (std.mem.eql(u8, event_type, "tool_result")) {
        const content = jsonString(data.get("content") orelse return) orelse return;
        const name = jsonString(data.get("name") orelse return) orelse return;
        if (findRunningToolBubble(chat)) |tb| {
            tb.tool_status = model.allocator.dupe(u8, "done") catch return;
            tb.tool_result = model.allocator.dupe(u8, content) catch return;
            tb.collapsed = true;
        }
        chat.status_text = std.fmt.allocPrint(model.allocator, "{s}: done", .{name}) catch name;
    } else if (std.mem.eql(u8, event_type, "interrupt")) {
        const tool = jsonString(data.get("tool") orelse return) orelse return;
        const call_id = jsonString(data.get("call_id") orelse return) orelse return;
        chat.has_pending = true;
        chat.pending_tool = model.allocator.dupe(u8, tool) catch return;
        chat.pending_call_id = model.allocator.dupe(u8, call_id) catch return;
        if (model.settings.reduced_motion) {
            model.hitl_entrance = 1;
        } else {
            model.hitl_entrance = 0;
            fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
        }
    } else if (std.mem.eql(u8, event_type, "rubric_evaluation_start")) {
        chat.status_text = "Checking rubric...";
        removeTrailingEmptyAssistant(chat);
        upsertRubricRow(chat, model.allocator, "checking…", "Checking response against rubric...", false);
    } else if (std.mem.eql(u8, event_type, "rubric_evaluation_end")) {
        const eval_obj = jsonObject(data.get("evaluation") orelse return) orelse return;
        const result = jsonString(eval_obj.get("result") orelse return) orelse {
            chat.status_text = "";
            return;
        };
        chat.rubric_attempts += 1;

        // Build the settled rubric row (same shape as a tool artifact):
        // label = verdict + criteria counts, body = explanation.
        const explanation_val = eval_obj.get("explanation");
        const explanation: []const u8 = if (explanation_val) |ev| switch (ev) {
            .string => |s| s,
            else => "",
        } else "";
        var passed_count: usize = 0;
        var total_count: usize = 0;
        if (eval_obj.get("criteria")) |crit_val| {
            if (crit_val == .array) {
                total_count = crit_val.array.items.len;
                for (crit_val.array.items) |c| {
                    if (c != .object) continue;
                    const passed = c.object.get("passed") orelse continue;
                    if (passed == .bool and passed.bool) passed_count += 1;
                }
            }
        }
        const verdict: []const u8 = if (std.mem.eql(u8, result, "satisfied"))
            std.fmt.allocPrint(model.allocator, "Passed ({d}/{d})", .{ passed_count, total_count }) catch "Passed"
        else if (std.mem.eql(u8, result, "needs_revision"))
            std.fmt.allocPrint(model.allocator, "Needs revision ({d}/{d})", .{ passed_count, total_count }) catch "Needs revision"
        else if (std.mem.eql(u8, result, "grader_error"))
            "Check failed"
        else if (std.mem.eql(u8, result, "invalid_rubric"))
            "Invalid rubric"
        else
            "Complete";

        const rubric_msg = std.fmt.allocPrint(model.allocator, "{d}/{d} criteria passed\n{s}", .{ passed_count, total_count, explanation }) catch verdict;
        upsertRubricRow(chat, model.allocator, verdict, rubric_msg, false);
        chat.status_text = model.allocator.dupe(u8, verdict) catch "";
    } else if (std.mem.eql(u8, event_type, "response_revision_start")) {
        // In-place revision: keep one stable answer bubble. The attempt-1
        // content is cleared (the empty bubble shows "Revising..." via
        // status_text); the revised attempt streams into the same bubble.
        // The live rubric row is dropped — the next evaluation re-adds it.
        removeRubricRows(chat);
        if (chat.msg_count > 0) {
            var i: usize = chat.msg_count;
            while (i > 0) {
                i -= 1;
                if (std.mem.eql(u8, chat._messages[i].role, "assistant")) {
                    chat._messages[i].content = "";
                    chat._messages[i].collapsed = false;
                    break;
                }
            }
        }
        chat.open_bubble_type = "assistant";
        chat.status_text = "Revising...";
    } else if (std.mem.eql(u8, event_type, "context_compressed")) {
        chat.compression_animation_ticks = 8;
        const status = jsonString(data.get("status") orelse return) orelse "succeeded";
        if (std.mem.eql(u8, status, "succeeded")) {
            removeTrailingEmptyAssistant(chat);
            addMessage(chat, model.allocator, "system", "Conversation summarized to fit context window.");
        }
    } else if (std.mem.eql(u8, event_type, "usage")) {
        const usage_data = jsonObject(data.get("usage") orelse return) orelse return;
        if (usage_data.get("input_tokens")) |it| {
            if (jsonCount(it)) |n| chat.context_info.input_tokens = n;
        }
        if (usage_data.get("output_tokens")) |ot| {
            if (jsonCount(ot)) |n| chat.context_info.output_tokens = n;
        }
    } else if (std.mem.eql(u8, event_type, "done")) {
        const result_data = jsonObject(data.get("result") orelse return) orelse return;
        if (result_data.get("verification")) |verification_val| {
            const verification = jsonObject(verification_val) orelse return;
            // Settle the rubric row with the terminal verdict (collapsed).
            addRubricRowFromVerdict(chat, model.allocator, verification);
        }
        if (result_data.get("model")) |model_val| {
            if (jsonString(model_val)) |model_str| {
                chat.context_info.model = model.allocator.dupe(u8, model_str) catch "";
            }
        }
    } else if (std.mem.eql(u8, event_type, "error")) {
        if (data.get("message")) |message_val| {
            const message = jsonString(message_val) orelse return;
            if (chat.open_bubble_type.len > 0 and chat.msg_count > 0) {
                appendToLastMessage(chat, model.allocator, chat.open_bubble_type, message);
            } else {
                addMessage(chat, model.allocator, "system", message);
            }
            return;
        }
        const code = jsonString(data.get("code") orelse return) orelse return;
        if (std.mem.eql(u8, code, "cancelled")) {
            chat.streaming = false;
            chat.open_bubble_type = "";
            chat.status_text = "";
            if (chat.msg_count > 0) {
                const last = &chat._messages[chat.msg_count - 1];
                if (std.mem.eql(u8, last.role, "assistant") and last.content.len == 0) {
                    chat.msg_count -= 1;
                    chat.messages = chat._messages[0..chat.msg_count];
                }
            }
        }
    }
}

/// Recalculate the textarea height from the current draft_text line count.
/// Called on input, chat switch, and new chat so the textarea always
/// reflects the actual content height (auto-grows with multi-line text,
/// up to max_lines, then scrolls internally).
fn recalcTextareaHeight(chat: *Chat) void {
    const new_line_count = std.mem.count(u8, chat.draft_text, "\n") + 1;
    const line_height: f32 = 20;
    const padding: f32 = 8;
    // Cap at 10 lines (Codex convention): beyond this the field stops
    // growing and scrolls internally instead of pushing the composer taller.
    const max_lines = 10;
    const natural_height = @max(36, @as(f32, @floatFromInt(new_line_count)) * line_height + padding);
    const max_height = @as(f32, @floatFromInt(max_lines)) * line_height + padding;
    const new_height = @min(max_height, natural_height);
    if (new_height != chat.last_textarea_height) {
        const growing = new_height > chat.last_textarea_height;
        chat.last_textarea_height = new_height;
        if (growing) {
            chat.transcript_scroll_generation += 1;
        }
    }
}

/// Core send-message logic, shared by the Send button and Enter-in-textarea.
/// Reads and clears the active chat's draft_text, fires the /message request.
fn doSend(model: *Model, fx: *Effects) void {
    const chat = model.activeChat();
    const text = std.mem.trim(u8, chat.draft_text, " \n\r\t");
    if (text.len == 0 or chat.streaming) return;
    chat.transcript_scroll_generation += 1;
    addMessage(chat, model.allocator, "user", text);
    if (chat.msg_count == 1) {
        chat.title = model.allocator.dupe(u8, text) catch "New chat";
    }
    chat.draft_text = "";
    chat.draft_selection = .{};
    chat.last_textarea_height = 36;
    chat.streaming = true;
    chat.stream_started_at = nowMillis();
    chat.fetch_key = model.allocFetchKey();
    chat.open_bubble_type = "";
    chat.status_text = "Thinking...";
    addMessage(chat, model.allocator, "assistant", "");
    chat.open_bubble_type = "assistant";

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
        }, .{
            .name = "Accept",
            .value = "text/event-stream",
        }},
        .body = body,
        .response = .stream,
        .on_line = Effects.lineMsg(.stream_line),
        .timeout_ms = 120_000,
        .on_response = Effects.responseMsg(.stream_done),
    });
}

pub fn update(model: *Model, msg: Msg, fx: *Effects) void {
    switch (msg) {
        .input_changed => |event| {
            const chat = model.activeChat();
            // Enter-to-send: the SDK's textarea inserts "\n" on plain Enter
            // (on_submit only fires on Cmd+Enter). Intercept a bare newline
            // insert and send the message instead of adding a line break.
            if (event == .insert_text and std.mem.eql(u8, event.insert_text, "\n")) {
                doSend(model, fx);
                return;
            }
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
            recalcTextareaHeight(chat);
        },
        .toggle_theme => {
            model.theme_mode = switch (model.theme_mode) {
                .dark => .light,
                .light => .dark,
            };
        },
        .new_chat => {
            model.settings.visible = false;
            if (model.chat_count >= max_chats) return;
            // Append the new chat, then sort by created_at descending so the
            // newest chat naturally appears at the top.
            model.chats[model.chat_count] = .{ .id = model.next_chat_id, .history_loaded = true };
            model.chats[model.chat_count].setSessionId(model.next_chat_id);
            model.chats[model.chat_count].created_at = currentTimestampISO(model.allocator);
            model.next_chat_id += 1;
            model.active_chat_idx = model.chat_count;
            model.active_chat_id = model.chats[model.active_chat_idx].id;
            model.chat_count += 1;
            // Sort by created_at descending (newest first)
            sortChatsByCreatedAt(model);
            // Re-find the active chat after sort
            var idx: usize = 0;
            while (idx < model.chat_count) : (idx += 1) {
                if (model.chats[idx].id == model.active_chat_id) {
                    model.active_chat_idx = idx;
                    break;
                }
            }
            // Entrance animation for the empty state (fade + translateY).
            // Reduced motion jumps straight to settled.
            if (model.settings.reduced_motion) {
                model.empty_entrance = 1;
            } else {
                model.empty_entrance = 0;
                fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
            }
        },
        .switch_chat => |chat_id| {
            model.settings.visible = false;
            var i: usize = 0;
            while (i < model.chat_count) : (i += 1) {
                if (model.chats[i].id == chat_id) {
                    model.active_chat_idx = i;
                    model.active_chat_id = model.chats[i].id;
                    model.chats[i].unread_count = 0;
                    recalcTextareaHeight(&model.chats[i]);
                    // Empty state entrance when switching to an empty chat
                    if (model.chats[i].msg_count == 0 and !model.chats[i].history_loading) {
                        if (model.settings.reduced_motion) {
                            model.empty_entrance = 1;
                        } else {
                            model.empty_entrance = 0;
                            fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
                        }
                    }
                    // Load history for this chat if not yet loaded
                    if (!model.chats[i].history_loaded and model.chats[i].msg_count == 0) {
                        model.chats[i].history_loaded = true;
                        model.chats[i].history_loading = true;
                        const fetch_key = model.chats[i].id + 1000;
                        model.chats[i].fetch_key = fetch_key;
                        const url = std.fmt.allocPrint(
                            model.allocator,
                            "http://127.0.0.1:8080/conversation/turns?user_id=native_sdk_chat&session_id={s}&limit=50",
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
                    // Fix active index: if we removed a chat before the active one,
                    // the active chat shifted left by one slot.
                    if (i < model.active_chat_idx) {
                        model.active_chat_idx -= 1;
                    }
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
        .models_loaded => |response| blk: {
            // Chain the active chat's history fetch (see initFx comment).
            defer fetchActiveChatHistory(model, fx);
            if (response.outcome != .ok) break :blk;
            const body = response.body;
            if (body.len == 0) break :blk;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch break :blk;
            defer parsed.deinit();
            const root = parsed.value;
            const models_arr = root.object.get("models") orelse break :blk;
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
                const prov_val = item.object.get("provider");
                const key_source_val = item.object.get("key_source");
                const id_str = switch (id_val) { .string => |s| s, else => continue };
                const name_str = switch (name_val) { .string => |s| s, else => continue };
                const pd_str = switch (pd_val) { .string => |s| s, else => continue };
                const prov_str = if (prov_val) |v| switch (v) { .string => |s| s, else => "" } else "";
                const key_source_str = if (key_source_val) |v| switch (v) { .string => |s| s, else => "" } else "";
                model.available_models[model.available_model_count] = .{
                    .id = model.allocator.dupe(u8, id_str) catch continue,
                    .name = model.allocator.dupe(u8, name_str) catch continue,
                    .provider = model.allocator.dupe(u8, prov_str) catch continue,
                    .provider_display = model.allocator.dupe(u8, pd_str) catch continue,
                    .key_source = model.allocator.dupe(u8, key_source_str) catch continue,
                };
                model.available_model_count += 1;
            }
        },
        .cycle_model => {
            if (model.available_model_count > 0) {
                var attempts: usize = 0;
                var idx = model.selected_model_idx;
                while (attempts < model.available_model_count) : (attempts += 1) {
                    idx = (idx + 1) % model.available_model_count;
                    if (!std.mem.eql(u8, model.available_models[idx].key_source, "none")) {
                        model.selected_model_idx = idx;
                        break;
                    }
                }
            }
        },
        .toggle_model_menu => {
            model.model_menu_open = !model.model_menu_open;
            if (model.model_menu_open) {
                model.model_menu_search = "";
                model.model_menu_search_selection = .{ .anchor = 0, .focus = 0 };
            }
        },
        .close_model_menu => {
            model.model_menu_open = false;
        },
        .model_menu_search_input => |event| {
            const output = model.allocator.alloc(u8, model.model_menu_search.len + 256) catch return;
            const next = (canvas.TextEditState{
                .text = model.model_menu_search,
                .selection = model.model_menu_search_selection,
            }).apply(event, output) catch return;
            model.model_menu_search = next.text;
            model.model_menu_search_selection = next.selection;
        },
        .model_menu_select => |filtered_idx| {
            // Map the filtered (ready + search-matching) index to the real
            // catalog index, select it, persist it, and close the menu.
            var seen: usize = 0;
            for (0..model.available_model_count) |i| {
                const m = model.available_models[i];
                if (std.mem.eql(u8, m.key_source, "none")) continue;
                if (!modelMatchesSearch(model, m)) continue;
                if (seen == filtered_idx) {
                    model.selected_model_idx = i;
                    model.model_menu_open = false;
                    saveSettingsModel(model, fx, i);
                    return;
                }
                seen += 1;
            }
        },
        .model_menu_manage => {
            model.model_menu_open = false;
            model.settings.visible = true;
            model.settings.section = .providers_models;
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
                    const q = model.search_query;
                    if (q.len > 0) {
                        // Walk back over UTF-8 continuation bytes (10xxxxxx) to the
                        // leading byte so we remove a whole code point, not one byte.
                        var end = q.len - 1;
                        while (end > 0 and (q[end] & 0xC0) == 0x80) {
                            end -= 1;
                        }
                        model.search_query = q[0..end];
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
            doSend(model, fx);
        },
        .retry => {
            // Re-send the last user message as a new turn. The failed
            // attempt's user message stays in the transcript (the backend
            // already stored it) — the retry is an honest second send.
            const chat = model.activeChat();
            if (chat.streaming) return;
            var i = chat.msg_count;
            while (i > 0) {
                i -= 1;
                if (std.mem.eql(u8, chat._messages[i].role, "user")) {
                    chat.draft_text = chat._messages[i].content;
                    doSend(model, fx);
                    return;
                }
            }
        },
        .cancel => {
            const chat = model.activeChat();
            if (!chat.streaming) return;
            chat.streaming = false;
            chat.has_pending = false;
            chat.pending_tool = "";
            chat.pending_call_id = "";
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
            chat.stream_started_at = nowMillis();
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
            const sse_body = line.line[prefix.len..];
            if (std.mem.eql(u8, sse_body, "[DONE]")) return;
            processSSEEvent(model, chat, sse_body, fx);
        },
        .stream_done => |response| {
            // Terminal event for the streaming fetch. The response content was
            // already rendered incrementally via stream_line/processSSEEvent.
            // Here we just finalize the stream state and generate a title.
            const chat = model.findChatByFetchKey(response.key) orelse return;
            if (response.outcome != .ok) {
                const err_msg = std.fmt.allocPrint(model.allocator, "Stream error: {s}", .{@tagName(response.outcome)}) catch "Stream error";
                addMessage(chat, model.allocator, "system", err_msg);
                finalizeStream(chat);
                return;
            }
            finalizeStream(chat);
            // Reconcile the transcript with server truth (the streamed content
            // may have been persisted with edits, e.g. canvas fences stripped).
            queueHistoryFetch(model, chat, fx);
            // Mark this chat as unread if it's not the active one
            if (&model.chats[model.active_chat_idx] != chat) {
                chat.unread_count += 1;
            }
            // Generate title if this was the first exchange (exactly 1 user message)
            // Retries on later exchanges if the first attempt failed.
            if (!chat.title_generated and chat.title.len >= 5) {
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
            // Route through finalizeStream so fetch_key, status_text and
            // open_bubble_type are cleared consistently with the other
            // terminal paths (not just the streaming flag).
            finalizeStream(chat);
        },
        .approve_done => |response| {
            const chat = model.findChatByFetchKey(response.key) orelse return;
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
            if (!model.settings.reduced_motion) {
                model.pulse_phase += 0.15;
                if (model.pulse_phase > std.math.tau) model.pulse_phase -= std.math.tau;
            }
            // Advance entrance animations (fade-in of surfaces). Each
            // progresses toward 1 (settled) in 2 ticks (~120ms) — the
            // settings panel is the heaviest view, so keep rebuilds minimal.
            // Reduced motion jumps straight to 1.
            var any_entrance = false;
            if (!model.settings.reduced_motion) {
                const entrance_step: f32 = 0.5; // 2 ticks * 60ms ≈ 120ms
                if (model.settings_entrance < 1) {
                    model.settings_entrance = @min(1, model.settings_entrance + entrance_step);
                    any_entrance = true;
                }
                if (model.hitl_entrance < 1) {
                    model.hitl_entrance = @min(1, model.hitl_entrance + entrance_step);
                    any_entrance = true;
                }
                if (model.empty_entrance < 1) {
                    model.empty_entrance = @min(1, model.empty_entrance + entrance_step);
                    any_entrance = true;
                }
                if (model.composer_entrance < 1) {
                    model.composer_entrance = @min(1, model.composer_entrance + entrance_step);
                    any_entrance = true;
                }
            }
            // Decrement compression animation ticks for all chats
            for (0..model.chat_count) |i| {
                if (model.chats[i].compression_animation_ticks > 0) {
                    model.chats[i].compression_animation_ticks -= 1;
                }
            }
            // Stream watchdog: if a chat has been streaming past the fetch
            // timeout (120s) plus slack, the terminal event was lost (timeout
            // teardown race). Force-finalize so the UI never sticks on
            // "Stop" with a dead composer. finalizeStream clears fetch_key,
            // so a late terminal event finds no chat and is ignored — no
            // double error messages.
            const now_ms = nowMillis();
            for (0..model.chat_count) |i| {
                const chat = &model.chats[i];
                if (chat.streaming and chat.stream_started_at > 0 and
                    now_ms - chat.stream_started_at > stream_watchdog_ms)
                {
                    addMessage(chat, model.allocator, "system", "Stream error: timed_out");
                    finalizeStream(chat);
                }
            }
            // Elapsed-time indicator: show how long the current response has
            // been streaming so a slow provider doesn't read as "stuck".
            for (0..model.chat_count) |i| {
                const chat = &model.chats[i];
                if (chat.streaming and chat.stream_started_at > 0 and
                    std.mem.startsWith(u8, chat.status_text, "Thinking"))
                {
                    const elapsed_s = @max(0, @divTrunc(now_ms - chat.stream_started_at, 1000));
                    chat.status_text = std.fmt.allocPrint(
                        model.allocator, "Thinking… {d}s", .{elapsed_s},
                    ) catch "Thinking...";
                }
            }
            // Reschedule timer if any chat is still streaming or has animation
            if (model.anyStreaming() or model.anyCompressionAnimation() or any_entrance) {
                fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
            }
        },
        .history_loaded => |response| {
            if (response.outcome != .ok) {
                const chat = model.activeChat();
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Unable to connect to server. Is the backend running?");
                return;
            }
            const body = response.body;
            const chat = model.findChatByHistoryKey(response.key) orelse model.activeChat();
            if (body.len == 0) {
                chat.history_loading = false;
                chat.history_loaded = true;
                return;
            }
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch {
                chat.history_loading = false;
                chat.history_loaded = true;
                return;
            };
            defer parsed.deinit();
            const root = parsed.value;
            const turns_arr = root.object.get("turns") orelse {
                chat.history_loading = false;
                chat.history_loaded = true;
                return;
            };
            const arr = switch (turns_arr) {
                .array => |a| a,
                else => {
                    chat.history_loading = false;
                    chat.history_loaded = true;
                    return;
                },
            };
            chat.history_loaded = true;
            chat.history_loading = false;
            chat.msg_count = 0;
            chat.messages = chat._messages[0..0];
            for (arr.items) |turn_item| {
                const turn = turn_item.object;
                const messages_arr = turn.get("messages") orelse continue;
                const msgs = switch (messages_arr) {
                    .array => |a| a,
                    else => continue,
                };
                for (msgs.items) |item| {
                    addHistoryMessage(chat, model.allocator, item);
                }
                if (turn.get("metadata")) |meta| {
                    if (meta.object.get("model")) |m| {
                        chat.context_info.model = model.allocator.dupe(u8, m.string) catch "";
                    }
                    // Reload renders the settled Rubric row from the stored
                    // verification verdict (collapsed), matching the live view.
                    if (meta.object.get("verification")) |ver| {
                        if (ver == .object) addRubricRowFromVerdict(chat, model.allocator, ver.object);
                    }
                }
            }
            // Fall back to the first message text only when the chat has no
            // title yet — a stored/LLM title from the sessions list must not
            // be clobbered by history reload.
            if (chat.msg_count > 0 and !chat.title_generated and std.mem.eql(u8, chat.title, "New chat")) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
            chat.fetch_key = 0;
            // Startup chain end: fetch the saved default model so the
            // composer selects it (the settings panel also fetches on open).
            fetchSettingsCatalog(fx);
        },
        .chat_history_loaded => |response| {
            if (response.outcome != .ok) {
                const chat = model.findChatByHistoryKey(response.key) orelse return;
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Failed to load chat history.");
                return;
            }
            const body = response.body;
            const chat = model.findChatByHistoryKey(response.key) orelse return;
            if (body.len == 0) {
                chat.history_loading = false;
                chat.history_loaded = true;
                return;
            }
            chat.history_loading = false;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const turns_arr = root.object.get("turns") orelse return;
            const arr = switch (turns_arr) {
                .array => |a| a,
                else => return,
            };
            chat.history_loaded = true;
            chat.msg_count = 0;
            chat.messages = chat._messages[0..0];
            for (arr.items) |turn_item| {
                const turn = turn_item.object;
                const messages_arr = turn.get("messages") orelse continue;
                const msgs = switch (messages_arr) {
                    .array => |a| a,
                    else => continue,
                };
                for (msgs.items) |item| {
                    addHistoryMessage(chat, model.allocator, item);
                }
                if (turn.get("metadata")) |meta| {
                    if (meta.object.get("model")) |m| {
                        chat.context_info.model = model.allocator.dupe(u8, m.string) catch "";
                    }
                    // Reload renders the settled Rubric row from the stored
                    // verification verdict (collapsed), matching the live view.
                    if (meta.object.get("verification")) |ver| {
                        if (ver == .object) addRubricRowFromVerdict(chat, model.allocator, ver.object);
                    }
                }
            }
            // Fall back to the first message text only when the chat has no
            // title yet — a stored/LLM title from the sessions list must not
            // be clobbered by history reload.
            if (chat.msg_count > 0 and !chat.title_generated and std.mem.eql(u8, chat.title, "New chat")) {
                const first = chat._messages[0];
                if (std.mem.eql(u8, first.role, "user")) {
                    chat.title = model.allocator.dupe(u8, first.content) catch "New chat";
                }
            }
            chat.fetch_key = 0;
        },
        .sessions_loaded => |response| blk: {
            // Chain the models fetch (and from it, history) so startup never
            // fires concurrent connects to the same host (ISCONN panic race).
            defer fetchModels(fx);
            if (response.outcome != .ok) {
                // A3: surface backend connection error
                const chat = model.activeChat();
                chat.history_loading = false;
                addMessage(chat, model.allocator, "system", "Unable to connect to server. Is the backend running?");
                break :blk;
            }
            const body = response.body;
            if (body.len == 0) break :blk;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch break :blk;
            defer parsed.deinit();
            const root = parsed.value;
            const sessions_arr = root.object.get("sessions") orelse break :blk;
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
                const created_val = item.object.get("created_at");
                const sid = switch (sid_val) {
                    .string => |s| s,
                    else => continue,
                };
                const title = switch (title_val) {
                    .string => |s| s,
                    else => continue,
                };
                const created = if (created_val) |v| switch (v) {
                    .string => |s| s,
                    else => "",
                } else "";
                if (std.mem.eql(u8, sid, initial_session_id)) {
                    // Update the initial chat with title and created_at
                    model.chats[0].title = model.allocator.dupe(u8, title) catch "New chat";
                    model.chats[0].created_at = model.allocator.dupe(u8, created) catch "";
                    continue;
                }
                if (model.chat_count >= max_chats) break;

                // Use a hash of the session_id string as the unique chat id
                const hash = std.hash.Wyhash.hash(0, sid);
                model.chats[model.chat_count] = .{ .id = hash };
                model.chats[model.chat_count].setSessionIdStr(sid);
                model.chats[model.chat_count].title = model.allocator.dupe(u8, title) catch "New chat";
                model.chats[model.chat_count].history_loaded = false;
                model.chats[model.chat_count].created_at = model.allocator.dupe(u8, created) catch "";
                model.chat_count += 1;
            }
            // Sort chats by created_at descending (newest first).
            // The initial chat (index 0) is included in the sort. The active chat
            // index is adjusted to follow the moved chat.
            const old_active_id = model.chats[model.active_chat_idx].id;
            sortChatsByCreatedAt(model);
            // Re-find the active chat by its id
            var new_idx: usize = 0;
            while (new_idx < model.chat_count) : (new_idx += 1) {
                if (model.chats[new_idx].id == old_active_id) break;
            }
            model.active_chat_idx = new_idx;
            // Ensure next_chat_id won't collide with any existing chat-N session
            model.next_chat_id = model.chat_count + 100;
        },
        .open_settings => {
            if (model.settings.visible) {
                model.settings.visible = false;
                model.settings.key_modal_visible = false;
                return;
            }
            model.settings.visible = true;
            model.settings.loading = true;
            model.settings.provider_count = 0;
            model.available_model_count = 0;
            model.settings.search_text = "";
            model.settings.model_error = "";
            model.settings.key_modal_visible = false;
            model.settings.key_input = "";
            model.settings.key_error = "";
            model.settings.grader_prompt_loading = false;
            // Entrance animation: fade + scale in from 0.97. Reduced motion
            // jumps straight to settled (no animation).
            if (model.settings.reduced_motion) {
                model.settings_entrance = 1;
            } else {
                model.settings_entrance = 0;
                fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
            }
            fx.fetch(.{
                .key = settings_key,
                .url = "http://127.0.0.1:8080/settings/model-catalog?user_id=native_sdk_chat&max_models_per_provider=20&max_providers=64",
                .method = .GET,
                .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                .response = .buffered,
                .on_response = Effects.responseMsg(.settings_loaded),
            });
            fx.fetch(.{
                .key = settings_general_key,
                .url = "http://127.0.0.1:8080/settings?user_id=native_sdk_chat",
                .method = .GET,
                .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                .response = .buffered,
                .on_response = Effects.responseMsg(.settings_general_loaded),
            });
        },
        .close_settings => {
            model.settings.visible = false;
        },
        .open_tools => {
            if (model.tools.visible) {
                model.tools.visible = false;
                return;
            }
            model.settings.visible = false; // tools wins precedence
            model.tools.visible = true;
            model.tools.loading = true;
        },
        .close_tools => {
            model.tools.visible = false;
        },
        .tools_tab_builtin => {
            model.tools.section = .builtin;
        },
        .tools_tab_connections => {
            model.tools.section = .connections;
        },
        .settings_providers_models => {
            model.settings.section = .providers_models;
        },
        .settings_general => {
            model.settings.section = .general;
            if (!model.settings.grader_prompt_loading) {
                model.settings.grader_prompt_loading = true;
                fx.fetch(.{
                    .key = grader_prompt_key,
                    .url = "http://127.0.0.1:8080/user/grader-prompt?user_id=native_sdk_chat",
                    .method = .GET,
                    .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                    .response = .buffered,
                    .on_response = Effects.responseMsg(.grader_prompt_loaded),
                });
            }
        },
        .settings_loaded => |response| {
            model.settings.loading = false;
            if (response.outcome != .ok) return;
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            // Parse optional default_model. Catalog responses may omit it; use composer selection then.
            model.settings.default_model_id = model.allocator.dupe(u8, model.selectedModel()) catch "";
            if (root.object.get("default_model")) |dm| {
                if (dm == .string and dm.string.len > 0) {
                    model.settings.default_model_id = model.allocator.dupe(u8, dm.string) catch "";
                }
            }
            // Select the saved default in the composer (startup or revisit).
            if (model.settings.default_model_id.len > 0) {
                for (0..model.available_model_count) |i| {
                    if (std.mem.eql(u8, model.available_models[i].id, model.settings.default_model_id)) {
                        model.selected_model_idx = i;
                        break;
                    }
                }
            }
            // Parse the role models (grader / title / summarization).
            if (root.object.get("grader_model")) |gm| {
                if (gm == .string and gm.string.len > 0) {
                    model.settings.grader_model_id = model.allocator.dupe(u8, gm.string) catch "";
                }
            }
            if (root.object.get("title_model")) |tm| {
                if (tm == .string and tm.string.len > 0) {
                    model.settings.title_model_id = model.allocator.dupe(u8, tm.string) catch "";
                }
            }
            if (root.object.get("summarization_model")) |sm| {
                if (sm == .string and sm.string.len > 0) {
                    model.settings.summarization_model_id = model.allocator.dupe(u8, sm.string) catch "";
                }
            }
            model.settings.provider_count = 0;
            model.available_model_count = 0;

            if (root.object.get("providers")) |providers_val| {
                const providers_arr = switch (providers_val) {
                    .array => |a| a,
                    else => return,
                };
                var count: usize = 0;
                for (providers_arr.items) |item| {
                    if (count >= max_providers) {
                        model.settings.model_error = "Catalog truncated; too many providers";
                        break;
                    }
                    const id_val = item.object.get("id") orelse continue;
                    const name_val = item.object.get("name") orelse continue;
                    const pid = switch (id_val) { .string => |s| s, else => continue };
                    if (pid.len == 0) continue;
                    const name = switch (name_val) { .string => |s| s, else => pid };
                    const key_source = if (item.object.get("key_source")) |ks| switch (ks) { .string => |s| s, else => "none" } else "none";
                    const has_key = if (item.object.get("has_key")) |h| h.bool else !std.mem.eql(u8, key_source, "none");
                    model.settings.providers[count] = .{
                        .id = model.allocator.dupe(u8, pid) catch continue,
                        .name = model.allocator.dupe(u8, name) catch continue,
                        .has_key = has_key,
                        .via_env = std.mem.eql(u8, key_source, "env") or std.mem.eql(u8, key_source, "hosted"),
                        .key_source = model.allocator.dupe(u8, key_source) catch "none",
                        .model_count = 0,
                    };
                    if (item.object.get("models")) |models_val| {
                        const models_arr = switch (models_val) { .array => |a| a, else => continue };
                        for (models_arr.items) |model_item| {
                            if (model.available_model_count >= max_models) {
                                model.settings.model_error = "Catalog truncated; narrow search or update the app";
                                break;
                            }
                            const mid_val = model_item.object.get("id") orelse continue;
                            const mname_val = model_item.object.get("name") orelse continue;
                            const pd_val = model_item.object.get("provider_display");
                            const prov_val = model_item.object.get("provider");
                            const mks_val = model_item.object.get("key_source");
                            const mid = switch (mid_val) { .string => |s| s, else => continue };
                            const mname = switch (mname_val) { .string => |s| s, else => continue };
                            const pd = if (pd_val) |v| switch (v) { .string => |s| s, else => name } else name;
                            const prov = if (prov_val) |v| switch (v) { .string => |s| s, else => pid } else pid;
                            const mks = if (mks_val) |v| switch (v) { .string => |s| s, else => key_source } else key_source;
                            const model_idx = model.available_model_count;
                            model.available_models[model_idx] = .{
                                .id = model.allocator.dupe(u8, mid) catch continue,
                                .name = model.allocator.dupe(u8, mname) catch continue,
                                .provider = model.allocator.dupe(u8, prov) catch continue,
                                .provider_display = model.allocator.dupe(u8, pd) catch continue,
                                .key_source = model.allocator.dupe(u8, mks) catch continue,
                            };
                            model.available_model_count += 1;
                            const p = &model.settings.providers[count];
                            if (p.model_count < max_provider_models) {
                                p.model_indices[p.model_count] = model_idx;
                                p.model_count += 1;
                            } else {
                                model.settings.model_error = "Catalog truncated; too many models for one provider";
                            }
                        }
                    }
                    count += 1;
                }
                model.settings.provider_count = count;
                for (0..model.available_model_count) |idx| {
                    if (std.mem.eql(u8, model.available_models[idx].id, model.settings.default_model_id)) {
                        model.selected_model_idx = idx;
                        model.settings.selected_model_idx = idx;
                        break;
                    }
                }
                sortSettingsProviders(&model.settings);
            }
        },
        .settings_general_loaded => |response| {
            if (response.outcome != .ok) return;
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            // Canonical settings response nests verification under saved/effective
            // and names the field max_attempts (not max_iterations).
            if (root.object.get("saved")) |saved| {
                if (saved.object.get("verification")) |v| {
                    if (v.object.get("enabled")) |e| {
                        if (e == .bool) model.settings.rubric_enabled = e.bool;
                    }
                }
            }
            if (root.object.get("effective")) |effective| {
                if (effective.object.get("verification")) |v| {
                    if (v.object.get("max_attempts")) |ma| {
                        if (ma == .integer) model.settings.rubric_max_iterations = @intCast(ma.integer);
                    }
                }
            }
        },
        .grader_prompt_loaded => |response| {
            model.settings.grader_prompt_loading = false;
            if (response.outcome != .ok) return;
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            if (root.object.get("content")) |c| {
                model.settings.grader_prompt = model.allocator.dupe(u8, c.string) catch "";
            }
            // The save must send the current revision back (strictly enforced).
            if (root.object.get("revision")) |r| {
                if (r == .integer and r.integer >= 0) {
                    model.settings.grader_prompt_revision = @intCast(r.integer);
                }
            }
        },
        .settings_general_saved => |response| {
            model.settings.saving_general = false;
            _ = response;
        },
        .grader_prompt_saved => |response| {
            _ = response;
        },
        .settings_search => |event| {
            if (model.settings.search_runtime_text_synced and event != .set_selection) {
                model.settings.search_runtime_text_synced = false;
                return;
            }
            const output = model.allocator.alloc(u8, model.settings.search_text.len + 256) catch return;
            const next = (canvas.TextEditState{
                .text = model.settings.search_text,
                .selection = model.settings.search_selection,
            }).apply(event, output) catch return;
            model.settings.search_text = next.text;
            model.settings.search_selection = next.selection;
            model.settings.search_selection_programmatic = (event == .set_selection);
        },
        .set_model_role => |role| {
            model.settings.model_role = role;
        },
        .select_model => |idx| {
            if (idx >= model.available_model_count or model.settings.saving_model) return;
            const m = model.available_models[idx];
            if (std.mem.eql(u8, m.key_source, "none")) {
                model.settings.key_modal_visible = true;
                model.settings.pending_model_idx = idx;
                model.settings.pending_model_id = model.allocator.dupe(u8, m.id) catch "";
                model.settings.pending_model_name = model.allocator.dupe(u8, m.name) catch "";
                model.settings.pending_provider_id = model.allocator.dupe(u8, m.provider) catch "";
                model.settings.key_input = "";
                model.settings.key_visible = false;
                model.settings.key_error = "";
                model.settings.key_testing = false;
                for (0..model.settings.provider_count) |pi| {
                    if (std.mem.eql(u8, model.settings.providers[pi].id, m.provider)) {
                        model.settings.pending_provider_idx = pi;
                        break;
                    }
                }
                return;
            }
            saveSettingsModel(model, fx, idx);
        },
        .model_selected => |response| {
            model.settings.saving_model = false;
            if (response.outcome != .ok) {
                model.settings.model_error = "Failed to save";
                return;
            }
            const saved_id = model.available_models[model.settings.selected_model_idx].id;
            // Sync composer model selector (agent role only) and the active
            // role's id in the settings panel.
            if (model.settings.model_role == .agent) {
                model.selected_model_idx = model.settings.selected_model_idx;
            }
            switch (model.settings.model_role) {
                .agent => model.settings.default_model_id = model.allocator.dupe(u8, saved_id) catch "",
                .grader => model.settings.grader_model_id = model.allocator.dupe(u8, saved_id) catch "",
                .title => model.settings.title_model_id = model.allocator.dupe(u8, saved_id) catch "",
                .summarization => model.settings.summarization_model_id = model.allocator.dupe(u8, saved_id) catch "",
            }
        },
        .add_key_expand => |idx| {
            if (idx < model.settings.provider_count) {
                model.settings.providers[idx].adding_key = true;
                model.settings.providers[idx].key_input = "";
                model.settings.providers[idx].key_visible = false;
                model.settings.providers[idx].test_error = "";
            }
        },
        .add_key_cancel => |idx| {
            if (idx < model.settings.provider_count) {
                model.settings.providers[idx].adding_key = false;
                model.settings.providers[idx].key_input = "";
                model.settings.providers[idx].key_visible = false;
                model.settings.providers[idx].test_error = "";
            }
            model.settings.key_modal_visible = false;
            model.settings.key_input = "";
            model.settings.key_error = "";
            model.settings.pending_provider_id = "";
            model.settings.pending_model_id = "";
            model.settings.pending_model_name = "";
        },
        .add_key_input => |event| {
            if (model.settings.key_modal_visible) {
                const output = model.allocator.alloc(u8, model.settings.key_input.len + 256) catch return;
                const next = (canvas.TextEditState{
                    .text = model.settings.key_input,
                    .selection = .{ .anchor = model.settings.key_input.len, .focus = model.settings.key_input.len },
                }).apply(event, output) catch return;
                model.settings.key_input = next.text;
                return;
            }
            for (0..model.settings.provider_count) |i| {
                if (model.settings.providers[i].adding_key) {
                    const p = &model.settings.providers[i];
                    const output = model.allocator.alloc(u8, p.key_input.len + 256) catch return;
                    const next = (canvas.TextEditState{
                        .text = p.key_input,
                        .selection = .{ .anchor = p.key_input.len, .focus = p.key_input.len },
                    }).apply(event, output) catch return;
                    p.key_input = next.text;
                    break;
                }
            }
        },
        .toggle_key_visibility => |idx| {
            if (model.settings.key_modal_visible) {
                model.settings.key_visible = !model.settings.key_visible;
                return;
            }
            if (idx < model.settings.provider_count) {
                model.settings.providers[idx].key_visible = !model.settings.providers[idx].key_visible;
            }
        },
        .add_key_submit => |idx| {
            const modal = model.settings.key_modal_visible;
            if (!modal and idx >= model.settings.provider_count) return;
            if (modal and model.settings.key_testing) return;
            if (!modal and model.settings.providers[idx].testing) return;
            const provider_id = if (modal) model.settings.pending_provider_id else model.settings.providers[idx].id;
            const api_key = if (modal) model.settings.key_input else model.settings.providers[idx].key_input;
            if (api_key.len == 0) return;
            if (modal) {
                model.settings.key_testing = true;
                model.settings.key_error = "";
            } else {
                model.settings.providers[idx].testing = true;
                model.settings.providers[idx].test_error = "";
            }
            const escaped_provider = escapeJsonString(model.allocator, provider_id) catch return;
            const escaped_key = escapeJsonString(model.allocator, api_key) catch return;
            const body = std.fmt.allocPrint(
                model.allocator,
                "{{\"provider\":\"{s}\",\"api_key\":\"{s}\"}}",
                .{ escaped_provider, escaped_key },
            ) catch return;
            const fetch_key = model.allocFetchKey();
            fx.fetch(.{
                .key = fetch_key,
                .url = "http://127.0.0.1:8080/settings/test-key",
                .method = .POST,
                .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                .body = body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.key_tested),
            });
        },
        .key_tested => |response| {
            if (response.outcome != .ok) {
                if (model.settings.key_modal_visible and model.settings.key_testing) {
                    model.settings.key_testing = false;
                    model.settings.key_error = "Failed to test key";
                    return;
                }
                for (0..model.settings.provider_count) |i| {
                    if (model.settings.providers[i].testing) {
                        model.settings.providers[i].testing = false;
                        model.settings.providers[i].test_error = "Failed to test key";
                        return;
                    }
                }
                return;
            }
            const body = response.body;
            if (body.len == 0) return;
            const parsed = std.json.parseFromSlice(std.json.Value, model.allocator, body, .{}) catch return;
            defer parsed.deinit();
            const root = parsed.value;
            const valid = if (root.object.get("valid")) |v| v.bool else false;
            const error_msg = if (root.object.get("error")) |e| switch (e) {
                .string => |s| s,
                else => "",
            } else "";
            if (model.settings.key_modal_visible and model.settings.key_testing) {
                model.settings.key_testing = false;
                if (valid) {
                    const escaped_provider = escapeJsonString(model.allocator, model.settings.pending_provider_id) catch return;
                    const escaped_key = escapeJsonString(model.allocator, model.settings.key_input) catch return;
                    const save_body = std.fmt.allocPrint(
                        model.allocator,
                        "{{\"provider\":\"{s}\",\"api_key\":\"{s}\"}}",
                        .{ escaped_provider, escaped_key },
                    ) catch return;
                    const fetch_key = model.allocFetchKey();
                    fx.fetch(.{
                        .key = fetch_key,
                        .url = "http://127.0.0.1:8080/settings/api-keys?user_id=native_sdk_chat",
                        .method = .POST,
                        .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                        .body = save_body,
                        .response = .buffered,
                        .on_response = Effects.responseMsg(.key_saved),
                    });
                } else {
                    model.settings.key_error = model.allocator.dupe(u8, error_msg) catch "Invalid key";
                }
                return;
            }
            for (0..model.settings.provider_count) |i| {
                if (model.settings.providers[i].testing) {
                    const p = &model.settings.providers[i];
                    p.testing = false;
                    if (valid) {
                        // Save the key
                        const escaped_provider = escapeJsonString(model.allocator, p.id) catch return;
                        const escaped_key = escapeJsonString(model.allocator, p.key_input) catch return;
                        const save_body = std.fmt.allocPrint(
                            model.allocator,
                            "{{\"provider\":\"{s}\",\"api_key\":\"{s}\"}}",
                            .{ escaped_provider, escaped_key },
                        ) catch return;
                        const fetch_key = model.allocFetchKey();
                        fx.fetch(.{
                            .key = fetch_key,
                            .url = "http://127.0.0.1:8080/settings/api-keys?user_id=native_sdk_chat",
                            .method = .POST,
                            .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                            .body = save_body,
                            .response = .buffered,
                            .on_response = Effects.responseMsg(.key_saved),
                        });
                    } else {
                        p.test_error = model.allocator.dupe(u8, error_msg) catch "Invalid key";
                    }
                    break;
                }
            }
        },
        .key_saved => |response| {
            if (response.outcome != .ok) {
                if (model.settings.key_modal_visible) {
                    model.settings.key_testing = false;
                    model.settings.key_error = "Failed to save key";
                    return;
                }
                for (0..model.settings.provider_count) |i| {
                    if (model.settings.providers[i].adding_key and model.settings.providers[i].key_input.len > 0) {
                        model.settings.providers[i].testing = false;
                        model.settings.providers[i].test_error = "Failed to save key";
                        return;
                    }
                }
                return;
            }
            if (model.settings.key_modal_visible) {
                const provider_idx = model.settings.pending_provider_idx;
                if (provider_idx < model.settings.provider_count) {
                    model.settings.providers[provider_idx].has_key = true;
                    model.settings.providers[provider_idx].via_env = false;
                    model.settings.providers[provider_idx].key_source = "user";
                    for (0..model.settings.providers[provider_idx].model_count) |mi| {
                        const model_idx = model.settings.providers[provider_idx].model_indices[mi];
                        model.available_models[model_idx].key_source = "user";
                    }
                }
                sortSettingsProviders(&model.settings);
                model.settings.key_modal_visible = false;
                model.settings.key_input = "";
                model.settings.key_error = "";
                const pending_idx = model.settings.pending_model_idx;
                model.settings.pending_provider_id = "";
                model.settings.pending_model_id = "";
                model.settings.pending_model_name = "";
                if (pending_idx < model.available_model_count) {
                    saveSettingsModel(model, fx, pending_idx);
                }
                return;
            }
            for (0..model.settings.provider_count) |i| {
                if (model.settings.providers[i].adding_key and model.settings.providers[i].key_input.len > 0) {
                    const prov = &model.settings.providers[i];
                    prov.has_key = true;
                    prov.via_env = false;
                    prov.key_source = "user";
                    prov.adding_key = false;
                    prov.key_input = "";
                    prov.key_visible = false;
                    for (0..prov.model_count) |mi| {
                        const model_idx = prov.model_indices[mi];
                        if (model_idx < model.available_model_count) {
                            model.available_models[model_idx].key_source = "user";
                        }
                    }
                    break;
                }
            }
        },
        .remove_key => |idx| {
            if (idx >= model.settings.provider_count) return;
            const p = &model.settings.providers[idx];
            if (p.via_env) return;
            const provider_id = p.id;
            const url = std.fmt.allocPrint(
                model.allocator,
                "http://127.0.0.1:8080/settings/api-keys/{s}?user_id=native_sdk_chat",
                .{provider_id},
            ) catch return;
            const fetch_key = model.allocFetchKey();
            if (model.settings.pending_key_delete_count < max_pending_key_deletes) {
                model.settings.pending_key_deletes[model.settings.pending_key_delete_count] = .{ .key = fetch_key, .provider_id = provider_id };
                model.settings.pending_key_delete_count += 1;
            }
            fx.fetch(.{
                .key = fetch_key,
                .url = url,
                .method = .DELETE,
                .headers = &.{.{ .name = "Accept", .value = "application/json" }},
                .response = .buffered,
                .on_response = Effects.responseMsg(.key_deleted),
            });
        },
        .key_deleted => |response| {
            if (response.outcome != .ok) return;
            // Correlate this response with the provider whose key was deleted.
            var provider_id: []const u8 = "";
            var found: ?usize = null;
            for (0..model.settings.pending_key_delete_count) |i| {
                if (model.settings.pending_key_deletes[i].key == response.key) {
                    provider_id = model.settings.pending_key_deletes[i].provider_id;
                    found = i;
                    break;
                }
            }
            if (found) |fi| {
                var k = fi;
                while (k + 1 < model.settings.pending_key_delete_count) : (k += 1) {
                    model.settings.pending_key_deletes[k] = model.settings.pending_key_deletes[k + 1];
                }
                model.settings.pending_key_delete_count -= 1;
            } else {
                return;
            }
            for (0..model.settings.provider_count) |i| {
                const prov = &model.settings.providers[i];
                if (std.mem.eql(u8, prov.id, provider_id)) {
                    prov.has_key = false;
                    prov.key_source = "none";
                    for (0..prov.model_count) |mi| {
                        const model_idx = prov.model_indices[mi];
                        if (model_idx < model.available_model_count) {
                            model.available_models[model_idx].key_source = "none";
                        }
                    }
                    break;
                }
            }
        },
        .toggle_rubric => {
            model.settings.rubric_enabled = !model.settings.rubric_enabled;
        },
        .toggle_reduced_motion => {
            model.settings.reduced_motion = !model.settings.reduced_motion;
            if (model.settings.reduced_motion) {
                model.pulse_phase = 0;
            }
        },
        .rubric_iterations_increment => {
            if (model.settings.rubric_max_iterations < 10) {
                model.settings.rubric_max_iterations += 1;
            }
        },
        .rubric_iterations_decrement => {
            if (model.settings.rubric_max_iterations > 1) {
                model.settings.rubric_max_iterations -= 1;
            }
        },
        .save_general_settings => {
            if (model.settings.saving_general) return;
            model.settings.saving_general = true;
            const escaped_prompt = escapeJsonString(model.allocator, model.settings.grader_prompt) catch return;
            const settings_body = std.fmt.allocPrint(
                model.allocator,
                "{{\"verification\":{{\"enabled\":{s},\"max_attempts\":{d}}}}}",
                .{ if (model.settings.rubric_enabled) "true" else "false", model.settings.rubric_max_iterations },
            ) catch return;
            const prompt_body = std.fmt.allocPrint(
                model.allocator,
                "{{\"content\":\"{s}\",\"expected_revision\":{d}}}",
                .{ escaped_prompt, model.settings.grader_prompt_revision },
            ) catch return;
            fx.fetch(.{
                .key = settings_general_key,
                .url = "http://127.0.0.1:8080/settings?user_id=native_sdk_chat",
                .method = .PATCH,
                .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                .body = settings_body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.settings_general_saved),
            });
            fx.fetch(.{
                .key = grader_prompt_key,
                .url = "http://127.0.0.1:8080/user/grader-prompt?user_id=native_sdk_chat",
                .method = .PUT,
                .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
                .body = prompt_body,
                .response = .buffered,
                .on_response = Effects.responseMsg(.grader_prompt_saved),
            });
        },
        .grader_prompt_input => |event| {
            const output = model.allocator.alloc(u8, model.settings.grader_prompt.len + 256) catch return;
            const next = (canvas.TextEditState{
                .text = model.settings.grader_prompt,
                .selection = .{ .anchor = model.settings.grader_prompt.len, .focus = model.settings.grader_prompt.len },
            }).apply(event, output) catch return;
            model.settings.grader_prompt = next.text;
        },
    }
}

fn syncModelFromLayout(model: *Model, layout: canvas.WidgetLayoutTree) void {
    if (!model.settings.visible or model.settings.section != .providers_models) return;
    for (layout.nodes) |node| {
        const widget = node.widget;
        if (widget.kind != .textarea) continue;
        if (!std.mem.eql(u8, widget.placeholder, "Search providers and models...")) continue;
        if (!std.mem.eql(u8, widget.text, model.settings.search_text)) {
            model.settings.search_text = model.allocator.dupe(u8, widget.text) catch widget.text;
            model.settings.search_runtime_text_synced = true;
        }
        if (widget.text_selection) |selection| {
            model.settings.search_selection = selection;
            model.settings.search_selection_programmatic = false;
        }
        return;
    }
}

fn saveSettingsModel(model: *Model, fx: *Effects, idx: usize) void {
    if (idx >= model.available_model_count or model.settings.saving_model) return;
    const m = model.available_models[idx];
    const escaped_id = escapeJsonString(model.allocator, m.id) catch return;
    // The PATCH field depends on which model role is being edited.
    const body = switch (model.settings.model_role) {
        .agent => std.fmt.allocPrint(
            model.allocator,
            "{{\"default_model\":\"{s}\"}}",
            .{escaped_id},
        ) catch return,
        .grader => std.fmt.allocPrint(
            model.allocator,
            "{{\"verification\":{{\"grader_model\":\"{s}\"}}}}",
            .{escaped_id},
        ) catch return,
        .title => std.fmt.allocPrint(
            model.allocator,
            "{{\"title_model\":\"{s}\"}}",
            .{escaped_id},
        ) catch return,
        .summarization => std.fmt.allocPrint(
            model.allocator,
            "{{\"summarization_model\":\"{s}\"}}",
            .{escaped_id},
        ) catch return,
    };
    model.settings.saving_model = true;
    model.settings.selected_model_idx = idx;
    model.settings.model_error = "";
    const fetch_key = model.allocFetchKey();
    fx.fetch(.{
        .key = fetch_key,
        .url = "http://127.0.0.1:8080/settings?user_id=native_sdk_chat",
        .method = .PATCH,
        .headers = &.{.{ .name = "Content-Type", .value = "application/json" }},
        .body = body,
        .response = .buffered,
        .on_response = Effects.responseMsg(.model_selected),
    });
}

fn providerComesBefore(a: ProviderInfo, b: ProviderInfo) bool {
    if (a.has_key != b.has_key) return a.has_key;
    return std.ascii.lessThanIgnoreCase(a.name, b.name);
}

fn sortSettingsProviders(settings: *SettingsState) void {
    var i: usize = 1;
    while (i < settings.provider_count) : (i += 1) {
        var j = i;
        while (j > 0 and providerComesBefore(settings.providers[j], settings.providers[j - 1])) : (j -= 1) {
            const tmp = settings.providers[j];
            settings.providers[j] = settings.providers[j - 1];
            settings.providers[j - 1] = tmp;
        }
    }
}

/// Smoothstep ease (standard ease-out) for entrance animations. Maps a raw
/// 0..1 progress to an eased 0..1: starts fast, settles gently — the
/// entrance curve. Matches the SDK's `.standard` easing (3t²-2t³).
fn smoothstep(t: f32) f32 {
    const x = std.math.clamp(t, 0, 1);
    return x * x * (3 - 2 * x);
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

/// Wall clock in milliseconds since the epoch (REALTIME, same source as
/// currentTimestamp). Used by the stream watchdog deadline.
pub fn nowMillis() i64 {
    var ts: std.posix.timespec = undefined;
    switch (std.posix.errno(std.posix.system.clock_gettime(.REALTIME, &ts))) {
        .SUCCESS => {
            const secs: i64 = @intCast(ts.sec);
            const nsecs: i64 = @intCast(ts.nsec);
            return secs * 1000 + @divTrunc(nsecs, 1_000_000);
        },
        else => return 0,
    }
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

/// ISO 8601 UTC timestamp for sorting (e.g. "2026-07-21T08:30:00Z").
fn currentTimestampISO(allocator: std.mem.Allocator) []const u8 {
    var ts: std.posix.timespec = undefined;
    switch (std.posix.errno(std.posix.system.clock_gettime(.REALTIME, &ts))) {
        .SUCCESS => {
            const total_secs: i64 = @intCast(ts.sec);
            const secs_per_day: i64 = 86400;
            const secs_per_hour: i64 = 3600;
            const secs_per_min: i64 = 60;
            const days_since_epoch: i64 = @divFloor(total_secs, secs_per_day);
            const day_secs = @mod(total_secs, secs_per_day);
            const hour: i64 = @divTrunc(day_secs, secs_per_hour);
            const min: i64 = @divTrunc(@mod(day_secs, secs_per_hour), secs_per_min);
            const sec: i64 = @mod(day_secs, secs_per_min);
            const civil = civilFromDays(days_since_epoch);
            return std.fmt.allocPrint(
                allocator,
                "{d:0>4}-{d:0>2}-{d:0>2}T{d:0>2}:{d:0>2}:{d:0>2}.000000+00:00",
                .{ civil.year, civil.month, civil.day, hour, min, sec },
            ) catch "";
        },
        else => return "",
    }
}

const CivilDate = struct { year: i64, month: i64, day: i64 };

fn civilFromDays(days: i64) CivilDate {
    // Howard Hinnant's algorithm: days since 1970-01-01 → civil date
    const z = days + 719468;
    const era: i64 = @divFloor(if (z >= 0) z else z - 146096, 146097);
    const doe: i64 = z - era * 146097;
    const yoe: i64 = @divFloor(doe - @divFloor(doe, 1460) + @divFloor(doe, 36524) - @divFloor(doe, 146096), 365);
    const y: i64 = yoe + era * 400;
    const doy: i64 = doe - (365 * yoe + @divFloor(yoe, 4) - @divFloor(yoe, 100));
    const mp: i64 = @divFloor(5 * doy + 2, 153);
    const d: i64 = doy - @divFloor(153 * mp + 2, 5) + 1;
    const m: i64 = if (mp < 10) mp + 3 else mp - 9;
    const year: i64 = if (m <= 2) y + 1 else y;
    return .{ .year = year, .month = m, .day = d };
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

/// Grows the message buffer (and the parallel group-extent cache) to at
/// least `capacity` items. The arena owns the memory, so old buffers are
/// never freed — growth is monotonic and cheap. Existing messages are
/// preserved.
pub fn ensureMessageCapacity(chat: *Chat, allocator: std.mem.Allocator, capacity: usize) void {
    if (capacity <= chat._message_capacity) return;
    const new_capacity = @max(capacity, chat._message_capacity * 2, default_message_capacity);
    const new_messages = allocator.alloc(ChatMessage, new_capacity) catch return;
    const new_extents = allocator.alloc(f32, new_capacity) catch return;
    if (chat.msg_count > 0) {
        @memcpy(new_messages[0..chat.msg_count], chat._messages[0..chat.msg_count]);
    }
    chat._messages = new_messages;
    chat._group_extents = new_extents;
    chat._message_capacity = new_capacity;
}

pub fn addMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
    ensureMessageCapacity(chat, allocator, chat.msg_count + 1);
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
    ensureMessageCapacity(chat, allocator, chat.msg_count + 1);
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

/// The rubric row is the turn's verification artifact, rendered in the same
/// shape as a tool row (glyph + label + muted preview). One row per turn:
/// update the trailing one if present, else append after the answer.
pub fn upsertRubricRow(chat: *Chat, allocator: std.mem.Allocator, status: []const u8, content: []const u8, collapsed: bool) void {
    var i: usize = chat.msg_count;
    while (i > 0) {
        i -= 1;
        if (std.mem.eql(u8, chat._messages[i].role, "rubric")) {
            chat._messages[i].tool_status = allocator.dupe(u8, status) catch return;
            chat._messages[i].content = allocator.dupe(u8, content) catch return;
            chat._messages[i].collapsed = collapsed;
            return;
        }
    }
    ensureMessageCapacity(chat, allocator, chat.msg_count + 1);
    chat._messages[chat.msg_count] = .{
        .id = chat.next_id,
        .role = allocator.dupe(u8, "rubric") catch return,
        .content = allocator.dupe(u8, content) catch return,
        .tool_name = allocator.dupe(u8, "Rubric") catch return,
        .tool_status = allocator.dupe(u8, status) catch return,
        .collapsed = collapsed,
    };
    chat.next_id += 1;
    chat.msg_count += 1;
    chat.messages = chat._messages[0..chat.msg_count];
}

/// Drop live rubric rows (an in-place revision re-adds the row at the next
/// evaluation, so the answer deltas keep appending to the same bubble).
pub fn removeRubricRows(chat: *Chat) void {
    var i: usize = 0;
    var out: usize = 0;
    while (i < chat.msg_count) : (i += 1) {
        if (std.mem.eql(u8, chat._messages[i].role, "rubric")) continue;
        if (out != i) chat._messages[out] = chat._messages[i];
        out += 1;
    }
    chat.msg_count = out;
    chat.messages = chat._messages[0..chat.msg_count];
}

/// Settle the rubric row from a terminal verification verdict (the done
/// event or the stored turn metadata on reload). Collapsed by default.
pub fn addRubricRowFromVerdict(chat: *Chat, allocator: std.mem.Allocator, verification: std.json.ObjectMap) void {
    const status = jsonString(verification.get("status") orelse return) orelse return;
    var passed: u32 = 0;
    var total: u32 = 0;
    var attempts: u32 = 0;
    var max_attempts: u32 = 0;
    var explanation: []const u8 = "";
    if (verification.get("attempts")) |a| {
        if (jsonCount(a)) |n| attempts = n;
    }
    if (verification.get("max_attempts")) |m| {
        if (jsonCount(m)) |n| max_attempts = n;
    }
    if (verification.get("evaluations")) |evals| {
        if (evals == .array and evals.array.items.len > 0) {
            const last = evals.array.items[evals.array.items.len - 1];
            if (last == .object) {
                if (last.object.get("explanation")) |x| {
                    if (x == .string) explanation = x.string;
                }
                if (last.object.get("criteria")) |c| {
                    if (c == .array) {
                        total = @intCast(c.array.items.len);
                        for (c.array.items) |ci| {
                            if (ci == .object) {
                                if (ci.object.get("passed")) |p| {
                                    if (p == .bool and p.bool) passed += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    const label: []const u8 = if (std.mem.eql(u8, status, "satisfied"))
        std.fmt.allocPrint(allocator, "Passed ({d}/{d})", .{ passed, total }) catch "Passed"
    else if (std.mem.eql(u8, status, "max_attempts_reached"))
        std.fmt.allocPrint(allocator, "Max revisions ({d}/{d})", .{ attempts, max_attempts }) catch "Max revisions"
    else if (std.mem.eql(u8, status, "grader_error"))
        "Check failed"
    else if (std.mem.eql(u8, status, "invalid_rubric"))
        "Invalid rubric"
    else if (std.mem.eql(u8, status, "cancelled"))
        "Cancelled"
    else
        "Complete";
    const content = std.fmt.allocPrint(allocator, "{d}/{d} criteria passed\n{s}", .{ passed, total, explanation }) catch label;
    upsertRubricRow(chat, allocator, label, content, true);
}

/// Serialize tool-call arguments for display in the tool bubble title.
/// Empty objects render as "" (matching the pre-existing behavior); non-empty
/// values render as compact JSON.
fn toolArgsString(model: *Model, v: std.json.Value) []const u8 {
    return switch (v) {
        .object => |obj| {
            if (obj.count() == 0) return "";
            return std.json.Stringify.valueAlloc(model.allocator, v, .{}) catch "";
        },
        .string => |s| s,
        else => "",
    };
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
        "http://127.0.0.1:8080/conversation/turns?user_id=native_sdk_chat&session_id={s}&limit=50",
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

pub fn appendToLastMessage(chat: *Chat, allocator: std.mem.Allocator, role: []const u8, content: []const u8) void {
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
    const right_panel: AppUi.Node = if (model.tools.visible)
        buildToolsPanel(ui, model)
    else if (model.settings.visible)
        buildSettingsPanel(ui, model)
    else
        buildChatPanel(ui, model);

    // Reading-width cap: the chat panel (messages + composer) and the
    // settings form both read better below ~full-width. Cap at 768px and
    // center the column in the space right of the sidebar. The cap is a
    // maximum only — on narrow windows the panel shrinks naturally.
    const panel_container = ui.row(.{
        .grow = 1,
        .main = .center,
        .cross = .stretch,
        .style_tokens = .{ .background = .surface },
    }, .{right_panel});

    const split = ui.split(.{
        .value = model.sidebar_split,
        .on_resize = AppUi.valueMsg(.sidebar_resized),
        .style_tokens = .{ .background = .surface, .border_color = .border },
        .grow = 1,
    }, .{
        buildSidebar(ui, model),
        panel_container,
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
    // Top section: search + New chat button (search above new chat)
    var top_nodes: [2]AppUi.Node = undefined;
    top_nodes[0] = ui.textField(.{
        .text = model.search_query,
        .placeholder = "Search chats...",
        .on_input = AppUi.inputMsg(.search_input),
        .semantics = .{ .label = "Search chats" },
        .style_tokens = .{ .background = .surface_subtle, .radius = .md },
    });
    top_nodes[1] = ui.button(.{
        .on_press = .new_chat,
        .variant = .ghost,
        .grow = 1,
        .style_tokens = .{ .background = .surface_pressed, .radius = .md },
        .semantics = .{ .label = "New chat" },
    }, "New chat");

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
    nav_nodes[0] = ui.row(.{
        .gap = 8,
        .padding = 8,
        .cross = .center,
        .on_press = .open_tools,
    }, .{
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
        ui.row(.{
            .gap = 8,
            .padding = 8,
            .cross = .center,
            .grow = 1,
            .on_press = .open_settings,
        }, .{
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

/// Estimate wrapped line count: ~150 chars per line at ~900px column width.
fn estimatedWrappedLines(content: []const u8) f32 {
    const chars_per_line: f32 = 150;
    const explicit_lines: f32 = @as(f32, @floatFromInt(std.mem.count(u8, content, "\n"))) + 1;
    const char_based_lines: f32 = @as(f32, @floatFromInt(content.len)) / chars_per_line;
    return @max(explicit_lines, char_based_lines);
}

/// Compare two chats by created_at for descending sort (newest first).
fn chatCreatedAtCmp(a: Chat, b: Chat) bool {
    if (a.created_at.len == 0) return false;
    if (b.created_at.len == 0) return true;
    return std.mem.order(u8, a.created_at, b.created_at) == .gt;
}

/// Sort the chats array by created_at descending (newest first).
fn sortChatsByCreatedAt(model: *Model) void {
    var sort_i: usize = 1;
    while (sort_i < model.chat_count) : (sort_i += 1) {
        var j = sort_i;
        while (j > 0 and chatCreatedAtCmp(model.chats[j], model.chats[j - 1])) : (j -= 1) {
            const tmp = model.chats[j];
            model.chats[j] = model.chats[j - 1];
            model.chats[j - 1] = tmp;
        }
    }
}

pub fn groupExtentEstimate(context: ?*const anyopaque, index: u64) f32 {
    const chat: *const Chat = @ptrCast(@alignCast(@constCast(context)));
    // Recompute cache if messages changed (different msg_count or scroll_gen).
    if (chat._group_extents_msg_count != chat.msg_count or
        chat._group_extents_scroll_gen != chat.transcript_scroll_generation)
    {
        computeGroupExtents(@constCast(chat));
    }
    if (index < chat._group_extents_count) {
        return chat._group_extents[index];
    }
    return 80;
}

/// Precompute the height of every message group. Called once per render
/// when the cache is invalid (messages changed). This makes the virtual
/// list's extent_estimate callback O(1) per item instead of O(n) per item,
/// eliminating the O(n²) scroll lag.
pub fn computeGroupExtents(chat: *Chat) void {
    const count = chat.msg_count;
    const line_height: f32 = 17.5;
    const timestamp_height: f32 = 16.25;
    const bubble_padding: f32 = 16;
    const group_gap: f32 = 8;
    var group_idx: usize = 0;
    var i: usize = 0;
    while (i < count) {
        const msg = &chat._messages[i];
        if (msg.isUser() or std.mem.eql(u8, msg.role, "system")) {
            const lines = estimatedWrappedLines(msg.content);
            chat._group_extents[group_idx] = bubble_padding + lines * line_height + timestamp_height + group_gap;
            group_idx += 1;
            i += 1;
        } else {
            const label_height: f32 = 16.25;
            const label_gap: f32 = 6;
            var group_height: f32 = label_height + label_gap;
            while (i < count and !chat._messages[i].isUser() and !std.mem.eql(u8, chat._messages[i].role, "system")) : (i += 1) {
                const m = &chat._messages[i];
                if (m.isTool()) {
                    const tool_lines = estimatedWrappedLines(m.content);
                    group_height += bubble_padding + tool_lines * line_height + group_gap;
                } else if (m.isReasoning()) {
                    group_height += 48;
                } else {
                    const lines = estimatedWrappedLines(m.content);
                    group_height += bubble_padding + lines * line_height + timestamp_height + group_gap;
                }
            }
            chat._group_extents[group_idx] = group_height;
            group_idx += 1;
        }
    }
    chat._group_extents_count = group_idx;
    chat._group_extents_msg_count = chat.msg_count;
    chat._group_extents_scroll_gen = chat.transcript_scroll_generation;
}

/// Seeds a deterministic synthetic transcript for the virtual-list stress
/// test. Roles and content lengths vary (user/assistant/tool/reasoning,
/// 10..600 chars, occasional newlines) to exercise variable-extent
/// estimation; the sequence is reproducible across runs.
pub fn seedStressTranscript(chat: *Chat, allocator: std.mem.Allocator, count: usize) void {
    ensureMessageCapacity(chat, allocator, count);
    var seed: u64 = 0x9E3779B97F4A7C15;
    var i: usize = 0;
    while (i < count) : (i += 1) {
        seed = seed *% 6364136223846793005 +% 1442695040888963407;
        const r = (seed >> 33) & 0xFFFF;
        const role: []const u8 = if (i % 2 == 0)
            "user"
        else if (i % 13 == 0)
            "tool"
        else if (i % 17 == 0)
            "reasoning"
        else
            "assistant";
        const len: usize = 10 + (r % 590); // 10..600 chars
        const content = allocator.alloc(u8, len) catch return;
        var j: usize = 0;
        while (j < len) : (j += 1) {
            const c = (seed >> 32) & 0xFF;
            seed = seed *% 6364136223846793005 +% 1442695040888963407;
            content[j] = if (j % 97 == 0) '\n' else 'a' + @as(u8, @intCast(c % 26));
        }
        chat._messages[chat.msg_count] = .{
            .id = chat.next_id,
            .role = role,
            .content = content,
            .tool_name = if (std.mem.eql(u8, role, "tool")) "stress_tool" else "",
            .tool_status = if (std.mem.eql(u8, role, "tool")) "done" else "",
            .timestamp = "",
        };
        chat.next_id += 1;
        chat.msg_count += 1;
    }
    chat.messages = chat._messages[0..chat.msg_count];
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

pub fn addHistoryMessage(chat: *Chat, allocator: std.mem.Allocator, item: std.json.Value) void {
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
        ensureMessageCapacity(chat, allocator, chat.msg_count + 1);
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

/// A model is selectable in the composer menu when it has a key and its
/// name or provider matches the menu's search text.
fn modelMatchesSearch(model: *const Model, m: ModelOption) bool {
    if (model.model_menu_search.len == 0) return true;
    return containsIgnoreCase(m.name, model.model_menu_search) or
        containsIgnoreCase(m.provider_display, model.model_menu_search);
}

/// The composer's model picker: an anchored dropdown listing ready-to-use
/// models (key present) with a search field. Selecting persists the choice
/// via the settings API; "Manage models…" opens the full catalog.
fn buildModelMenu(ui: *AppUi, model: *const Model) AppUi.Node {
    if (!model.model_menu_open) return ui.text(.{}, "");

    // Cap the catalog (8192) with a bounded array; the scroll region
    // handles overflow (common practice: ~8 visible rows, rest scrolls).
    const max_menu_items = 256;
    var rows: [max_menu_items]AppUi.Node = undefined;
    var row_count: usize = 0;
    var filtered_idx: usize = 0;

    for (0..model.available_model_count) |i| {
        if (row_count >= max_menu_items) break;
        const m = model.available_models[i];
        if (std.mem.eql(u8, m.key_source, "none")) continue;
        if (!modelMatchesSearch(model, m)) continue;
        const is_current = i == model.selected_model_idx;
        const label = std.fmt.allocPrint(ui.arena, "{s} · {s}", .{ m.provider_display, m.name }) catch m.name;
        rows[row_count] = ui.el(.menu_item, .{
            .key = .{ .int = filtered_idx },
            .text = label,
            .selected = is_current,
            .on_press = .{ .model_menu_select = filtered_idx },
        }, .{});
        row_count += 1;
        filtered_idx += 1;
    }

    // The list scrolls within a fixed-height region; the search field and
    // the "Manage models…" footer stay pinned above and below it.
    const scroll_rows: AppUi.Node = if (row_count > 0)
        ui.scroll(.{
            .height = 260,
            .max_width = 360,
            .style_tokens = .{ .background = .surface },
        }, .{
            ui.column(.{ .gap = 2 }, rows[0..row_count]),
        })
    else
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "No models match");

    return ui.el(.dropdown_menu, .{
        .anchor = .above,
        .anchor_alignment = .start,
        .anchor_offset = 4,
        .on_dismiss = .close_model_menu,
        .min_width = 280,
        .max_width = 360,
    }, .{
        ui.textField(.{
            .text = model.model_menu_search,
            .placeholder = "Search models…",
            .on_input = AppUi.inputMsg(.model_menu_search_input),
            .semantics = .{ .label = "Search models" },
            .style_tokens = .{ .background = .surface_subtle, .radius = .md },
        }),
        scroll_rows,
        ui.el(.separator, .{}, .{}),
        ui.el(.menu_item, .{
            .text = "Manage models…",
            .on_press = .model_menu_manage,
        }, .{}),
    });
}

fn containsIgnoreCase(haystack: []const u8, needle: []const u8) bool {
    if (needle.len == 0) return true;
    if (needle.len > haystack.len) return false;
    var i: usize = 0;
    while (i + needle.len <= haystack.len) : (i += 1) {
        if (std.ascii.eqlIgnoreCase(haystack[i .. i + needle.len], needle)) return true;
    }
    return false;
}

fn upperAscii(allocator: std.mem.Allocator, value: []const u8) []const u8 {
    const out = allocator.dupe(u8, value) catch return value;
    for (out) |*c| c.* = std.ascii.toUpper(c.*);
    return out;
}

fn buildSettingsPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    const search = model.settings.search_text;

    var children: [3]AppUi.Node = undefined;
    var child_count: usize = 0;
    var content_children: [4]AppUi.Node = undefined;
    var content_count: usize = 0;

    // Header
    children[child_count] = ui.row(.{ .gap = 12, .padding = 16, .cross = .center, .style_tokens = .{ .background = .surface } }, .{
        ui.text(.{ .size = .heading }, "Settings"),
        ui.spacer(1),
    });
    child_count += 1;

    if (model.settings.section == .providers_models) {
    // Search bar + provider-grouped model catalog
    var list_nodes: [max_visible_settings_rows + 4]AppUi.Node = undefined;
    var list_node_count: usize = 0;

    // Role toggle: which model role the catalog edits.
    var role_nodes: [4]AppUi.Node = undefined;
    const roles = [_]struct { role: ModelRole, label: []const u8 }{
        .{ .role = .agent, .label = "Agent" },
        .{ .role = .grader, .label = "Grader" },
        .{ .role = .title, .label = "Title" },
        .{ .role = .summarization, .label = "Summary" },
    };
    for (roles, 0..) |r, i| {
        const active = model.settings.model_role == r.role;
        role_nodes[i] = ui.button(.{
            .on_press = .{ .set_model_role = r.role },
            .variant = if (active) .primary else .ghost,
            .size = .sm,
        }, r.label);
    }
    const role_slice: []const AppUi.Node = role_nodes[0..4];
    list_nodes[list_node_count] = ui.row(.{ .gap = 6, .padding = 4, .cross = .center }, role_slice);
    list_node_count += 1;

    // Search input
    list_nodes[list_node_count] = blk: {
        var field = ui.el(.textarea, .{
            .text = model.settings.search_text,
            .placeholder = "Search providers and models...",
            .on_input = AppUi.inputMsg(.settings_search),
            .height = 36,
            .style_tokens = .{ .background = .surface_subtle, .border_color = .border },
        }, .{});
        if (model.settings.search_selection_programmatic) {
            field.widget.text_selection = model.settings.search_selection;
        }
        break :blk field;
    };
    list_node_count += 1;

    if (model.settings.model_error.len > 0) {
        list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .destructive }, .wrap = true }, model.settings.model_error);
        list_node_count += 1;
    }

    var rendered_provider_count: usize = 0;

    if (model.settings.loading) {
        list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Loading...");
        list_node_count += 1;
    } else {
        const search_active = search.len > 0;
        for (0..model.settings.provider_count) |pi| {
            if (list_node_count >= max_visible_settings_rows) {
                list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, "More models available. Search to narrow the catalog.");
                list_node_count += 1;
                break;
            }
            const p = &model.settings.providers[pi];
            const provider_matches = search_active and containsIgnoreCase(p.name, search);
            var visible_model_count: usize = 0;
            for (0..p.model_count) |mi| {
                const m = model.available_models[p.model_indices[mi]];
                if (!search_active or provider_matches or containsIgnoreCase(m.name, search)) {
                    visible_model_count += 1;
                }
            }
            if (search_active and !provider_matches and visible_model_count == 0) continue;

            rendered_provider_count += 1;
            const header_name = upperAscii(ui.arena, p.name);
            list_nodes[list_node_count] = ui.row(.{ .gap = 8, .cross = .center, .padding = 8, .style_tokens = .{ .background = .surface_subtle, .radius = .sm } }, .{
                ui.text(.{ .size = .sm, .grow = 1, .style_tokens = .{ .foreground = .text } }, header_name),
            });
            list_node_count += 1;

            if (p.model_count == 0) {
                list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "No models available");
                list_node_count += 1;
                continue;
            }

            for (0..p.model_count) |mi| {
                if (list_node_count >= max_visible_settings_rows) {
                    list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, "More models available. Search to narrow the catalog.");
                    list_node_count += 1;
                    break;
                }
                const model_idx = p.model_indices[mi];
                const m = model.available_models[model_idx];
                if (search_active and !provider_matches and !containsIgnoreCase(m.name, search)) continue;
                const active_id: []const u8 = switch (model.settings.model_role) {
                    .agent => model.settings.default_model_id,
                    .grader => model.settings.grader_model_id,
                    .title => model.settings.title_model_id,
                    .summarization => model.settings.summarization_model_id,
                };
                const is_selected = std.mem.eql(u8, m.id, active_id);
                const state_text: []const u8 = if (!is_selected and !p.has_key) "Add key" else "";
                const model_label = if (is_selected)
                    std.fmt.allocPrint(ui.arena, "  {s}  ✓", .{m.name}) catch m.name
                else
                    std.fmt.allocPrint(ui.arena, "  {s}", .{m.name}) catch m.name;
                list_nodes[list_node_count] = ui.row(.{
                    .gap = 8,
                    .cross = .center,
                    .padding = 6,
                    .on_press = .{ .select_model = model_idx },
                    .style_tokens = if (is_selected) .{ .background = .surface_pressed, .radius = .sm } else .{ .background = .surface, .radius = .sm },
                }, .{
                    ui.text(.{ .size = .sm, .grow = 1, .style_tokens = .{ .foreground = if (is_selected) .text else .text_muted } }, model_label),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = if (is_selected) .success else .text_muted } }, state_text),
                });
                list_node_count += 1;
            }
        }
        if (rendered_provider_count == 0) {
            list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "No providers or models found");
            list_node_count += 1;
        }
    }

    content_children[content_count] = ui.el(.card, .{
        .padding = 12,
        .style_tokens = .{ .background = .surface, .radius = .md },
    }, .{
        ui.column(.{ .gap = 8 }, list_nodes[0..list_node_count]),
    });
    content_count += 1;
    }

    if (model.settings.key_modal_visible) {
        const title = std.fmt.allocPrint(ui.arena, "Add {s} key", .{model.available_models[model.settings.pending_model_idx].provider_display}) catch "Add API key";
        const subtitle = std.fmt.allocPrint(ui.arena, "Required to use {s}.", .{model.settings.pending_model_name}) catch "Required to use this model.";
        var modal_nodes: [5]AppUi.Node = undefined;
        var modal_count: usize = 0;
        modal_nodes[modal_count] = ui.text(.{ .size = .heading }, title);
        modal_count += 1;
        modal_nodes[modal_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, subtitle);
        modal_count += 1;
        modal_nodes[modal_count] = ui.el(.textarea, .{
            .text = if (model.settings.key_visible) model.settings.key_input else "",
            .placeholder = "Enter API key...",
            .on_input = AppUi.inputMsg(.add_key_input),
            .height = 36,
            .style_tokens = .{ .background = .surface_subtle, .border_color = .border },
        }, .{});
        modal_count += 1;
        if (model.settings.key_error.len > 0) {
            modal_nodes[modal_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .destructive }, .wrap = true }, model.settings.key_error);
            modal_count += 1;
        }
        modal_nodes[modal_count] = ui.row(.{ .gap = 8, .cross = .center }, .{
            ui.button(.{ .on_press = .{ .toggle_key_visibility = model.settings.pending_provider_idx }, .variant = .ghost, .size = .sm }, if (model.settings.key_visible) "Hide" else "Show"),
            ui.spacer(1),
            ui.button(.{ .on_press = .{ .add_key_cancel = model.settings.pending_provider_idx }, .variant = .ghost, .size = .sm }, "Cancel"),
            ui.button(.{ .on_press = .{ .add_key_submit = model.settings.pending_provider_idx }, .variant = .primary, .size = .sm }, if (model.settings.key_testing) "Testing..." else "Test & Save"),
        });
        modal_count += 1;

        content_children[content_count] = ui.el(.card, .{
            .padding = 18,
            .style_tokens = .{ .background = .surface_subtle, .radius = .lg },
        }, .{
            ui.column(.{ .gap = 10 }, modal_nodes[0..modal_count]),
        });
        content_count += 1;
    }

    if (model.settings.section == .general) {
    // Rubric settings
    content_children[content_count] = ui.el(.card, .{
        .padding = 16,
        .style_tokens = .{ .background = .surface, .radius = .md },
    }, .{
        ui.column(.{ .gap = 8 }, .{
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Rubric"),
            ui.row(.{ .gap = 12, .cross = .center }, .{
                ui.text(.{}, "Enable rubric check"),
                ui.spacer(1),
                ui.button(.{
                    .on_press = .toggle_rubric,
                    .variant = if (model.settings.rubric_enabled) .primary else .secondary,
                }, if (model.settings.rubric_enabled) "On" else "Off"),
            }),
            ui.row(.{ .gap = 12, .cross = .center }, .{
                ui.text(.{}, "Max iterations"),
                ui.spacer(1),
                ui.button(.{ .on_press = .rubric_iterations_decrement, .variant = .ghost }, "−"),
                ui.text(.{}, std.fmt.allocPrint(ui.arena, "{d}", .{model.settings.rubric_max_iterations}) catch "3"),
                ui.button(.{ .on_press = .rubric_iterations_increment, .variant = .ghost }, "+"),
            }),
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Grader prompt"),
            ui.el(.textarea, .{
                .text = model.settings.grader_prompt,
                .placeholder = "Enter rubric criteria...",
                .on_input = AppUi.inputMsg(.grader_prompt_input),
                .height = 100,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border },
            }, .{}),
            if (model.settings.saving_general)
                ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Saving...")
            else
                ui.button(.{ .on_press = .save_general_settings, .variant = .primary }, "Save"),
        }),
    });
    content_count += 1;

    // Appearance
    content_children[content_count] = ui.el(.card, .{
        .padding = 16,
        .style_tokens = .{ .background = .surface, .radius = .md },
    }, .{
        ui.column(.{ .gap = 8 }, .{
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Appearance"),
            ui.row(.{ .gap = 12, .cross = .center }, .{
                ui.text(.{}, "Theme"),
                ui.spacer(1),
                ui.button(.{
                    .on_press = .toggle_theme,
                    .variant = .secondary,
                }, switch (model.theme_mode) {
                    .dark => "Switch to Light",
                    .light => "Switch to Dark",
                }),
            }),
            ui.row(.{ .gap = 12, .cross = .center }, .{
                ui.text(.{}, "Reduced motion"),
                ui.spacer(1),
                ui.button(.{
                    .on_press = .toggle_reduced_motion,
                    .variant = if (model.settings.reduced_motion) .primary else .secondary,
                }, if (model.settings.reduced_motion) "On" else "Off"),
            }),
        }),
    });
    content_count += 1;

    // About
    content_children[content_count] = ui.el(.card, .{
        .padding = 16,
        .style_tokens = .{ .background = .surface, .radius = .md },
    }, .{
        ui.column(.{ .gap = 4 }, .{
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "About"),
            ui.text(.{}, "Assistant"),
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Backend: http://127.0.0.1:8080"),
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "User: native_sdk_chat"),
        }),
    });
    content_count += 1;
    }

    const sidebar = ui.el(.card, .{
        .width = 120,
        .padding = 6,
        .style_tokens = .{ .background = .surface, .radius = .md },
    }, .{
        ui.column(.{ .gap = 6 }, .{
            ui.button(.{
                .on_press = .settings_providers_models,
                .variant = if (model.settings.section == .providers_models) .primary else .secondary,
                .size = .sm,
                .width = 104,
                .padding = 12,
            }, "Models"),
            ui.button(.{
                .on_press = .settings_general,
                .variant = if (model.settings.section == .general) .primary else .secondary,
                .size = .sm,
                .width = 104,
                .padding = 12,
            }, "General"),
        }),
    });

    const content_slice: []const AppUi.Node = content_children[0..content_count];
    children[child_count] = ui.row(.{ .gap = 12, .cross = .start }, .{
        sidebar,
        ui.column(.{ .gap = 12, .grow = 1 }, content_slice),
    });
    child_count += 1;

    const children_slice: []const AppUi.Node = children[0..child_count];
    // Entrance animation: fade-in only. The settings panel is the heaviest
    // view (header + search + full model catalog); a scale transform forces
    // re-rasterization of the whole panel each frame, which reads as lag.
    // Opacity alone is GPU-cheap and still prevents the teleport.
    const settings_eased = smoothstep(model.settings_entrance);

    return ui.scroll(.{
        .grow = 1,
        .max_width = 768,
        .padding = 12,
        .opacity = settings_eased,
        .style_tokens = .{ .background = .surface },
    }, .{
        ui.column(.{ .gap = 12 }, children_slice),
    });
}

fn buildToolsPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    var children: [4]AppUi.Node = undefined;
    var child_count: usize = 0;

    children[child_count] = ui.row(.{ .gap = 12, .padding = 16, .cross = .center, .style_tokens = .{ .background = .surface } }, .{
        ui.text(.{ .size = .heading }, "Tools"),
        ui.spacer(1),
    });
    child_count += 1;

    var tab_nodes: [2]AppUi.Node = undefined;
    const tabs = [_]struct { section: ToolsSection, label: []const u8 }{
        .{ .section = .builtin, .label = "Built-in" },
        .{ .section = .connections, .label = "Connections" },
    };
    for (tabs, 0..) |t, i| {
        const active = model.tools.section == t.section;
        tab_nodes[i] = ui.button(.{
            .on_press = switch (t.section) {
                .builtin => .tools_tab_builtin,
                .connections => .tools_tab_connections,
            },
            .variant = if (active) .primary else .ghost,
            .size = .sm,
        }, t.label);
    }
    const tab_slice: []const AppUi.Node = tab_nodes[0..2];
    children[child_count] = ui.row(.{ .gap = 6, .padding = 4, .cross = .center }, tab_slice);
    child_count += 1;

    children[child_count] = ui.scroll(.{ .grow = 1, .padding = 16 }, .{
        ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Loading…"),
    });
    child_count += 1;

    return ui.column(.{ .grow = 1, .style_tokens = .{ .background = .background } }, children[0..child_count]);
}

fn buildChatPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    const chat = &model.chats[model.active_chat_idx];
    const count = chat.msg_count;

    // Invalidate the group extents cache if messages or scroll generation changed.
    // The virtual list calls groupExtentEstimate for every visible item on each
    // scroll frame. Without caching, that's O(n) per item × O(n) items = O(n²).
    // With caching, it's O(n) once + O(1) per item = O(n) total.

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
            // Empty state: heading, subtitle, and suggestions stagger in
            // (fade + 8px rise, ~90ms between nodes, ~270ms total). This is
            // the first-time tier — delight is allowed. Reduced motion:
            // fade only, no rise.
            const e0 = smoothstep(model.empty_entrance);
            const e1 = smoothstep(model.empty_entrance - 0.15);
            const e2 = smoothstep(model.empty_entrance - 0.3);
            const rise: f32 = if (model.settings.reduced_motion) 0.0 else 8.0;
            const fade_node = struct {
                fn build(_: *AppUi, progress: f32, dy: f32, node_in: AppUi.Node) AppUi.Node {
                    if (progress >= 1) return node_in;
                    var node = node_in;
                    node.widget.opacity = std.math.clamp(progress, 0, 1);
                    node.widget.transform = canvas.Affine.translate(0, dy * (1 - std.math.clamp(progress, 0, 1)));
                    return node;
                }
            }.build;
            const heading = ui.text(.{ .size = .heading }, "How can I help?");
            const eyebrow = ui.text(.{
                .size = .sm,
                .padding = 8,
                .style_tokens = .{ .foreground = .accent, .background = .surface_subtle, .radius = .md },
            }, "YOUR ASSISTANT");
            const subtitle = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Ask me anything, or try one of these:");
            const suggestions = ui.row(.{ .gap = 8 }, .{
                ui.button(.{ .on_press = .suggestion_inbox, .variant = .ghost, .style = .{ .radius = 999 } }, "Triage my inbox"),
                ui.button(.{ .on_press = .suggestion_summary, .variant = .ghost, .style = .{ .radius = 999 } }, "Draft a weekly summary"),
                ui.button(.{ .on_press = .suggestion_contacts, .variant = .ghost, .style = .{ .radius = 999 } }, "Find contacts in marketing"),
            });
            children[child_count] = ui.column(.{
                .grow = 1,
                .padding = 32,
                .gap = 16,
                .cross = .center,
                .main = .center,
                .style_tokens = .{ .background = .surface },
            }, .{
                fade_node(ui, e0, rise, eyebrow),
                fade_node(ui, e1, rise, heading),
                fade_node(ui, e2, rise, subtitle),
                fade_node(ui, e2, rise, suggestions),
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

    // HITL bar — DISABLED FOR SHIP: the backend never emits interrupts
    // (loop._should_interrupt returns false), so has_pending is always
    // false and this block never renders. Kept dormant for re-enable.
    if (chat.has_pending) {
        const approve_text = std.fmt.allocPrint(ui.arena, "Approve: {s}?", .{chat.pending_tool}) catch "Approve?";
        const hitl_eased = smoothstep(model.hitl_entrance);
        const hitl_dy = if (model.settings.reduced_motion) 0.0 else 8.0 * (1 - hitl_eased);
        children[child_count] = ui.row(.{
            .gap = 12,
            .padding = 12,
            .cross = .center,
            .opacity = hitl_eased,
            .transform = canvas.Affine.translate(0, hitl_dy),
            .style_tokens = .{ .background = .surface, .radius = .md },
        }, .{
            ui.text(.{ .grow = 1 }, approve_text),
            ui.button(.{ .on_press = .approve, .style_tokens = .{ .foreground = .success } }, "Approve"),
            ui.button(.{ .on_press = .reject, .variant = .ghost, .style_tokens = .{ .foreground = .destructive } }, "Reject"),
        });
        child_count += 1;
    }

    // Composer: bordered container with textarea + model button + Send/Stop button
    // Composer model label: model name only (the "Provider · Name" form is
    // for the settings panel). Bounded width keeps the bottom row from
    // overflowing on narrow windows.
    const model_label = if (model.available_model_count > 0)
        model.available_models[model.selected_model_idx].name
    else
        "DeepSeek V4 Flash 0731";
    // Always a button — even while streaming — so the user can switch the
    // model mid-run; the new selection applies from the next send. Keeping
    // the button (chevron + padding) in both states also stops the row's
    // trailing group from shifting when a stream starts.
    const model_button: AppUi.Node = if (model.available_model_count > 0)
        ui.button(.{ .on_press = .toggle_model_menu, .variant = .ghost, .max_width = 260, .icon = "chevron-down", .icon_placement = .trailing, .style_tokens = .{ .foreground = .text_muted } }, model_label)
    else
        ui.text(.{}, "");

    // The model picker: the button is the dropdown's anchor (stack sibling).
    // The stack exists only while open — a closed menu's empty placeholder
    // would layer on top of the button and swallow its clicks.
    const model_selector: AppUi.Node = if (model.available_model_count > 0)
        if (model.model_menu_open)
            ui.stack(.{}, .{ model_button, buildModelMenu(ui, model) })
        else
            model_button
    else
        model_button;

    const textarea_height: f32 = chat.last_textarea_height;

    const composer_textarea = blk: {
        var field = ui.el(.textarea, .{
            .text = model.inputText(),
            .placeholder = "Type a message... (Enter to send)",
            .on_input = AppUi.inputMsg(.input_changed),
            .on_submit = .send_message,
            .semantics = .{ .label = "Message" },
            .height = textarea_height,
            .style_tokens = .{ .background = .surface_subtle, .border_color = .surface_subtle, .radius = .md },
        }, .{});
        if (chat.draft_selection_programmatic) {
            field.widget.text_selection = chat.draft_selection;
        }
        field.widget.style.radius = 12;
        field.widget.style.focus_ring = canvas.Color.rgba8(0, 0, 0, 0);
        break :blk field;
    };

    const send_button: AppUi.Node = if (chat.streaming)
        ui.button(.{ .on_press = .cancel, .variant = .ghost, .min_width = 76 }, "Stop")
    else
        ui.button(.{ .on_press = .send_message, .variant = .primary, .icon = "send", .min_width = 76 }, "Send");

    // Textarea in its own nested card — an outer shell (hairline border +
    // padding + large radius) with the textarea as the inner core (its own
    // surface + concentric radius). The two-tone nesting reads as machined
    // hardware instead of a flat box.
    const ci = &chat.context_info;
    const tokens_text = std.fmt.allocPrint(ui.arena, "{d} in / {d} out", .{ ci.input_tokens, ci.output_tokens }) catch "";
    const freshness_style: canvas.ColorTokenName = if (std.mem.eql(u8, ci.freshness, "live")) .success else .text_muted;

    children[child_count] = ui.column(.{
        .grow = 0,
        .padding = 6,
        .gap = 0,
        .style_tokens = .{ .background = .surface, .border_color = .border, .radius = .lg },
    }, .{
        composer_textarea,
    });
    child_count += 1;

    // Bottom row: separate, static, sits below the textarea card.
    children[child_count] = ui.row(.{
        .gap = 6,
        .cross = .center,
        .grow = 0,
        .padding = 12,
    }, .{
        model_selector,
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "•"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, tokens_text),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "•"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = freshness_style } }, ci.freshness),
        ui.spacer(1),
        send_button,
    });
    child_count += 1;

    const children_slice: []const AppUi.Node = children[0..child_count];
    // Composer entrance: a one-time fade-up on app start (reduced motion:
    // fade only). The composer is the app's home surface — it should settle
    // in, not teleport.
    const composer_eased = smoothstep(model.composer_entrance);
    const composer_rise: f32 = if (model.settings.reduced_motion) 0.0 else 6.0;
    var composer_node = ui.column(.{
        .style_tokens = .{ .background = .surface },
        .gap = 2,
        .min_width = 320,
        .max_width = 768,
        .grow = 1,
        .padding = 12,
    }, children_slice);
    composer_node.widget.opacity = std.math.clamp(composer_eased, 0, 1);
    composer_node.widget.transform = canvas.Affine.translate(0, composer_rise * (1 - std.math.clamp(composer_eased, 0, 1)));
    return composer_node;
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
    return ui.column(.{ .gap = 6, .cross = .start }, .{
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, "Assistant"),
        ui.column(.{ .gap = 8 }, child_nodes),
    });
}

fn buildChildBubble(ui: *AppUi, msg: *const ChatMessage, status_text: []const u8) AppUi.Node {
    if (msg.isReasoning()) {
        if (msg.content.len == 0) return ui.text(.{}, "");
        const expand_label: []const u8 = if (msg.collapsed) "Expand" else "Collapse";
        const display_text: []const u8 = if (msg.collapsed) blk: {
            const newline = std.mem.indexOf(u8, msg.content, "\n");
            break :blk if (newline) |n| msg.content[0..n] else msg.content;
        } else msg.content;
        const toggle_label = std.fmt.allocPrint(ui.arena, "Thinking  {s}", .{expand_label}) catch expand_label;
        return ui.column(.{
            .gap = 6,
            .cross = .start,
            .style_tokens = .{ .border_color = .border },
        }, .{
                ui.row(.{ .gap = 8, .cross = .center }, .{
                    ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, "circle-dot"),
                    ui.el(.text, .{
                        .on_press = .{ .toggle_bubble = msg.id },
                        .text = toggle_label,
                        .size = .sm,
                        .style_tokens = .{ .foreground = .accent },
                        .semantics = .{ .role = .button, .label = expand_label },
                    }, .{}),
                }),
                ui.text(.{ .wrap = true, .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, display_text),
        });
    } else if (std.mem.eql(u8, msg.role, "rubric")) {
        // Rubric row: the turn's verification artifact, same shape as a
        // tool row — glyph + label + muted preview. Checking/revising
        // render as the running state (accent); settled renders the
        // toggle label (collapsed shows the first line).
        const icon: []const u8 = if (std.mem.startsWith(u8, msg.tool_status, "Passed")) "check-circle" else "circle-dot";
        const running = std.mem.startsWith(u8, msg.tool_status, "checking") or std.mem.startsWith(u8, msg.tool_status, "Revising");
        if (running) {
            return ui.column(.{
                .gap = 4,
                .cross = .start,
                .style_tokens = .{ .border_color = .border },
            }, .{
                    ui.row(.{ .gap = 8, .cross = .center }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, icon),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, msg.tool_name),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, msg.tool_status),
                    }),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, msg.content),
            });
        } else {
            const display: []const u8 = if (msg.collapsed) blk: {
                const newline = std.mem.indexOf(u8, msg.content, "\n");
                break :blk if (newline) |n| msg.content[0..n] else msg.content;
            } else msg.content;
            const label = std.fmt.allocPrint(ui.arena, "Rubric  {s}", .{msg.tool_status}) catch "Rubric";
            return ui.column(.{
                .gap = 4,
                .cross = .start,
                .style_tokens = .{ .border_color = .border },
            }, .{
                    ui.row(.{ .gap = 8, .cross = .center }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, icon),
                        ui.el(.text, .{
                            .on_press = .{ .toggle_bubble = msg.id },
                            .text = label,
                            .size = .sm,
                            .style_tokens = .{ .foreground = .accent },
                            .semantics = .{ .role = .button, .label = msg.tool_status },
                        }, .{}),
                    }),
                    if (display.len > 0)
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, display)
                    else
                        ui.text(.{}, ""),
            });
        }
    } else if (msg.isTool()) {
        const icon = toolIconName(msg.tool_name);
        if (std.mem.eql(u8, msg.tool_status, "running")) {
            return ui.column(.{
                .gap = 4,
                .cross = .start,
                .style_tokens = .{ .border_color = .border },
            }, .{
                    ui.row(.{ .gap = 8, .cross = .center }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, icon),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .accent } }, msg.tool_name),
                        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "running..."),
                    }),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, msg.content),
            });
        } else {
            const display_result: []const u8 = if (msg.collapsed) blk: {
                const newline = std.mem.indexOf(u8, msg.tool_result, "\n");
                break :blk if (newline) |n| msg.tool_result[0..n] else msg.tool_result;
            } else msg.tool_result;
            const tool_label = std.fmt.allocPrint(ui.arena, "{s}  {s}", .{ msg.tool_name, msg.tool_status }) catch msg.tool_name;
            return ui.column(.{
                .gap = 4,
                .cross = .start,
                .style_tokens = .{ .border_color = .border },
            }, .{
                    ui.row(.{ .gap = 8, .cross = .center }, .{
                        ui.iconGlyph(.{ .style_tokens = .{ .foreground = .accent } }, toolIconName(msg.tool_name)),
                        ui.el(.text, .{
                            .on_press = .{ .toggle_bubble = msg.id },
                            .text = tool_label,
                            .size = .sm,
                            .style_tokens = .{ .foreground = .accent },
                            .semantics = .{ .role = .button, .label = msg.tool_status },
                        }, .{}),
                    }),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, display_result),
            });
        }
    } else {
        // Assistant text bubble — nested card (outer shell + inner core).
        if (msg.isEmpty()) {
            const typing_text: []const u8 = if (status_text.len > 0) status_text else "typing...";
            return ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, typing_text);
        } else {
            return ui.column(.{
                .gap = 0,
                .padding = 4,
                .style_tokens = .{ .background = .surface, .border_color = .border, .radius = .lg },
            }, .{
                ui.column(.{
                    .gap = 0,
                    .padding = 10,
                    .style_tokens = .{ .background = .surface_subtle, .radius = .md },
                }, .{
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
        // User message: right-aligned, no role label. Nested card: an outer
        // shell (hairline border + padding + large radius) with the content
        // as the inner core (its own surface + concentric radius).
        const ts_node: AppUi.Node = if (msg.timestamp.len > 0)
            ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, msg.timestamp)
        else
            ui.text(.{}, "");
        return ui.row(.{
            .main = .end,
            .cross = .start,
        }, .{
            ui.column(.{
                .gap = 0,
                .padding = 4,
                .style_tokens = .{ .background = .surface, .border_color = .border, .radius = .lg },
            }, .{
                ui.column(.{
                    .gap = 0,
                    .padding = 10,
                    .style_tokens = .{ .background = .surface_subtle, .radius = .md },
                }, .{
                    ui.text(.{ .wrap = true }, msg.content),
                    ts_node,
                }),
            }),
        });
    } else if (std.mem.eql(u8, msg.role, "system")) {
        // System/error: muted, no card, no role label, padded to align with messages
        const is_stream_error = std.mem.startsWith(u8, msg.content, "Stream error");
        return ui.row(.{}, .{
            ui.column(.{ .gap = 6, .cross = .start }, .{
                ui.text(.{
                    .size = .sm,
                    .style_tokens = .{ .foreground = .text_muted },
                    .wrap = true,
                }, msg.content),
                if (is_stream_error)
                    ui.button(.{ .on_press = .retry, .variant = .ghost, .size = .sm }, "Retry")
                else
                    ui.text(.{}, ""),
            }),
        });
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
    // Stress-test mode: the transcript is seeded locally; skip all fetches
    // so the test is deterministic and does not need the backend.
    if (model.stress_mode) return;
    // Prime the entrance animation tick (composer fade-up) — nothing else
    // fires the first tick at a quiet startup, so without this the chat
    // panel would stay at entrance opacity 0 (blank).
    fx.startTimer(.{ .key = 1, .interval_ms = 60, .mode = .one_shot, .on_fire = Effects.timerMsg(.tick) });
    // Fetch sessions first; models and history are chained from the
    // response handlers (see fetchModels / fetchActiveChatHistory) so the
    // startup never fires concurrent connects to the same host — the Zig
    // std threaded Io can panic with ISCONN on that race.
    fx.fetch(.{
        .key = sessions_key,
        .url = "http://127.0.0.1:8080/conversation/sessions?user_id=native_sdk_chat",
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.sessions_loaded),
    });
}

fn fetchModels(fx: *Effects) void {
    fx.fetch(.{
        .key = models_key,
        .url = "http://127.0.0.1:8080/models?user_id=native_sdk_chat",
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.models_loaded),
    });
}

fn fetchSettingsCatalog(fx: *Effects) void {
    fx.fetch(.{
        .key = settings_key,
        .url = "http://127.0.0.1:8080/settings/model-catalog?user_id=native_sdk_chat&max_models_per_provider=20&max_providers=64",
        .method = .GET,
        .headers = &.{.{ .name = "Accept", .value = "application/json" }},
        .response = .buffered,
        .on_response = Effects.responseMsg(.settings_loaded),
    });
}

fn fetchActiveChatHistory(model: *Model, fx: *Effects) void {
    const chat = model.activeChat();
    chat.history_loaded = true;
    chat.history_loading = true;
    const init_fetch_key = chat.id + 1000;
    chat.fetch_key = init_fetch_key;
    const url = std.fmt.allocPrint(
        model.allocator,
        "http://127.0.0.1:8080/conversation/turns?user_id=native_sdk_chat&session_id={s}&limit=50",
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
        .sync = syncModelFromLayout,
        .view = buildView,
        // Geist (SIL OFL 1.1, Vercel) — the house face. Registered before
        // the first view build so layout measures with it. Ids 64+ are
        // the app range (see canvas.min_registered_font_id); the theme
        // tokens reference them (typography.font_id / mono_font_id /
        // button_font_id).
        .fonts = &.{
            .{ .id = 64, .name = "Geist-Regular.ttf", .ttf = @embedFile("fonts/Geist-Regular.ttf") },
            .{ .id = 65, .name = "Geist-Medium.ttf", .ttf = @embedFile("fonts/Geist-Medium.ttf") },
            .{ .id = 66, .name = "Geist-SemiBold.ttf", .ttf = @embedFile("fonts/Geist-SemiBold.ttf") },
            .{ .id = 67, .name = "GeistMono-Regular.ttf", .ttf = @embedFile("fonts/GeistMono-Regular.ttf") },
        },
    });
    app_state.model = initialModel();
    app_state.model.allocator = allocator;

    // Stress-test mode: seed a synthetic transcript of N messages so the
    // virtual list can be exercised at scale without a backend.
    if (init.environ_map.get("NATIVE_SDK_STRESS_MESSAGES")) |count_str| {
        if (std.fmt.parseInt(usize, count_str, 10)) |count| {
            app_state.model.stress_mode = true;
            seedStressTranscript(app_state.model.activeChat(), allocator, count);
        } else |_| {}
    }

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
