const std = @import("std");
const native_sdk = @import("native_sdk");
const main = @import("main.zig");

const canvas = native_sdk.canvas;
const testing = std.testing;

const AppUi = main.AppUi;
const Model = main.Model;
const Msg = main.Msg;
const Effects = main.Effects;

fn buildTree(arena: std.mem.Allocator, model: *const Model) !AppUi.Tree {
    var ui = AppUi.init(arena);
    const node = main.buildView(&ui, model);
    return ui.finalize(node);
}

fn findByText(widget: canvas.Widget, kind: canvas.WidgetKind, text: []const u8) ?canvas.Widget {
    if (widget.kind == kind and std.mem.eql(u8, widget.text, text)) return widget;
    for (widget.children) |child| {
        if (findByText(child, kind, text)) |found| return found;
    }
    return null;
}

fn expectByText(widget: canvas.Widget, kind: canvas.WidgetKind, text: []const u8) !canvas.Widget {
    return findByText(widget, kind, text) orelse {
        std.debug.print("no {t} with text \"{s}\" in the view\n", .{ kind, text });
        return error.WidgetNotFound;
    };
}

fn findByLabel(widget: canvas.Widget, kind: canvas.WidgetKind, label: []const u8) ?canvas.Widget {
    if (widget.kind == kind and std.mem.eql(u8, widget.semantics.label, label)) return widget;
    for (widget.children) |child| {
        if (findByLabel(child, kind, label)) |found| return found;
    }
    return null;
}

fn countKind(widget: canvas.Widget, kind: canvas.WidgetKind) usize {
    var count: usize = if (widget.kind == kind) 1 else 0;
    for (widget.children) |child| count += countKind(child, kind);
    return count;
}

fn countAllWidgets(widget: canvas.Widget) usize {
    var count: usize = 1;
    for (widget.children) |child| count += countAllWidgets(child);
    return count;
}

fn findTextContaining(widget: canvas.Widget, fragment: []const u8) ?canvas.Widget {
    if (widget.kind == .text and std.mem.indexOf(u8, widget.text, fragment) != null) return widget;
    for (widget.children) |child| {
        if (findTextContaining(child, fragment)) |found| return found;
    }
    return null;
}

fn findButtonContaining(widget: canvas.Widget, fragment: []const u8) ?canvas.Widget {
    if (widget.kind == .button and std.mem.indexOf(u8, widget.text, fragment) != null) return widget;
    for (widget.children) |child| {
        if (findButtonContaining(child, fragment)) |found| return found;
    }
    return null;
}

fn findNthVirtualList(widget: canvas.Widget, target: usize, seen: *usize) ?canvas.Widget {
    if (widget.kind == .scroll_view and widget.layout.virtualized) {
        if (seen.* == target) return widget;
        seen.* += 1;
    }
    for (widget.children) |child| {
        if (findNthVirtualList(child, target, seen)) |found| return found;
    }
    return null;
}

fn findChatTranscriptList(widget: canvas.Widget) ?canvas.Widget {
    var seen: usize = 0;
    return findNthVirtualList(widget, 0, &seen);
}

fn expectByLabel(widget: canvas.Widget, kind: canvas.WidgetKind, label: []const u8) !canvas.Widget {
    return findByLabel(widget, kind, label) orelse {
        std.debug.print("no {t} with label \"{s}\" in the view\n", .{ kind, label });
        return error.WidgetNotFound;
    };
}

fn noopFx(allocator: std.mem.Allocator) Effects {
    var fx = Effects.init(allocator);
    fx.executor = .fake;
    return fx;
}

fn sendAndStartStream(model: *Model, fx: *Effects, text: []const u8) u64 {
    main.update(model, .new_chat, fx);
    main.update(model, .{ .input_changed = .{ .insert_text = text } }, fx);
    main.update(model, .send_message, fx);
    return model.activeChat().fetch_key;
}

test "enter sends the message and clears the draft" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello world" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "\n" } }, &fx);
    // Enter-to-send: a bare newline insert sends the message and clears the draft.
    try testing.expectEqualStrings("", model.activeChat().draft_text);
    try testing.expect(model.activeChat().streaming);
}

test "send message adds user message and starts streaming" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    main.update(&model, .send_message, &fx);
    const chat = model.activeChat();
    // User message + empty assistant typing indicator
    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    try testing.expectEqualStrings("user", chat._messages[0].role);
    try testing.expectEqualStrings("Hello", chat._messages[0].content);
    try testing.expectEqualStrings("assistant", chat._messages[1].role);
    try testing.expectEqualStrings("", chat._messages[1].content);
    try testing.expect(chat.streaming);
    try testing.expectEqualStrings("Thinking...", chat.status_text);
    try testing.expectEqualStrings("", model.inputText());
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/message/stream", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, "ollama-cloud:deepseek-v4-flash:0731") != null);
}

test "default model falls back to Ollama Cloud DeepSeek V4 Flash 0731" {
    var model = main.initialModel();

    try testing.expectEqualStrings("ollama-cloud:deepseek-v4-flash:0731", model.selectedModel());
}

test "models response labels selected model without credential source" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const models_body =
        \\{"models":[{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted","billing_mode":"hosted"}]}
    ;
    main.update(&model, .{ .models_loaded = .{
        .key = 9,
        .outcome = .ok,
        .body = models_body,
    } }, &fx);

    try testing.expectEqual(@as(usize, 1), model.available_model_count);
    try testing.expectEqualStrings("agnes:agnes-2.0-flash", model.selectedModel());
    try testing.expectEqualStrings("Agnes · Agnes 2.0 Flash", model.selectedModelLabel(arena));
}

test "hosted model shows change button" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const models_body =
        \\{"models":[{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted","billing_mode":"hosted"}]}
    ;
    main.update(&model, .{ .models_loaded = .{
        .key = 9,
        .outcome = .ok,
        .body = models_body,
    } }, &fx);

    const tree = try buildTree(arena, &model);
    const btn = findButtonContaining(tree.root, "Agnes 2.0 Flash") orelse return error.WidgetNotFound;
    const send_btn = findButtonContaining(tree.root, "Send") orelse return error.WidgetNotFound;
    // Model button and Send button are in the same row (same y)
    try testing.expectApproxEqAbs(btn.frame.y, send_btn.frame.y, 1.0);
    // Model button fits its content (not full width)
    try testing.expect(btn.frame.width < send_btn.frame.width or btn.frame.width < 200);
}

test "message column aligns closer to divider than textarea content" {
    try testing.expectEqual(@as(u32, 0), main.message_list_outer_padding);
}

test "input accumulates incremental text events" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "h" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "e" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "llo" } }, &fx);
    try testing.expectEqualStrings("hello", model.inputText());

    main.update(&model, .{ .input_changed = .delete_backward }, &fx);
    try testing.expectEqualStrings("hell", model.inputText());

    main.update(&model, .{ .input_changed = .clear }, &fx);
    try testing.expectEqualStrings("", model.inputText());
}

test "selected composer text deletes and replaces as a range" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .set_selection = .{ .anchor = 0, .focus = 5 } } }, &fx);
    main.update(&model, .{ .input_changed = .delete_backward }, &fx);
    try testing.expectEqualStrings("", model.inputText());

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .set_selection = .{ .anchor = 0, .focus = 5 } } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "x" } }, &fx);
    try testing.expectEqualStrings("x", model.inputText());
}

test "selected settings search text deletes and replaces as a range" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .settings_search = .{ .insert_text = "agnes" } }, &fx);
    main.update(&model, .{ .settings_search = .{ .set_selection = .{ .anchor = 0, .focus = 5 } } }, &fx);
    main.update(&model, .{ .settings_search = .delete_backward }, &fx);
    try testing.expectEqualStrings("", model.settings.search_text);

    main.update(&model, .{ .settings_search = .{ .insert_text = "agnes" } }, &fx);
    main.update(&model, .{ .settings_search = .{ .set_selection = .{ .anchor = 0, .focus = 5 } } }, &fx);
    main.update(&model, .{ .settings_search = .{ .insert_text = "x" } }, &fx);
    try testing.expectEqualStrings("x", model.settings.search_text);
}

