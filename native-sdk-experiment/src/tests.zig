const std = @import("std");
const native_sdk = @import("native_sdk");
const main = @import("main.zig");

const canvas = native_sdk.canvas;
const testing = std.testing;

const AppUi = main.AppUi;
const Model = main.Model;
const Msg = main.Msg;
const Effects = main.Effects;

const AppMarkup = canvas.MarkupView(Model, Msg);

fn buildTree(arena: std.mem.Allocator, model: *const Model) !AppUi.Tree {
    var view = try AppMarkup.init(arena, main.app_markup);
    var ui = AppUi.init(arena);
    const node = view.build(&ui, model) catch |err| {
        if (err == error.MarkupBuild) {
            std.debug.print("app.native:{d}:{d}: {s}\n", .{ view.diagnostic.line, view.diagnostic.column, view.diagnostic.message });
        }
        return err;
    };
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

fn noopFx(allocator: std.mem.Allocator) Effects {
    var fx = Effects.init(allocator);
    fx.executor = .fake;
    return fx;
}

test "send message adds user message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    try testing.expectEqualStrings("Hello", model.inputText());

    main.update(&model, .send_message, &fx);
    const chat = model.activeChat();
    // user message + empty assistant typing indicator
    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    try testing.expectEqualStrings("user", chat._messages[0].role);
    try testing.expectEqualStrings("Hello", chat._messages[0].content);
    try testing.expectEqualStrings("assistant", chat._messages[1].role);
    try testing.expectEqualStrings("", chat._messages[1].content);
    try testing.expect(model.streaming);
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

test "stream_line appends to assistant message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "Hi" } }, &fx);
    main.update(&model, .send_message, &fx);

    // After send: user message + empty assistant (typing indicator)
    const chat = model.activeChat();
    try testing.expectEqual(@as(usize, 2), chat.msg_count);
    try testing.expectEqualStrings("", chat._messages[1].content);

    // First stream line replaces empty content
    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hello\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello", chat._messages[1].content);

    // Second stream line appends
    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\" world\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello world", chat._messages[1].content);
}

test "interrupt sets pending state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(model.has_pending);
    try testing.expectEqualStrings("email_send", model.pending_tool);
    try testing.expectEqualStrings("abc123", model.pending_call_id);
}

test "approve clears pending" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(model.has_pending);

    main.update(&model, .approve, &fx);
    try testing.expect(!model.has_pending);
    try testing.expect(model.streaming);
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
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

    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"interrupt\",\"data\":{\"tool\":\"email_send\",\"call_id\":\"abc123\",\"args\":{}}}" } }, &fx);
    try testing.expect(model.has_pending);

    main.update(&model, .reject, &fx);
    try testing.expect(!model.has_pending);
    try testing.expectEqualStrings("", model.pending_tool);
    try testing.expectEqual(@as(usize, 1), fx.pendingFetchCount());
    const request = fx.pendingFetchAt(0).?;
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
    // Color channels are f32 normalized to [0,1] (rgb8 divides by 255).
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
    // First chat is empty (no messages) — new_chat should NOT create another
    main.update(&model, .new_chat, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);
    // Send a message to make the first chat non-empty
    model.activeChat().draft_text = "test";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
    // Now new_chat should create a second chat
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
    // Make first chat non-empty, then create second
    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
    main.update(&model, .new_chat, &fx);
    // Make second chat non-empty, then create third
    model.activeChat().draft_text = "second";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
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

    // Make first chat non-empty, create second, make second non-empty, create third
    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
    main.update(&model, .new_chat, &fx);
    model.activeChat().draft_text = "second";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
    main.update(&model, .new_chat, &fx);
    model.active_chat_idx = 1;

    model.chats[0].title = "First chat";
    model.chats[1].title = "Second chat";
    main.addMessage(&model.chats[0], arena, "user", "hi");

    main.update(&model, .{ .stream_done = .{ .key = 0 } }, &fx);
    try testing.expectEqual(@as(u32, 1), model.chats[0].unread_count);
    try testing.expectEqual(@as(u32, 0), model.chats[1].unread_count);
}

test "unread badge: switch chat resets unread count" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    // Make first chat non-empty, create second
    model.activeChat().draft_text = "first";
    main.update(&model, .send_message, &fx);
    model.streaming = false;
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

    // Type a draft but don't send
    main.update(&model, .{ .input_changed = .{ .insert_text = "hello" } }, &fx);
    try testing.expectEqual(@as(usize, 1), model.chat_count);

    // Press new_chat — should stay on current empty chat (has draft, no messages)
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

    // Chat 1: type and send a message
    main.update(&model, .{ .input_changed = .{ .insert_text = "first" } }, &fx);
    main.update(&model, .send_message, &fx);
    model.streaming = false;

    // Create chat 2, type a draft
    main.update(&model, .new_chat, &fx);
    main.update(&model, .{ .input_changed = .{ .insert_text = "draft2" } }, &fx);
    try testing.expectEqualStrings("draft2", model.inputText());

    // Switch back to chat 1 — draft should be empty (it was sent)
    main.update(&model, .{ .switch_chat = model.chats[0].id }, &fx);
    try testing.expectEqualStrings("", model.inputText());

    // Switch back to chat 2 — draft should be preserved
    main.update(&model, .{ .switch_chat = model.chats[1].id }, &fx);
    try testing.expectEqualStrings("draft2", model.inputText());
}
