# Virtual List Migration Plan — Auto-scroll chat transcript

> **Goal:** Replace `<scroll>` + `<column>` message list with a Zig-view `virtualList` using `anchor="trailing"` for proper chat auto-scroll behavior.

## Problem

The current `<scroll>` + `<column>` markup pattern:
- Does NOT auto-scroll to bottom when new messages arrive
- Does NOT open at the bottom on first render
- Requires the user to manually scroll down after each message

The Native SDK's `VirtualListOptions.anchor = .trailing` is the documented "chat contract":
> "The first build opens at the bottom, and while the user sits at the bottom an appended batch keeps the list pinned there (scrolled away, appends never yank the viewport)."

**Constraint:** `virtualList` with `anchor="trailing"` is **builder-only** (Zig views), NOT available in markup. The SDK docs explicitly state: "markup keeps bounded `<list virtualized>` (layout-culled) plus `on-reach-end` on `scroll` for honest infinite fetch."

**Second constraint:** `Options.view` and `Options.markup` render the SAME view — they are NOT for different parts of the UI. The SDK docs say: "At least one of `view` and `markup` must be set. When both are set, this view renders until the watched markup file first diverges from the embedded source, at which point the interpreter takes over (compiled view for release, hot reload in dev)."

**This means:** To use `virtualList`, the ENTIRE view must be a Zig function. We cannot keep the sidebar in markup and only the message list in Zig. However, we can use `CompiledMarkupView` fragments to embed markup-authored sub-views inside the Zig view builder.

## Architecture

### Current (markup-only)
```
app.native → <split> → [sidebar markup] + [<scroll> → <column> → <for> → bubbles]
```

### Target (full Zig view with markup fragments)
```
main.zig → buildView(ui, model) → <split>
  ├── buildSidebar(ui, model)  — Zig view (was markup)
  └── buildChatPanel(ui, model) → virtualList with anchor="trailing"
```

The sidebar, composer, HITL bar, and empty state are all rebuilt as Zig view functions. The markup file (`app.native`) becomes a fallback/compiled source, or is removed entirely.

**Alternative:** Use `canvas.CompiledMarkupView(Model, Msg, source)` to compile the sidebar markup at build time and embed it as a fragment in the Zig view. This preserves the sidebar's declarative authoring while allowing the message list to use `virtualList`. The SDK supports `fragment_watch` for hot-reload of these fragments in Debug builds.

## Migration Steps

### Step 1: Write the full Zig view builder

Create a `buildView` function that replaces the markup:

```zig
fn buildView(ui: *AppUi, model: *const Model) AppUi.Node {
    return ui.split(.{
        .value = model.sidebar_split,
        .on_resize = .sidebar_resized,
    }, .{
        buildSidebar(ui, model),
        buildChatPanel(ui, model),
    });
}
```

### Step 2: Write buildSidebar (Zig view)

Recreate the sidebar as Zig view calls:
- New chat button
- Search input
- Chat list (can use `<scroll>` + `<for>` equivalent in Zig, or `virtualList` if needed)
- Bottom nav (Tools, Skills, Subagents)
- Settings + theme toggle

### Step 3: Write buildChatPanel with virtualList

```zig
fn buildChatPanel(ui: *AppUi, model: *const Model) AppUi.Node {
    const chat = &model.chats[model.active_chat_idx];
    const count = chat.msg_count;

    var children: [3]AppUi.Node = undefined;
    var child_count: usize = 0;

    // Message list (virtual list with trailing anchor)
    if (count == 0) {
        children[child_count] = buildEmptyState(ui);
    } else {
        const options = AppUi.VirtualListOptions{
            .id = "chat-messages",
            .item_count = count,
            .item_extent = 80,
            .gap = 12,
            .anchor = .trailing,
            .viewport_fallback = 600,
        };
        const window = ui.virtualWindow(options);
        // Build nodes for visible range
        var nodes: [max_messages]AppUi.Node = undefined;
        var node_count: usize = 0;
        var i = window.start_index;
        while (i < window.end_index and i < count) : (i += 1) {
            nodes[node_count] = buildMessageBubble(ui, &chat._messages[i]);
            node_count += 1;
        }
        children[child_count] = ui.virtualList(options, window, nodes[0..node_count]);
    }
    child_count += 1;

    // HITL bar (if pending)
    if (model.has_pending) {
        children[child_count] = buildHitlBar(ui, model);
        child_count += 1;
    }

    // Composer
    children[child_count] = buildComposer(ui, model);
    child_count += 1;

    return ui.column(.{ .background = .background, .gap = 0 }, children[0..child_count]);
}
```