test "settings search skips duplicate app edit after runtime text sync" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.settings.search_text = "a";
    model.settings.search_selection = .{ .anchor = 1, .focus = 1 };
    model.settings.search_runtime_text_synced = true;

    main.update(&model, .{ .settings_search = .{ .insert_text = "a" } }, &fx);

    try testing.expectEqualStrings("a", model.settings.search_text);
    try testing.expect(!model.settings.search_runtime_text_synced);
}

test "stale composer selection is clamped before inserting" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .set_selection = .{ .anchor = 20, .focus = 20 } } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "h" } }, &fx);

    try testing.expectEqualStrings("h", model.inputText());
    try testing.expectEqualDeep(canvas.TextSelection{ .anchor = 1, .focus = 1 }, model.activeChat().draft_selection);
}

test "composer renders selected draft range" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .set_selection = .{ .anchor = 0, .focus = 5 } } }, &fx);

    const tree = try buildTree(arena, &model);
    const composer = try expectByLabel(tree.root, canvas.WidgetKind.textarea, "Message");
    try testing.expect(composer.text_selection != null);
    try testing.expectEqualDeep(canvas.TextSelection{ .anchor = 0, .focus = 5 }, composer.text_selection.?);
}

test "messages event creates assistant bubble" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "Hi");
    const chat = model.activeChat();

    // First text_delta event creates assistant bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Hello\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + assistant
    try testing.expectEqualStrings("assistant", chat._messages[1].role);
    try testing.expectEqualStrings("Hello", chat._messages[1].content);

    // Second text_delta event appends
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\" world\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello world", chat._messages[1].content);
}

test "reasoning_delta event creates reasoning bubble" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\"I need to think...\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + reasoning (empty assistant removed)
    try testing.expectEqualStrings("reasoning", chat._messages[1].role);
    try testing.expectEqualStrings("I need to think...", chat._messages[1].content);

    // Second reasoning_delta appends
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\" about this.\"}}" } }, &fx);
    try testing.expectEqualStrings("I need to think... about this.", chat._messages[1].content);
}

test "tool_input_start creates tool bubble with running status" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_start\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + tool (empty assistant removed)
    try testing.expectEqualStrings("tool", chat._messages[1].role);
    try testing.expectEqualStrings("time_get", chat._messages[1].tool_name);
    try testing.expectEqualStrings("running", chat._messages[1].tool_status);
}

test "tool_result updates tool bubble in place" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    // tool_input_start
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_start\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);

    // tool_result updates in place — no new bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_result\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\",\"content\":\"Current time: 12:00 UTC\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // still 2, no new bubble
    try testing.expectEqualStrings("done", chat._messages[1].tool_status);
    try testing.expectEqualStrings("Current time: 12:00 UTC", chat._messages[1].tool_result);
}

test "tool_input_end fills tool bubble arguments" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    // Main stream path: tool_input_start carries no args (RunEvent ToolStartData),
    // the arguments arrive on tool_input_end.
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_start\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_end\",\"data\":{\"tool_call_id\":\"call_1\",\"arguments\":{\"tz\":\"UTC\"}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // no new bubble
    try testing.expect(std.mem.indexOf(u8, chat._messages[1].content, "UTC") != null);
    try testing.expect(std.mem.indexOf(u8, chat._messages[1].content, "time_get") != null);
}

test "assistant response after tool trims leading stream whitespace" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "find news");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_start\",\"data\":{\"name\":\"web_search\",\"tool_call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_result\",\"data\":{\"name\":\"web_search\",\"tool_call_id\":\"call_1\",\"content\":\"results\"}}" } }, &fx);
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"\\n\\nHere are the latest news\"}}" } }, &fx);

    try testing.expectEqual(@as(usize, 3), chat.msg_count);
    try testing.expectEqualStrings("assistant", chat._messages[2].role);
    try testing.expectEqualStrings("Here are the latest news", chat._messages[2].content);
}

test "multiple reasoning segments create separate bubbles" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    // First reasoning
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\"thinking 1\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);

    // Tool call (closes reasoning bubble)
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_input_start\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 3), chat.msg_count);

    // tool_result
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_result\",\"data\":{\"name\":\"time_get\",\"tool_call_id\":\"call_1\",\"content\":\"12:00\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 3), chat.msg_count);

    // Second reasoning — new bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\"thinking 2\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 4), chat.msg_count);
    try testing.expectEqualStrings("reasoning", chat._messages[3].role);
    try testing.expectEqualStrings("thinking 2", chat._messages[3].content);
}

test "done finalizes and collapses reasoning bubbles" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    // reasoning
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\"thinking...\"}}" } }, &fx);
    // assistant
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Answer\"}}" } }, &fx);

    try testing.expect(!chat._messages[1].collapsed); // reasoning not collapsed during stream

    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);
    try testing.expect(!chat.streaming);
    try testing.expectEqualStrings("", chat.open_bubble_type);
    try testing.expectEqualStrings("", chat.status_text);
    try testing.expect(chat._messages[1].collapsed); // reasoning collapsed after done
}

test "stream done queues history reconciliation" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "time in shanghai");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"The time is 1 AM\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    try testing.expectEqual(@as(usize, 3), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(1).?;
    try testing.expect(std.mem.indexOf(u8, request.url, "/conversation/turns?") != null);
    try testing.expect(std.mem.indexOf(u8, request.url, chat.sessionId()) != null);
}

test "sending message resets transcript scroll identity" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const chat = model.activeChat();
    main.addMessage(chat, arena, "user", "old prompt");
    main.addMessage(chat, arena, "assistant", "old response");

    const before_tree = try buildTree(arena, &model);
    const before_list = findChatTranscriptList(before_tree.root) orelse return error.WidgetNotFound;

    main.update(&model, .{ .input_changed = .{ .insert_text = "new prompt" } }, &fx);
    main.update(&model, .send_message, &fx);

    const after_tree = try buildTree(arena, &model);
    const after_list = findChatTranscriptList(after_tree.root) orelse return error.WidgetNotFound;
    try testing.expect(before_list.id != after_list.id);
}

test "history reconciliation replaces stale streamed content" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "time in shanghai");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"The current time is 1 AM\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    const history_body =
        \\{"turns":[{"run_id":"run-1","messages":[{"role":"user","content":"time in shanghai","timestamp":"2026-07-19T16:49:00Z","metadata":{}},{"role":"assistant","content":"The current time is 1 AM).","timestamp":"2026-07-19T16:50:00Z","metadata":{}}],"metadata":{"model":"test:model"}}]}
    ;
    main.update(&model, .{ .chat_history_loaded = .{
        .key = chat.fetch_key,
        .outcome = .ok,
        .body = history_body,
    } }, &fx);

    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    try testing.expectEqualStrings("The current time is 1 AM).", chat._messages[1].content);
}

test "title generation fires after first exchange" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "What is the weather in Shanghai?");
    const chat = model.activeChat();

    // assistant response
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"It is hot and humid.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    // Title generation should have fired (stream + history reconcile + title = 3)
    try testing.expectEqual(@as(usize, 3), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(2).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/conversation/title", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, chat.sessionId()) != null);
}

test "title generation does not fire for short first message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "hi");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Hello!\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    // Title generation should NOT fire (user message < 5 chars).
    // Only stream + history reconciliation should be pending.
    try testing.expectEqual(@as(usize, 2), fx.pendingFetchCount());
    _ = chat;
}

