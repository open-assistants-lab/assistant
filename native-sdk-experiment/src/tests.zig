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

    main.update(&model, .{ .input_changed = .{ .insert_text = "Hello" } }, &fx);
    try testing.expectEqualStrings("Hello", model.input_text);

    main.update(&model, .send_message, &fx);
    try testing.expectEqual(@as(usize, 1), model.msg_count);
    try testing.expectEqualStrings("user", model._messages[0].role);
    try testing.expectEqualStrings("Hello", model._messages[0].content);
    try testing.expect(model.streaming);
    try testing.expectEqualStrings("", model.input_text);
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
    try testing.expectEqualStrings("hello", model.input_text);

    main.update(&model, .{ .input_changed = .delete_backward }, &fx);
    try testing.expectEqualStrings("hell", model.input_text);

    main.update(&model, .{ .input_changed = .clear }, &fx);
    try testing.expectEqualStrings("", model.input_text);
}

test "stream_line appends to assistant message" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

    main.update(&model, .{ .input_changed = .{ .insert_text = "Hi" } }, &fx);
    main.update(&model, .send_message, &fx);

    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\"Hello\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello", model._messages[1].content);

    main.update(&model, .{ .stream_line = .{ .key = 0, .line = "data: {\"type\":\"messages\",\"data\":{\"content\":\" world\"}}" } }, &fx);
    try testing.expectEqualStrings("Hello world", model._messages[1].content);
}

test "interrupt sets pending state" {
    var arena_state = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var model = main.initialModel();
    model.allocator = arena;
    var fx = noopFx(arena);

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
    _ = try expectByText(tree.root, .text, "Send a message to start...");
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
