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

test "shift+enter inserts newline without clearing draft" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello world" } }, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "\n" } }, &fx);

    try testing.expectEqualStrings("hello world\n", model.activeChat().draft_text);
    try testing.expect(!model.activeChat().streaming);
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
    try testing.expect(std.mem.indexOf(u8, request.body, "agnes:agnes-2.0-flash") != null);
}

test "default model falls back to hosted Agnes" {
    var model = main.initialModel();

    try testing.expectEqualStrings("agnes:agnes-2.0-flash", model.selectedModel());
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
    const btn = findButtonContaining(tree.root, "Hosted") orelse return error.WidgetNotFound;
    const send_btn = findButtonContaining(tree.root, "Send") orelse return error.WidgetNotFound;
    // Hosted button and Send button are in the same row (same y)
    try testing.expectApproxEqAbs(btn.frame.y, send_btn.frame.y, 1.0);
    // Hosted button fits its content (not full width)
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

    // Simulate title generation response
    const title_response_body =
        \\{"title":"Shanghai Weather Forecast","session_id":"chat-1"}
    ;
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
    try testing.expectEqual(@as(f32, 8.0), tokens.radius.sm);
    try testing.expectEqual(@as(f32, 12.0), tokens.radius.md);
    try testing.expectEqual(@as(f32, 14.0), tokens.radius.lg);
    try testing.expectEqual(@as(f32, 18.0), tokens.radius.xl);
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
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);
    model.activeChat().draft_text = "test";
    main.update(&model, .send_message, &fx);
    model.activeChat().streaming = false;
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 2), model.chat_count);
    try testing.expectEqual(@as(usize, 1), model.active_chat_idx);
    try testing.expectEqual(@as(usize, 0), model.chats[1].msg_count);
}

test "chat list: switch chat sets active index" {
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
    try testing.expectEqual(@as(usize, 2), model.active_chat_idx);
    const first_chat_id = model.chats[0].id;
    main.update(&model, .{ .switch_chat = first_chat_id }, &fx);
    try testing.expectEqual(@as(usize, 0), model.active_chat_idx);
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

test "smart new chat: stays on empty chat with draft" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);

    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);
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

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "draft2" } }, &fx);
    try testing.expectEqualStrings("draft2", model.inputText());

    main.update(&model, .{ .switch_chat = model.chats[0].id }, &fx);
    try testing.expectEqualStrings("", model.inputText());

    main.update(&model, .{ .switch_chat = model.chats[1].id }, &fx);
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