test "title generation does not fire on second exchange" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // First exchange
    const fk1 = sendAndStartStream(&model, &fx, "What is the weather?");
    main.update(&model, .{ .stream_line = .{ .key = fk1, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Hot.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk1 } }, &fx);
    const fetch_count_after_first = fx.pendingFetchCount();

    // Second exchange
    model.activeChat().draft_text = "Thanks";
    model.activeChat().streaming = false;
    const fk2 = sendAndStartStream(&model, &fx, "Thanks");
    main.update(&model, .{ .stream_line = .{ .key = fk2, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"You're welcome.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk2 } }, &fx);

    // Title generation should NOT fire on second exchange (2 user messages now).
    // The second exchange queues stream + history reconciliation, but no title.
    try testing.expectEqual(fetch_count_after_first + 2, fx.pendingFetchCount());
}

test "title_generated updates chat title" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "What is the weather in Shanghai?");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Hot and humid.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);
    // Note: title fetch is queued but we don't need to clear it

    // Simulate title generation response for the active chat's session
    const title_response_body = std.fmt.allocPrint(
        arena,
        "{{\"title\":\"Shanghai Weather Forecast\",\"session_id\":\"{s}\"}}",
        .{chat.sessionId()},
    ) catch return;
    main.update(&model, .{ .title_generated = .{
        .key = 8,
        .outcome = .ok,
        .body = title_response_body,
    } }, &fx);

    try testing.expectEqualStrings("Shanghai Weather Forecast", chat.title);
}

test "title_generated for deleted chat is no-op" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // Create a second chat so we can delete the first
    main.update(&model, .new_chat, &fx);
    model.active_chat_idx = 0;
    const original_title = model.chats[0].title;

    // Simulate title response for a session that doesn't match any chat
    const title_response_body =
        \\{"title":"Nonexistent","session_id":"fake-session-999"}
    ;
    main.update(&model, .{ .title_generated = .{
        .key = 8,
        .outcome = .ok,
        .body = title_response_body,
    } }, &fx);

    // No chat should have been updated
    try testing.expectEqualStrings(original_title, model.chats[0].title);
}

test "toggle_bubble flips collapsed state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning_delta\",\"data\":{\"delta\":\"thinking...\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);
    try testing.expect(chat._messages[1].collapsed);

    // Toggle: expand
    main.update(&model, .{ .toggle_bubble = chat._messages[1].id }, &fx);
    try testing.expect(!chat._messages[1].collapsed);

    // Toggle: collapse again
    main.update(&model, .{ .toggle_bubble = chat._messages[1].id }, &fx);
    try testing.expect(chat._messages[1].collapsed);
}

test "interrupt sets pending state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(chat.has_pending);
    try testing.expectEqualStrings("email_send", chat.pending_tool);
    try testing.expectEqualStrings("abc123", chat.pending_call_id);
}

test "approve clears pending" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(chat.has_pending);

    main.update(&model, .approve, &fx);
    try testing.expect(!chat.has_pending);
    try testing.expect(chat.streaming);
    try testing.expectEqual(@as(usize, 2), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(1).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/message/approve", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, "abc123") != null);
}

test "approve streams the resumed run" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(chat.has_pending);

    main.update(&model, .approve, &fx);
    // The approve endpoint returns an SSE stream; the fetch must be in stream
    // mode so the resumed run's events render via stream_line.
    const request = fx.pendingFetchAt(1).?;
    try testing.expect(request.response == .stream);

    // The resumed run's text must render through stream_line.
    const fk2 = chat.fetch_key;
    main.update(&model, .{ .stream_line = .{ .key = fk2, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Resumed answer\"}}" } }, &fx);
    try testing.expectEqualStrings("Resumed answer", chat._messages[chat.msg_count - 1].content);
}

test "approve resumes the run through the real effect pipeline" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    // 1. The backend streams an interrupt (HITL) — fed through the fake
    //    executor exactly as the runtime delivers SSE lines.
    try fx.feedLine(fk, "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}");
    while (fx.takeMsg()) |msg| main.update(&model, msg, &fx);
    try testing.expect(chat.has_pending);
    try testing.expectEqualStrings("email_send", chat.pending_tool);

    // 2. The first stream ends (the backend closed it after the interrupt).
    try fx.feedResponse(fk, 200, "");
    while (fx.takeMsg()) |msg| main.update(&model, msg, &fx);
    try testing.expect(!chat.streaming);

    // 3. User approves — the approve fetch must be a stream: feedLine below
    //    would fail with EffectNotFound on a buffered fetch. (Fetch 0 is the
    //    history reconciliation queued by stream_done.)
    main.update(&model, .approve, &fx);
    try testing.expectEqual(@as(usize, 2), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(1).?;
    try testing.expect(request.response == .stream);
    const fk2 = chat.fetch_key;

    // 4. The resumed run streams text — rendered through the pipeline.
    try fx.feedLine(fk2, "data: {\"type\":\"text_delta\",\"data\":{\"delta\":\"Resumed answer\"}}");
    while (fx.takeMsg()) |msg| main.update(&model, msg, &fx);
    try testing.expectEqualStrings("Resumed answer", chat._messages[chat.msg_count - 1].content);

    // 5. The resumed stream ends — approve_done finalizes the stream.
    try fx.feedResponse(fk2, 200, "");
    while (fx.takeMsg()) |msg| main.update(&model, msg, &fx);
    try testing.expect(!chat.streaming);
}

test "reject clears pending" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(chat.has_pending);

    main.update(&model, .reject, &fx);
    try testing.expect(!chat.has_pending);
    try testing.expectEqualStrings("", chat.pending_tool);
    try testing.expectEqual(@as(usize, 2), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(1).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/message/reject", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, "abc123") != null);
}

test "empty state renders placeholder" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "How can I help?");
}

test "live-activity pill appears only while streaming" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // Idle: no pill.
    var tree = try buildTree(arena, &model);
    try testing.expect(findByText(tree.root, .text, "Working…") == null);

    // Streaming: pill floats above the composer with the status text.
    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    main.update(&model, .send_message, &fx);
    try testing.expect(model.activeChat().streaming);
    tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "Working…");
    // The pill resets its entrance on each send.
    try testing.expectEqual(@as(f32, 0), model.pill_entrance);

    // Stream completes: pill unmounts.
    const fk = model.activeChat().fetch_key;
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);
    tree = try buildTree(arena, &model);
    try testing.expect(findByText(tree.root, .text, "Working…") == null);
}

test "quick action sends a preset prompt" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .quick_action_browse, &fx);
    const chat = model.activeChat();
    try testing.expect(chat.streaming);
    try testing.expectEqualStrings("", model.inputText());
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
    try testing.expect(std.mem.indexOf(u8, request.body, "Browse the web and find the latest news on AI agents") != null);
}

test "typing dots render while streaming" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    main.update(&model, .send_message, &fx);
    try testing.expect(model.activeChat().streaming);

    const tree = try buildTree(arena, &model);
    // The only .panel widgets while streaming: the three typing dots, the
    // pill's pulse dot, and the glass composer bar itself (panel kind).
    try testing.expectEqual(@as(usize, 5), countKind(tree.root, .panel));
}

test "empty state renders suggestions and quick-action chips" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    const tree = try buildTree(arena, &model);
    _ = findButtonContaining(tree.root, "Triage my inbox") orelse return error.WidgetNotFound;
    // Icon-only chips: buttons with no label text. The empty state has three
    // (browse / files / research) plus the rail collapse chevron and the
    // composer's Send + model buttons — assert at least three empty-label
    // buttons exist.
    var empty_label_buttons: usize = 0;
    var stack: [64]canvas.Widget = undefined;
    var count: usize = 1;
    stack[0] = tree.root;
    while (count > 0) {
        count -= 1;
        const w = stack[count];
        if (w.kind == .button and w.text.len == 0) empty_label_buttons += 1;
        for (w.children) |child| {
            if (count + 1 >= stack.len) break;
            stack[count] = child;
            count += 1;
        }
    }
    try testing.expect(empty_label_buttons >= 3);
}