### Step 4: Write buildMessageBubble, buildEmptyState, buildHitlBar, buildComposer

Each is a small Zig function that builds the equivalent of the markup elements:

```zig
fn buildMessageBubble(ui: *AppUi, msg: *const ChatMessage) AppUi.Node {
    if (std.mem.eql(u8, msg.role, "user")) {
        return ui.row(.{ .main = .end, .cross = .start }, .{
            ui.card(.{ .background = .surface_subtle, .radius = .lg, .padding = 12 }, .{
                ui.text(.{ .wrap = true }, msg.content),
            }),
        });
    } else {
        return ui.column(.{ .gap = 4 }, .{
            ui.text(.{ .size = .sm, .foreground = .accent }, "Assistant"),
            ui.card(.{ .background = .surface, .radius = .lg, .padding = 12, .border_color = .border }, .{
                ui.text(.{ .wrap = true }, msg.content),
            }),
        });
    }
}
```

### Step 5: Wire `view` into ChatApp.create

Replace `.markup` with `.view`:

```zig
const app_state = try ChatApp.create(allocator, .{
    .name = "native-sdk-experiment",
    .scene = shell_scene,
    .canvas_label = canvas_label,
    .update_fx = update,
    .tokens_fn = tokensFn,
    .init_fx = initFx,
    .view = buildView,  // Zig view replaces markup
});
```

Note: Remove `.markup` option. Hot reload for the view is lost, but `update_fx` still works for logic changes.

### Step 6: Update tests

The `buildTree` test helper currently uses `MarkupView`. With a Zig view, tests need to call `buildView` directly instead of parsing markup. The tree structure assertions (expectByText, findByText) should still work since they operate on the finalized tree.

### Step 7: Test

- Verify messages stack vertically (no overlap)
- Verify auto-scroll to bottom on first render
- Verify new messages keep the view pinned to bottom
- Verify scrolling up doesn't yank back to bottom
- Verify `native test` passes
- Verify `native dev` launches and renders correctly

## Alternative: Keep markup, accept no auto-scroll

If the full Zig view migration is too large, the current `<scroll>` + `<column>` pattern works correctly for message stacking — just without auto-scroll. The user must scroll down manually after sending a message. This is a UX limitation, not a bug.

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|------------|-----------|
| Zig view API differs from plan's assumptions | Medium | Verify against SDK source before implementing |
| Losing markup hot-reload slows iteration | High | Acceptable — `update_fx` still hot-reloads |
| Breaking existing tests | Medium | Update test helpers to call `buildView` |
| `virtualList` API complexity | Medium | Start with fixed `item_extent`, add variable later |
| Sidebar rebuild is tedious | Low | Use `CompiledMarkupView` fragment for sidebar |

## Estimated Effort

| Step | Time | Risk |
|------|------|------|
| Step 1: Full view builder | 1 hour | Low |
| Step 2: Sidebar in Zig | 2-3 hours | Medium |
| Step 3: Chat panel with virtualList | 2-3 hours | Medium |
| Step 4: Bubble/state/HITL/composer builders | 2 hours | Low |
| Step 5: Wire view option | 30 min | Low |
| Step 6: Update tests | 1-2 hours | Medium |
| Step 7: Test + debug | 2-3 hours | Medium |
| **Total** | **10-14 hours** | |

This is a significant refactor — essentially rewriting the entire view layer from markup to Zig. It should be a dedicated task.