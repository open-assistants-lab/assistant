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
    // Only user message — no empty assistant typing indicator anymore
    try testing.expectEqual(@as(usize, 1), chat.msg_count);
    try testing.expectEqualStrings("user", chat._messages[0].role);
    try testing.expectEqualStrings("Hello", chat._messages[0].content);
    try testing.expect(chat.streaming);
    try testing.expectEqualStrings("Thinking...", chat.status_text);
    try testing.expectEqualStrings("", model.inputText());
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
    try testing.expectEqualStrings("http://127.0.0.1:8080/message/stream", request.url);
    try testing.expect(std.mem.indexOf(u8, request.body, "deepseek:deepseek-v4-flash") != null);
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

    // First messages event creates assistant bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hello\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + assistant
    try testing.expectEqualStrings("assistant", chat._messages[1].role);
    try testing.expectEqualStrings("Hello", chat._messages[1].content);

    // Second messages event appends
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\" world\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello world", chat._messages[1].content);
}

test "reasoning event creates reasoning bubble" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\"I need to think...\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + reasoning
    try testing.expectEqualStrings("reasoning", chat._messages[1].role);
    try testing.expectEqualStrings("I need to think...", chat._messages[1].content);

    // Second reasoning delta appends
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\" about this.\"}}" } }, &fx);
    try testing.expectEqualStrings("I need to think... about this.", chat._messages[1].content);
}

test "tool_start creates tool bubble with running status" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);
    const fk = sendAndStartStream(&model, &fx, "test");
    const chat = model.activeChat();

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_start\",\"data\":{\"tool\":\"time_get\",\"call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // user + tool
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

    // tool_start
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_start\",\"data\":{\"tool\":\"time_get\",\"call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);

    // tool_result updates in place — no new bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_result\",\"data\":{\"tool\":\"time_get\",\"call_id\":\"call_1\",\"result\":\"Current time: 12:00 UTC\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count); // still 2, no new bubble
    try testing.expectEqualStrings("done", chat._messages[1].tool_status);
    try testing.expectEqualStrings("Current time: 12:00 UTC", chat._messages[1].tool_result);
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
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\"thinking 1\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 2), chat.msg_count);

    // Tool call (closes reasoning bubble)
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_start\",\"data\":{\"tool\":\"time_get\",\"call_id\":\"call_1\",\"args\":{}}}" } }, &fx);
    try testing.expectEqual(@as(usize, 3), chat.msg_count);

    // tool_result
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"tool_result\",\"data\":{\"tool\":\"time_get\",\"call_id\":\"call_1\",\"result\":\"12:00\"}}" } }, &fx);
    try testing.expectEqual(@as(usize, 3), chat.msg_count);

    // Second reasoning — new bubble
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\"thinking 2\"}}" } }, &fx);
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
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\"thinking...\"}}" } }, &fx);
    // assistant
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Answer\"}}" } }, &fx);

    try testing.expect(!chat._messages[1].collapsed); // reasoning not collapsed during stream

    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);
    try testing.expect(!chat.streaming);
    try testing.expectEqualStrings("", chat.open_bubble_type);
    try testing.expectEqualStrings("", chat.status_text);
    try testing.expect(chat._messages[1].collapsed); // reasoning collapsed after done
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
    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"It is hot and humid.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    // Title generation should have fired (1 stream fetch + 1 title fetch = 2)
    try testing.expectEqual(@as(usize, 2), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(1).?;
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

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hello!\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk } }, &fx);

    // Title generation should NOT fire (user message < 5 chars)
    // Only the stream fetch should be pending (1)
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
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
    main.update(&model, .{ .stream_line = .{ .key = fk1, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hot.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk1 } }, &fx);
    const fetch_count_after_first = fx.pendingFetchCount();

    // Second exchange
    model.activeChat().draft_text = "Thanks";
    model.activeChat().streaming = false;
    const fk2 = sendAndStartStream(&model, &fx, "Thanks");
    main.update(&model, .{ .stream_line = .{ .key = fk2, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"You're welcome.\"}}" } }, &fx);
    main.update(&model, .{ .stream_done = .{ .key = fk2 } }, &fx);

    // Title generation should NOT fire on second exchange (2 user messages now).
    // After first exchange: 2 fetches (stream1 + title). After second: +1 (stream2) = 3.
    // If title fired again it would be 4. So check it's exactly 3.
    try testing.expectEqual(fetch_count_after_first + 1, fx.pendingFetchCount());
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

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hot and humid.\"}}" } }, &fx);
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

    main.update(&model, .{ .stream_line = .{ .key = fk, .line = "data: {\"type\":\"reasoning\",\"data\":{\"content\":\"thinking...\"}}" } }, &fx);
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