test "theme: dark tokens have teal accent" {
    const theme = @import("theme.zig");
    const tokens = theme.darkTokens();
    try testing.expectApproxEqAbs(@as(f32, 20) / 255.0, tokens.colors.accent.r, 1e-5);
    try testing.expectApproxEqAbs(@as(f32, 184) / 255.0, tokens.colors.accent.g, 1e-5);
    try testing.expectApproxEqAbs(@as(f32, 166) / 255.0, tokens.colors.accent.b, 1e-5);
}

test "theme: light tokens have darker teal accent" {
    const theme = @import("theme.zig");
    const tokens = theme.lightTokens();
    try testing.expectApproxEqAbs(@as(f32, 13) / 255.0, tokens.colors.accent.r, 1e-5);
    try testing.expectApproxEqAbs(@as(f32, 148) / 255.0, tokens.colors.accent.g, 1e-5);
    try testing.expectApproxEqAbs(@as(f32, 136) / 255.0, tokens.colors.accent.b, 1e-5);
}

test "theme: radius tokens are comfortable values" {
    const theme = @import("theme.zig");
    const tokens = theme.darkTokens();
    // Squircle scale from the openassistants.org tokens (10/14/18/24).
    try testing.expectEqual(@as(f32, 10.0), tokens.radius.sm);
    try testing.expectEqual(@as(f32, 14.0), tokens.radius.md);
    try testing.expectEqual(@as(f32, 18.0), tokens.radius.lg);
    try testing.expectEqual(@as(f32, 24.0), tokens.radius.xl);
}

test "theme: toggle switches dark to light" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    try testing.expectEqual(main.ThemeMode.dark, model.theme_mode);
    main.update(&model, .toggle_theme, &fx);
    try testing.expectEqual(main.ThemeMode.light, model.theme_mode);
    main.update(&model, .toggle_theme, &fx);
    try testing.expectEqual(main.ThemeMode.dark, model.theme_mode);
}

test "chat list: new chat creates empty chat and sets active" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    try testing.expectEqual(@as(usize, 1), model.chat_count);
    const first_id = model.chats[0].id;
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 2), model.chat_count);
    // The new chat becomes active and is empty.
    try testing.expect(model.activeChat().id != first_id);
    try testing.expectEqual(@as(usize, 0), model.activeChat().msg_count);
}

test "chat list: switch chat sets active index" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const first_chat_id = model.chats[0].id;
    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    model.activeChat().draft_text = "second";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    // The newest chat is active (wherever it sorted to).
    try testing.expect(model.activeChat().id != first_chat_id);
    main.update(&model, .{ .switch_chat = first_chat_id }, &fx);
    try testing.expectEqual(first_chat_id, model.activeChat().id);
}

test "search: query accumulates text" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    main.update(&model, .{ .search_input = .{ .insert_text = "tri" } }, &fx);
    try testing.expectEqualStrings("tri", model.search_query);
}

test "unread badge: increments for non-active chat on stream_done" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    model.activeChat().draft_text = "second";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);

    // Switch to chat 0 (active), then let chat 1 finish streaming
    model.active_chat_idx = 0;
    model.chats[0].title = "First chat";
    model.chats[1].title = "Second chat";

    model.chats[1].streaming = true;
    model.chats[1].fetch_key = 42;
    main.update(&model, .{ .stream_done = .{ .key = 42 } }, &fx);
    // Chat 1 finished streaming while NOT active → should be unread
    try testing.expectEqual(@as(u32, 0), model.chats[0].unread_count);
    try testing.expectEqual(@as(u32, 1), model.chats[1].unread_count);
}

test "unread badge: switch chat resets unread count" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    model.chats[0].unread_count = 3;
    model.active_chat_idx = 1;

    main.update(&model, .{ .switch_chat = model.chats[0].id }, &fx);
    try testing.expectEqual(@as(u32, 0), model.chats[0].unread_count);
    try testing.expectEqual(@as(usize, 0), model.active_chat_idx);
}

test "new chat keeps draft on the previous chat" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);

    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 2), model.chat_count);
    // The new chat is empty; the draft stays on the previous chat.
    try testing.expectEqualStrings("", model.inputText());
    main.update(&model, .{ .switch_chat = model.chats[1].id }, &fx);
    try testing.expectEqualStrings("hello", model.inputText());
}

test "draft preservation: switching chats preserves per-chat draft" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "first" } }, &fx);
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    const first_chat_id = model.chats[0].id;

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "draft2" } }, &fx);
    try testing.expectEqualStrings("draft2", model.inputText());

    main.update(&model, .{ .switch_chat = first_chat_id }, &fx);
    try testing.expectEqualStrings("", model.inputText());

    main.update(&model, .{ .switch_chat = model.chats[0].id }, &fx);
    try testing.expectEqualStrings("draft2", model.inputText());
}

test "settings catalog response parses grouped providers and models" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"agnes","name":"Agnes","key_source":"hosted","has_key":true,"models":[{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted"}]},{"id":"anthropic","name":"Anthropic","key_source":"none","has_key":false,"models":[{"id":"anthropic:claude-sonnet-4-5","name":"Claude Sonnet 4.5","provider":"anthropic","provider_display":"Anthropic","key_source":"none"}]}]}
    ;

    main.update(&model, .{ .settings_loaded = .{
        .key = 10,
        .outcome = .ok,
        .body = catalog_body,
    } }, &fx);

    try testing.expectEqual(@as(usize, 2), model.settings.provider_count);
    try testing.expectEqual(@as(usize, 2), model.available_model_count);
    try testing.expectEqualStrings("agnes", model.settings.providers[0].id);
    try testing.expectEqualStrings("hosted", model.settings.providers[0].key_source);
    try testing.expect(model.settings.providers[0].has_key);
    try testing.expectEqual(@as(usize, 1), model.settings.providers[0].model_count);
    try testing.expectEqualStrings("anthropic", model.settings.providers[1].id);
    try testing.expectEqualStrings("none", model.settings.providers[1].key_source);
    try testing.expect(!model.settings.providers[1].has_key);
    try testing.expectEqual(@as(usize, 1), model.settings.providers[1].model_count);
}

test "settings open fetches dedicated model catalog endpoint" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .open_settings, &fx);

    const request = fx.pendingFetchAt(0).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/settings/model-catalog?user_id=native_sdk_chat&max_models_per_provider=20&max_providers=64", request.url);
}

test "locked settings model opens API key modal instead of saving" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"anthropic","name":"Anthropic","key_source":"none","has_key":false,"models":[{"id":"anthropic:claude-sonnet-4-5","name":"Claude Sonnet 4.5","provider":"anthropic","provider_display":"Anthropic","key_source":"none"}]}]}
    ;
    main.update(&model, .{ .settings_loaded = .{ .key = 10, .outcome = .ok, .body = catalog_body } }, &fx);

    main.update(&model, .{ .select_model = 0 }, &fx);

    try testing.expect(model.settings.key_modal_visible);
    try testing.expectEqualStrings("anthropic", model.settings.pending_provider_id);
    try testing.expectEqualStrings("anthropic:claude-sonnet-4-5", model.settings.pending_model_id);
    try testing.expectEqual(@as(usize, 0), fx.pendingFetchCount());
}

test "settings request bodies escape JSON strings" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.available_models[0] = .{
        .id = "openai:gpt-quote\\\"slash",
        .name = "Quoted",
        .provider = "openai",
        .provider_display = "OpenAI",
        .key_source = "user",
    };
    model.available_model_count = 1;
    main.update(&model, .{ .select_model = 0 }, &fx);

    const select_request = fx.pendingFetchAt(0).?;
    try testing.expect(std.mem.indexOf(u8, select_request.body, "openai:gpt-quote\\\\\\\"slash") != null);
    const parsed_select = try std.json.parseFromSlice(std.json.Value, arena, select_request.body, .{});
    defer parsed_select.deinit();
    try testing.expectEqualStrings("openai:gpt-quote\\\"slash", parsed_select.value.object.get("default_model").?.string);
}

test "settings general response loads rubric state from canonical shape" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // Canonical GET /settings response: verification lives under saved/effective
    // and the field is max_attempts (not max_iterations).
    const body =
        \\{"saved":{"default_model":null,"verification":{"enabled":true,"grader_model":null,"max_attempts":2}},"effective":{"default_model":"agnes:agnes-2.0-flash","verification":{"state":"on","grader_model":"ollama-cloud:deepseek-v4-flash","max_attempts":2,"grader_prompt_hash":"sha256:abc"}},"provider_status":{}}
    ;
    main.update(&model, .{ .settings_general_loaded = .{ .key = 0, .outcome = .ok, .body = body } }, &fx);
    try testing.expect(model.settings.rubric_enabled);
    try testing.expectEqual(@as(u32, 2), model.settings.rubric_max_iterations);
}

test "save general settings PATCHes verification with max_attempts" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.settings.rubric_enabled = true;
    model.settings.rubric_max_iterations = 2;
    main.update(&model, .save_general_settings, &fx);

    // Settings save must PATCH (backend has no PUT) with max_attempts.
    const settings_request = fx.pendingFetchAt(0).?;
    try testing.expect(settings_request.method == .PATCH);
    try testing.expect(std.mem.indexOf(u8, settings_request.body, "max_attempts") != null);
    try testing.expect(std.mem.indexOf(u8, settings_request.body, "max_iterations") == null);
    try testing.expect(std.mem.indexOf(u8, settings_request.body, "\"enabled\":true") != null);

    // Grader prompt save must hit /user/grader-prompt and send expected_revision.
    const prompt_request = fx.pendingFetchAt(1).?;
    try testing.expect(std.mem.indexOf(u8, prompt_request.url, "/user/grader-prompt") != null);
    try testing.expect(prompt_request.method == .PUT);
    try testing.expect(std.mem.indexOf(u8, prompt_request.body, "expected_revision") != null);
}

test "grader prompt fetches use /user/grader-prompt and track revision" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .settings_general, &fx);
    const prompt_request = fx.pendingFetchAt(0).?;
    try testing.expect(std.mem.indexOf(u8, prompt_request.url, "/user/grader-prompt") != null);
}

test "grader prompt save sends the tracked revision" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // The GET response carries a revision; the save must send it back.
    const body =
        \\{"content":"# Rubric","source":"customized","content_hash":"sha256:abc","revision":2}
    ;
    main.update(&model, .{ .grader_prompt_loaded = .{ .key = 0, .outcome = .ok, .body = body } }, &fx);
    try testing.expectEqualStrings("# Rubric", model.settings.grader_prompt);

    main.update(&model, .save_general_settings, &fx);
    // 0 = settings PATCH, 1 = grader-prompt PUT.
    const save_request = fx.pendingFetchAt(1).?;
    try testing.expect(std.mem.indexOf(u8, save_request.body, "\"expected_revision\":2") != null);
}

test "settings catalog renders locked model rows instead of add key cards" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"anthropic","name":"Anthropic","key_source":"none","has_key":false,"models":[{"id":"anthropic:claude-sonnet-4-5","name":"Claude Sonnet 4.5","provider":"anthropic","provider_display":"Anthropic","key_source":"none"}]}]}
    ;
    main.update(&model, .{ .settings_loaded = .{ .key = 10, .outcome = .ok, .body = catalog_body } }, &fx);

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .button, "Models");
    _ = try expectByText(tree.root, .button, "General");
    try testing.expect(findButtonContaining(tree.root, "Providers & Models") == null);
    _ = try expectByText(tree.root, .text, "ANTHROPIC");
    try testing.expect(findTextContaining(tree.root, "PROVIDER") == null);
    _ = try expectByText(tree.root, .text, "  Claude Sonnet 4.5");
    _ = try expectByText(tree.root, .text, "Add key");
    try testing.expect(findButtonContaining(tree.root, "Add Key") == null);
}

test "settings provider headers have no status and model rows own state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"agnes","name":"Agnes","key_source":"hosted","has_key":true,"models":[{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted"}]},{"id":"deepseek","name":"DeepSeek","key_source":"none","has_key":false,"models":[{"id":"deepseek:deepseek-chat","name":"DeepSeek Chat","provider":"deepseek","provider_display":"DeepSeek","key_source":"none"}]}]}
    ;
    main.update(&model, .{ .settings_loaded = .{ .key = 10, .outcome = .ok, .body = catalog_body } }, &fx);

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "  Agnes 2.0 Flash  ✓");
    _ = try expectByText(tree.root, .text, "  DeepSeek Chat");
    _ = try expectByText(tree.root, .text, "Add key");
    try testing.expect(findTextContaining(tree.root, "Ready") == null);
    try testing.expect(findTextContaining(tree.root, "Env") == null);
    try testing.expect(findTextContaining(tree.root, "Key required") == null);
}

test "settings general section owns appearance and about" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;

    var tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .button, "Models");
    _ = try expectByText(tree.root, .button, "General");
    try testing.expect(findButtonContaining(tree.root, "Providers & Models") == null);
    try testing.expect(findTextContaining(tree.root, "Appearance") == null);
    try testing.expect(findTextContaining(tree.root, "About") == null);

    var fx = noopFx(arena);
    main.update(&model, .settings_general, &fx);
    tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "Appearance");
    _ = try expectByText(tree.root, .text, "About");
    try testing.expect(findTextContaining(tree.root, "Search providers and models") == null);
}

test "settings sidebar uses compact item sizing" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;

    const tree = try buildTree(arena, &model);
    const models = try expectByText(tree.root, .button, "Models");
    const general = try expectByText(tree.root, .button, "General");

    try testing.expect(models.layout.max_size.width <= 112);
    try testing.expectEqual(@as(f32, 12), models.layout.padding.top);
    try testing.expectEqual(@as(f32, 12), models.layout.padding.bottom);
    try testing.expectEqual(models.frame.height, general.frame.height);
}

test "settings catalog overflow uses neutral search guidance" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;

    model.settings.providers[0] = .{
        .id = "openrouter",
        .name = "OpenRouter",
        .has_key = true,
        .key_source = "user",
        .model_count = 130,
    };
    model.settings.provider_count = 1;
    model.settings.default_model_id = "openrouter:model-0";
    model.available_model_count = 130;
    for (0..130) |i| {
        const id = try std.fmt.allocPrint(arena, "openrouter:model-{d}", .{i});
        const name = try std.fmt.allocPrint(arena, "Model {d}", .{i});
        model.available_models[i] = .{
            .id = id,
            .name = name,
            .provider = "openrouter",
            .provider_display = "OpenRouter",
            .key_source = "user",
        };
        model.settings.providers[0].model_indices[i] = i;
    }

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "More models available. Search to narrow the catalog.");
    try testing.expect(findTextContaining(tree.root, "Catalog truncated") == null);
}

test "settings locked model click renders centered key modal copy" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"anthropic","name":"Anthropic","key_source":"none","has_key":false,"models":[{"id":"anthropic:claude-sonnet-4-5","name":"Claude Sonnet 4.5","provider":"anthropic","provider_display":"Anthropic","key_source":"none"}]}]}
    ;
    main.update(&model, .{ .settings_loaded = .{ .key = 10, .outcome = .ok, .body = catalog_body } }, &fx);
    main.update(&model, .{ .select_model = 0 }, &fx);

    const tree = try buildTree(arena, &model);
    _ = try expectByText(tree.root, .text, "Add Anthropic key");
    _ = try expectByText(tree.root, .text, "Required to use Claude Sonnet 4.5.");
    _ = try expectByText(tree.root, .button, "Test & Save");
}

test "composer model cycling skips locked catalog models" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const catalog_body =
        \\{"default_model":"agnes:agnes-2.0-flash","providers":[{"id":"agnes","name":"Agnes","key_source":"hosted","has_key":true,"models":[{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted"}]},{"id":"anthropic","name":"Anthropic","key_source":"none","has_key":false,"models":[{"id":"anthropic:claude-sonnet-4-5","name":"Claude Sonnet 4.5","provider":"anthropic","provider_display":"Anthropic","key_source":"none"}]}]}
    ;
    main.update(&model, .{ .settings_loaded = .{ .key = 10, .outcome = .ok, .body = catalog_body } }, &fx);

    main.update(&model, .cycle_model, &fx);

    try testing.expectEqualStrings("agnes:agnes-2.0-flash", model.selectedModel());
}

test "settings toggle closes visible settings panel" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    model.settings.visible = true;
    var fx = noopFx(arena);

    main.update(&model, .open_settings, &fx);

    try testing.expect(!model.settings.visible);
    try testing.expectEqual(@as(usize, 0), fx.pendingFetchCount());
}

test "settings modal test-key network failure shows inline error" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    model.settings.key_modal_visible = true;
    model.settings.key_testing = true;

    main.update(&model, .{ .key_tested = .{ .key = 11, .outcome = .connect_failed, .body = "" } }, &fx);

    try testing.expect(model.settings.key_modal_visible);
    try testing.expect(!model.settings.key_testing);
    try testing.expectEqualStrings("Failed to test key", model.settings.key_error);
}

test "delete chat before active keeps same active chat" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    model.activeChat().draft_text = "second";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    model.activeChat().draft_text = "third";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    try testing.expectEqual(@as(usize, 3), model.chat_count);

    const middle_id = model.chats[1].id;
    main.update(&model, .{ .switch_chat = middle_id }, &fx);
    try testing.expectEqual(middle_id, model.activeChat().id);

    const top_id = model.chats[0].id;
    main.update(&model, .{ .delete_chat = top_id }, &fx);

    try testing.expectEqual(@as(usize, 2), model.chat_count);
    // Deleting a chat before the active one keeps the active chat selected.
    try testing.expectEqual(middle_id, model.activeChat().id);
}

test "cancel clears pending HITL state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(chat.has_pending);

    main.update(&model, .cancel, &fx);

    try testing.expect(!chat.streaming);
    try testing.expect(!chat.has_pending);
    try testing.expectEqualStrings("", chat.pending_tool);
    try testing.expectEqualStrings("", chat.pending_call_id);
}

test "stream_done with unknown key does not finalize active stream" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    try testing.expect(chat.streaming);

    main.update(&model, .{ .stream_done = .{ .key = fk + 999, .outcome = .ok, .body = "" } }, &fx);

    try testing.expect(chat.streaming);
}

test "remove key clears the targeted provider not the first keyed one" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.settings.providers[0] = .{
        .id = "openai",
        .name = "OpenAI",
        .has_key = true,
        .via_env = false,
        .key_source = "user",
        .model_count = 1,
    };
    model.settings.providers[0].model_indices[0] = 0;
    model.settings.providers[1] = .{
        .id = "anthropic",
        .name = "Anthropic",
        .has_key = true,
        .via_env = false,
        .key_source = "user",
        .model_count = 1,
    };
    model.settings.providers[1].model_indices[0] = 1;
    model.settings.provider_count = 2;
    model.available_models[0] = .{ .id = "openai:gpt", .name = "GPT", .provider = "openai", .provider_display = "OpenAI", .key_source = "user" };
    model.available_models[1] = .{ .id = "anthropic:claude", .name = "Claude", .provider = "anthropic", .provider_display = "Anthropic", .key_source = "user" };
    model.available_model_count = 2;

    main.update(&model, .{ .remove_key = 1 }, &fx);
    const req = fx.pendingFetchAt(fx.pendingFetchCount() - 1).?;
    main.update(&model, .{ .key_deleted = .{ .key = req.key, .outcome = .ok, .body = "" } }, &fx);

    try testing.expect(!model.settings.providers[1].has_key);
    try testing.expect(model.settings.providers[0].has_key);
    try testing.expectEqual(@as(usize, 1), model.settings.providers[0].model_count);
}

test "inline add key marks provider and models usable" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    model.settings.key_modal_visible = false;
    model.settings.providers[0] = .{
        .id = "anthropic",
        .name = "Anthropic",
        .has_key = false,
        .key_source = "none",
        .adding_key = true,
        .key_input = "sk-ant-test",
        .model_count = 1,
    };
    model.settings.providers[0].model_indices[0] = 0;
    model.settings.provider_count = 1;
    model.available_models[0] = .{ .id = "anthropic:claude", .name = "Claude", .provider = "anthropic", .provider_display = "Anthropic", .key_source = "none" };
    model.available_model_count = 1;

    main.update(&model, .{ .key_saved = .{ .key = 50, .outcome = .ok, .body = "" } }, &fx);

    try testing.expect(model.settings.providers[0].has_key);
    try testing.expectEqualStrings("user", model.settings.providers[0].key_source);
    try testing.expectEqualStrings("user", model.available_models[0].key_source);
}

test "search backspace deletes a full UTF-8 code point" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .search_input = .{ .insert_text = "café" } }, &fx);
    try testing.expectEqualStrings("café", model.search_query);
    main.update(&model, .{ .search_input = .delete_backward }, &fx);
    try testing.expectEqualStrings("caf", model.search_query);
}

test "addHistoryMessage beyond default capacity grows the buffer without corrupting previous messages" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();

    var i: usize = 0;
    while (i < main.default_message_capacity) : (i += 1) {
        main.addMessage(chat, arena, "user", "msg");
    }
    try testing.expectEqual(main.default_message_capacity, chat.msg_count);
    chat._messages[chat.msg_count - 1].timestamp = "SENTINEL";

    const item_json = "{\"role\":\"assistant\",\"content\":\"overflow\",\"timestamp\":\"2026-01-01T12:34:56Z\"}";
    const parsed = try std.json.parseFromSlice(std.json.Value, arena, item_json, .{});
    defer parsed.deinit();
    main.addHistoryMessage(chat, arena, parsed.value);

    // The buffer grows on demand instead of dropping the overflow message.
    try testing.expectEqual(main.default_message_capacity + 1, chat.msg_count);
    try testing.expectEqualStrings("overflow", chat._messages[chat.msg_count - 1].content);
    try testing.expectEqualStrings("SENTINEL", chat._messages[chat.msg_count - 2].timestamp);
    try testing.expectEqualStrings("msg", chat._messages[chat.msg_count - 2].content);
}

test "virtual list mounts a viewport-sized window at 10k messages" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();
    main.seedStressTranscript(chat, arena, 10_000);
    try testing.expectEqual(@as(usize, 10_000), chat.msg_count);

    const tree = try buildTree(arena, &model);
    const list = findChatTranscriptList(tree.root) orelse return error.VirtualListNotFound;
    // The windowed list mounts only the visible window + overscan, never the
    // full 10k-item transcript.
    try testing.expect(list.children.len <= 64);
    // The whole tree stays far under the 1024-node per-view budget.
    const total = countAllWidgets(tree.root);
    try testing.expect(total < 1024);
}

test "extent cache is correct and O(1) at 10k messages" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();
    main.seedStressTranscript(chat, arena, 10_000);

    // First estimate call recomputes the cache; every group extent is positive.
    const first = main.groupExtentEstimate(chat, 0);
    try testing.expect(first > 0);
    try testing.expect(chat._group_extents_count > 0);
    try testing.expect(chat._group_extents_count <= chat.msg_count);
    var i: usize = 0;
    while (i < chat._group_extents_count) : (i += 1) {
        try testing.expect(chat._group_extents[i] > 0);
    }

    // Cache is stable across calls (no recompute churn).
    const cached_count = chat._group_extents_count;
    _ = main.groupExtentEstimate(chat, 0);
    try testing.expectEqual(cached_count, chat._group_extents_count);

    // Cache invalidates when messages change.
    main.addMessage(chat, arena, "user", "new message");
    _ = main.groupExtentEstimate(chat, 0);
    try testing.expect(chat._group_extents_count > cached_count);
}

test "seedStressTranscript is deterministic" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();
    main.seedStressTranscript(chat, arena, 1_000);

    var model2 = main.initialModel();
    model2.allocator = arena;
    const chat2 = model2.activeChat();
    main.seedStressTranscript(chat2, arena, 1_000);

    try testing.expectEqual(chat.msg_count, chat2.msg_count);
    var i: usize = 0;
    while (i < chat.msg_count) : (i += 1) {
        try testing.expectEqualStrings(chat._messages[i].role, chat2._messages[i].role);
        try testing.expectEqual(chat._messages[i].content.len, chat2._messages[i].content.len);
        try testing.expectEqualSlices(u8, chat._messages[i].content, chat2._messages[i].content);
    }
}

test "stream_line ignores non-string event type without crashing" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    const before = chat.msg_count;

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":123,\"data\":{}}" } }, &fx);
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":null,\"data\":{}}" } }, &fx);

    try testing.expect(chat.streaming);
    try testing.expectEqual(before, chat.msg_count);
}

test "stream_line ignores non-object data without crashing" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    const before = chat.msg_count;

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":[1,2,3]}" } }, &fx);

    try testing.expect(chat.streaming);
    try testing.expectEqual(before, chat.msg_count);
}

test "stream_line usage with float token counts does not crash" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"usage\",\"data\":{\"usage\":{\"input_tokens\":10.0,\"output_tokens\":20.5}}}" } }, &fx);

    try testing.expect(chat.streaming);
    try testing.expectEqual(@as(u32, 10), chat.context_info.input_tokens);
    try testing.expectEqual(@as(u32, 20), chat.context_info.output_tokens);
}

test "stream_line text_delta with non-string delta is ignored" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();
    const before = chat.msg_count;

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"text_delta\",\"data\":{\"delta\":42}}" } }, &fx);

    try testing.expect(chat.streaming);
    try testing.expectEqual(before, chat.msg_count);
}

test "findChatByFetchKey matches even when streaming flag is cleared" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "Hello");
    const chat = model.activeChat();
    // Simulate the race: the streaming flag is cleared before the terminal
    // event arrives. The lookup must still find the chat so finalize runs.
    chat.streaming = false;
    try testing.expect(model.findChatByFetchKey(fk) != null);
}

test "stream watchdog force-finalizes a stuck chat" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    _ = sendAndStartStream(&model, &fx, "Hello");
    const chat = model.activeChat();
    try testing.expect(chat.streaming);
    const before = chat.msg_count;

    // Age the stream's LAST EVENT past the watchdog deadline (terminal
    // event lost / connection dead — no keepalive pings arrived).
    chat.last_stream_event_at = main.nowMillis() - main.stream_watchdog_ms - 1000;
    main.update(&model, .{ .tick = .{ .key = 1 } }, &fx);

    try testing.expect(!chat.streaming);
    try testing.expectEqual(@as(u64, 0), chat.fetch_key);
    try testing.expectEqualStrings("", chat.status_text);
    // The watchdog adds a timed-out error message.
    try testing.expectEqual(before + 1, chat.msg_count);
    try testing.expectEqualStrings("Stream error: timed_out", chat._messages[chat.msg_count - 1].content);
}

test "stream watchdog does not kill a long-but-alive stream" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    _ = sendAndStartStream(&model, &fx, "Hello");
    const chat = model.activeChat();

    // The stream has been running LONG (start aged past the old total
    // deadline) but lines are still arriving (keepalive pings) — the
    // gap-based watchdog must NOT force-finalize it.
    chat.stream_started_at = main.nowMillis() - main.stream_watchdog_ms - 5000;
    chat.last_stream_event_at = main.nowMillis() - 1000;
    main.update(&model, .{ .tick = .{ .key = 1 } }, &fx);

    // Still streaming, still owns the fetch: the gap watchdog left it alone.
    try testing.expect(chat.streaming);
    try testing.expect(chat.fetch_key != 0);
}

test "stream_error finalizes stream state fully" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    _ = sendAndStartStream(&model, &fx, "Hello");
    const chat = model.activeChat();
    try testing.expect(chat.streaming);

    main.update(&model, .{ .stream_error = "boom" }, &fx);

    try testing.expect(!chat.streaming);
    try testing.expectEqual(@as(u64, 0), chat.fetch_key);
    try testing.expectEqualStrings("", chat.status_text);
    try testing.expectEqualStrings("", chat.open_bubble_type);
    try testing.expectEqualStrings("boom", chat._messages[chat.msg_count - 1].content);
}

test "retry re-sends the last user message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const chat = model.activeChat();
    main.addMessage(chat, arena, "user", "hello");
    main.addMessage(chat, arena, "system", "Stream error: timed_out");
    const before = fx.pendingFetchCount();

    main.update(&model, .retry, &fx);

    try testing.expectEqual(before + 1, fx.pendingFetchCount());
    try testing.expect(chat.streaming);
    try testing.expectEqualStrings("hello", chat._messages[chat.msg_count - 2].content);
}

test "retry is a no-op while streaming" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    _ = sendAndStartStream(&model, &fx, "hello");
    const before = fx.pendingFetchCount();

    main.update(&model, .retry, &fx);

    try testing.expectEqual(before, fx.pendingFetchCount());
}

test "tick shows elapsed seconds in the thinking indicator" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    _ = sendAndStartStream(&model, &fx, "hello");
    const chat = model.activeChat();
    chat.stream_started_at = main.nowMillis() - 12_000;

    main.update(&model, .{ .tick = .{ .key = 1 } }, &fx);

    try testing.expectEqualStrings("Thinking… 12s", chat.status_text);
}

test "model menu lists only ready models and selects one" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    const models_body =
        \\{"models":[
        \\{"id":"agnes:agnes-2.0-flash","name":"Agnes 2.0 Flash","provider":"agnes","provider_display":"Agnes","key_source":"hosted","billing_mode":"hosted"},
        \\{"id":"openai:gpt-4.1","name":"GPT-4.1","provider":"openai","provider_display":"OpenAI","key_source":"none","billing_mode":"api_key"},
        \\{"id":"ollama-cloud:deepseek-v4-flash:0731","name":"DeepSeek V4 Flash 0731","provider":"ollama-cloud","provider_display":"Ollama Cloud","key_source":"hosted","billing_mode":"hosted"}
        \\]}
    ;
    main.update(&model, .{ .models_loaded = .{
        .key = 9,
        .outcome = .ok,
        .body = models_body,
    } }, &fx);

    // Open the menu.
    main.update(&model, .toggle_model_menu, &fx);
    try testing.expect(model.model_menu_open);

    const tree = try buildTree(arena, &model);
    // Ready models are listed; the keyless model is not.
    try testing.expect(findByText(tree.root, .menu_item, "Agnes · Agnes 2.0 Flash") != null);
    try testing.expect(findByText(tree.root, .menu_item, "Ollama Cloud · DeepSeek V4 Flash 0731") != null);
    try testing.expect(findByText(tree.root, .menu_item, "OpenAI · GPT-4.1") == null);

    // Search narrows the list.
    main.update(&model, .{ .model_menu_search_input = .{ .insert_text = "deepseek" } }, &fx);
    try testing.expectEqualStrings("deepseek", model.model_menu_search);
    const tree2 = try buildTree(arena, &model);
    try testing.expect(findByText(tree2.root, .menu_item, "Ollama Cloud · DeepSeek V4 Flash 0731") != null);
    try testing.expect(findByText(tree2.root, .menu_item, "Agnes · Agnes 2.0 Flash") == null);

    // Selecting the filtered model (index 0 = DeepSeek) updates the
    // composer selection and fires a save.
    const before = fx.pendingFetchCount();
    main.update(&model, .{ .model_menu_select = 0 }, &fx);
    try testing.expect(!model.model_menu_open);
    try testing.expectEqualStrings("ollama-cloud:deepseek-v4-flash:0731", model.selectedModel());
    try testing.expectEqual(before + 1, fx.pendingFetchCount());
}

test "rubric verdict settles a collapsed row from the done verification" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();

    const parsed = std.json.parseFromSlice(
        std.json.Value,
        arena,
        \\{"status":"satisfied","attempts":1,"max_attempts":3,"evaluations":[{"result":"satisfied","explanation":"ok","criteria":[{"name":"a","passed":true},{"name":"b","passed":true}]}]}
    , .{}) catch return;
    main.addRubricRowFromVerdict(chat, arena, parsed.value.object);

    try testing.expectEqual(@as(usize, 1), chat.msg_count);
    try testing.expectEqualStrings("rubric", chat._messages[0].role);
    try testing.expectEqualStrings("Rubric", chat._messages[0].tool_name);
    try testing.expectEqualStrings("Passed (2/2)", chat._messages[0].tool_status);
    try testing.expect(chat._messages[0].collapsed);
    try testing.expect(std.mem.indexOf(u8, chat._messages[0].content, "2/2 criteria passed") != null);
}

test "in-place revision keeps one answer bubble and drops the rubric row" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    const chat = model.activeChat();

    main.addMessage(chat, arena, "user", "hi");
    main.addMessage(chat, arena, "assistant", "first attempt");
    main.upsertRubricRow(chat, arena, "Needs revision (1/2)", "1/2 criteria passed\ngap", false);
    try testing.expectEqual(@as(usize, 3), chat.msg_count);

    // Simulate the response_revision_start handler body: remove the row,
    // clear the answer, open the assistant bubble for the revised stream.
    main.removeRubricRows(chat);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    var i: usize = chat.msg_count;
    while (i > 0) {
        i -= 1;
        if (std.mem.eql(u8, chat._messages[i].role, "assistant")) {
            chat._messages[i].content = "";
            break;
        }
    }
    try testing.expectEqualStrings("", chat._messages[1].content);

    // The revised deltas append into the SAME assistant bubble.
    main.appendToLastMessage(chat, arena, "assistant", "revised answer");
    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    try testing.expectEqualStrings("revised answer", chat._messages[1].content);
}

test "connectorConnectBody escapes control characters as \\u00XX" {
    var arena_state = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena_state.deinit();
    const allocator = arena_state.allocator();

    var row = main.ConnectorRow{
        .name = "fixture-api",
        .display = "Fixture API",
        .description = "test",
        .category = "test",
        .auth_type = "api_key",
        .connected = false,
    };
    row.required_fields[0] = .{
        .name = "api_key",
        .label = "API Key",
        .placeholder = "",
        .input_type = "password",
        .optional = false,
        .help_text = "",
    };
    row.field_count = 1;

    // Control chars 0x01, 0x08 (backspace), 0x0c (form feed) must be
    // escaped — raw control bytes are invalid inside JSON strings.
    const buffers = [_][]const u8{ "sk\x01\x08\x0ctest", "", "", "" };
    const body = try main.connectorConnectBody(allocator, &row, &buffers);
    try testing.expectEqualStrings("{\"api_key\":\"sk\\u0001\\u0008\\u000ctest\"}", body);
}

test "connectorConnectBody escapes quotes and backslashes, skips empty buffers" {
    var arena_state = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena_state.deinit();
    const allocator = arena_state.allocator();

    var row = main.ConnectorRow{
        .name = "fixture-api",
        .display = "Fixture API",
        .description = "test",
        .category = "test",
        .auth_type = "api_key",
        .connected = false,
    };
    row.required_fields[0] = .{
        .name = "api_key",
        .label = "API Key",
        .placeholder = "",
        .input_type = "password",
        .optional = false,
        .help_text = "",
    };
    row.required_fields[1] = .{
        .name = "secret",
        .label = "Secret",
        .placeholder = "",
        .input_type = "password",
        .optional = true,
        .help_text = "",
    };
    row.field_count = 2;

    const buffers = [_][]const u8{ "a\"b\\c", "", "", "" };
    const body = try main.connectorConnectBody(allocator, &row, &buffers);
    // Empty second field is skipped; quote and backslash escaped.
    try testing.expectEqualStrings("{\"api_key\":\"a\\\"b\\\\c\"}", body);
}

test "credential form node budget covers a 4-field connector" {
    // Regression: the form used [max_required_fields + 6] = [10] but a
    // 4-field connector writes heading + subtitle + optional error + 8 field
    // nodes (label + textarea each) + submit/cancel row = 12 nodes — out of
    // bounds (Debug panic / ReleaseFast stack corruption). The budget
    // constant must cover every write the render performs at max field count.
    const heading = 1;
    const subtitle = 1;
    const error_line = 1;
    const nodes_per_field = 2; // label + textarea
    const buttons_row = 1;
    const max_writes = heading + subtitle + error_line + nodes_per_field * main.max_required_fields + buttons_row;
    try testing.expectEqual(12, max_writes);
    try testing.expect(main.credential_form_nodes >= max_writes);
    try testing.expectEqual(12, main.credential_form_nodes);
}

test "disambiguateChatTitles suffixes duplicate titles with timestamps" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    // Two chats sharing a title, one unique.
    model.chats[0].title = "Connect Gmail account test@gmail.com";
    model.chats[0].created_at = "2026-08-24T12:34:56";
    model.chats[0].setSessionIdStr("chat-2");
    model.chats[1].title = "Connect Gmail account test@gmail.com";
    model.chats[1].created_at = "2026-08-24T09:00:00";
    model.chats[1].setSessionIdStr("chat-3");
    model.chats[2].title = "Unique chat";
    model.chat_count = 3;

    main.disambiguateChatTitles(&model);

    // Duplicates gain a timestamp suffix; unique title untouched.
    try testing.expect(std.mem.startsWith(u8, model.chats[0].title, "Connect Gmail account test@gmail.com · 08-24"));
    try testing.expect(std.mem.startsWith(u8, model.chats[1].title, "Connect Gmail account test@gmail.com · 08-24"));
    try testing.expectEqualStrings("Unique chat", model.chats[2].title);
    // The two duplicates remain distinguishable from each other.
    try testing.expect(!std.mem.eql(u8, model.chats[0].title, model.chats[1].title));
}

test "disambiguateChatTitles falls back to session-id tail without created_at" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;

    model.chats[0].title = "Same title";
    model.chats[0].setSessionIdStr("chat-7");
    model.chats[1].title = "Same title";
    model.chats[1].setSessionIdStr("chat-8");
    model.chat_count = 2;

    main.disambiguateChatTitles(&model);

    try testing.expect(!std.mem.eql(u8, model.chats[0].title, model.chats[1].title));
    try testing.expect(std.mem.endsWith(u8, model.chats[0].title, "…chat-7") or std.mem.endsWith(u8, model.chats[1].title, "…chat-7"));
}
