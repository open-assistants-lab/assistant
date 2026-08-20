# Tools Page — Phase A (Connectors + Built-in Tools) Implementation Plan

> **Status: EXECUTED (2026-08-21).** All 7 tasks landed on `main` (12 commits, `84ca8c4..8620606`), followed by two follow-ups: Tools folded into Settings as a section (`cba7566`) and the high-end settings redesign (`9b386b4`). Final whole-branch review clean after one fix wave (OOB credential-form array write + asymmetric panel precedence).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first real Tools page in the native chat app — a right-side panel (mirroring the Settings panel) with two tabs: **Built-in** (searchable list of the agent's built-in tools with per-tool enable/disable) and **Connections** (SaaS connector catalog with API-key connect and OAuth authorize flows).

**Architecture:** Purely a frontend (Zig) effort — the backend already exposes everything needed: `GET /tools` + `PATCH /tools/{name}` (enable/disable, server-side loop reset), `GET /connectors/catalog` (already includes a `connected` flag per service), `POST /connectors/connect`, `DELETE /connectors/disconnect`, and the ConnectKit OAuth router (`/auth/login`, `/auth/callback`, `/auth/status`). The page follows the existing Settings panel pattern in `native-sdk-experiment/src/main.zig` exactly: `ToolsState` struct on `Model`, `Msg` variants, `fx.fetch`/`Effects` for HTTP, a `buildToolsPanel` alongside `buildSettingsPanel`, and the sidebar "Tools" row gets an `on_press` handler. Script-authored tools are deliberately **out of scope** (Phase A.2).

**Tech Stack:** Zig 0.16.0 (Native SDK, declarative views + canvas UI), ConnectKit 0.1.4 (pinned, already a dependency), FastAPI backend at `http://127.0.0.1:8080`.

**Spec:** Design captured in this plan — canonical naming rule (UI labels MUST match backend jargon: "Tools", "Skills", "Subagents", "Settings") decided in the 2026-08-21 brainstorming session. No separate spec doc.

## Global Constraints

- **Naming rule (user decision, 2026-08-21):** UI nav labels are exactly `Tools`, `Skills`, `Subagents`, `Settings` — no renamed concepts ("Connections" is a *section title inside* the Tools page, never a nav label). Human-friendly copy lives inside pages only.
- **Quality gates (every task's final step runs these):** `uv run pytest`, `uv run ruff check src/`, `uv run mypy src/`, `uv run native test` (87 tests), `bash tests/frontend_suite.sh --all` (37+ tests, run from `native-sdk-experiment/`).
- **Backend contract (fixed, no backend changes in Phase A):** base URL `http://127.0.0.1:8080`; `user_id=native_sdk_chat`; `workspace_id=personal`.
- **Tool toggle semantics:** `PATCH /tools/{name}` with body `{"scope": "all"}` (enable) or `{"scope": "none"}` (disable). The backend resets the user's agent loops on toggle — do NOT reset loops client-side.
- **Connector states (from `GET /connectors/catalog`):** each item has `name`, `display`, `icon`, `category`, `description`, `auth_type` (`oauth2`|`api_key`), `required_fields` (`[{name,label,placeholder,input_type,optional,help_text}]`), `connected` (bool). Do not invent fields.
- **OAuth flow:** `POST /connectors/connect` stores client credentials → returns `{"status":"configured","next_step":"/auth/login?service=X&user_id=Y"}`; the app then opens `http://127.0.0.1:8080/auth/login?service=X&user_id=native_sdk_chat` in the system browser and polls until `connected` flips. `api_key` connectors: `POST /connectors/connect` with the credential fields → immediate `{"status":"connected"}`.
- **App is macOS-only** (`app.zon` `.platforms = .{"macos"}`) — system browser open = `open <url>` via `std.process.Child`.
- **Capacity caps** (mirror `max_visible_settings_rows = 120`): `max_visible_tools_rows = 200`, `max_connector_rows = 128`, `max_required_fields = 4`. Anything beyond cap truncates; the search field is the escape hatch.
- **Panel precedence in `buildView`:** Tools panel takes precedence over Settings (opening one closes the other) — `if (model.tools.visible) buildToolsPanel else if (model.settings.visible) buildSettingsPanel else buildChatPanel`.
- **Reduced motion:** honor `model.settings.reduced_motion` for the panel entrance animation exactly like Settings does.
- Out of scope: script-built tools, skills page, subagents page, any backend endpoint changes.

---

### Task 1: Tools panel shell — sidebar wiring + open/close/tabs

**Files:**
- Modify: `native-sdk-experiment/src/main.zig` (Msg enum ~line 260, Model ~line 342, `buildSidebar` nav rows ~2781, `buildView` ~2599, new `buildToolsPanel`)
- Modify: `native-sdk-experiment/src/app.native` (sidebar mirror rows ~62-75) — only if `grep -n "app.native" src/main.zig` shows it is embedded/loaded; otherwise skip with a comment (verified in Step 1)
- Test: `native-sdk-experiment/tests/frontend_suite.sh` (new `test_tools()` + `--tools` mode)

**Interfaces:**
- Consumes: existing `AppUi` helpers (`ui.row`, `ui.text`, `ui.icon`, `ui.button`, `ui.scroll`), `Effects.responseMsg`, `AppUi.inputMsg` — same shapes as the Settings panel code already uses.
- Produces: `Msg` variants `open_tools`, `close_tools`, `tools_tab_builtin`, `tools_tab_connections`; `Model.tools: ToolsState` with `visible: bool`, `section: enum { builtin, connections }`, `loading: bool`; `fn buildToolsPanel(ui: *AppUi, model: *const Model) AppUi.Node`.

- [ ] **Step 1: Confirm whether `app.native` is loaded at runtime**

Run: `grep -n "app.native\|embedFile" src/main.zig`
Expected: fonts are embedded via `@embedFile`; no `app.native` reference → it is a markup twin, not the live view. **Ruling from controller pre-flight scan:** this was verified at plan-writing time (`grep` found no `app.native` embed) — update it in every task for consistency but never treat it as the source of truth, and never gate tests on it.

- [ ] **Step 2: Write the failing frontend test** — add to `tests/frontend_suite.sh` (after `test_settings`, ~line 546):

```bash
# NOTE: Tools row in the sidebar is a pressable row (role=group, name="")
# with the "Tools" label on a child text — located like Settings.
test_tools() {
  echo ""
  echo "=== 8. Tools Panel: Open, tabs, close ==="

  TOOLS=$(find_pressable_by_child_text "Tools")
  if [ -z "$TOOLS" ]; then
    fail "Tools pressable row not found"
    return 1
  fi
  native automate widget-action main-canvas "$TOOLS" press > /dev/null 2>&1
  if native automate assert --timeout-ms 3000 'role=text name="Tools"' > /dev/null 2>&1; then
    pass "tools panel opens with a Tools header"
  else
    fail "tools panel did not open"
  fi
  # Both tab labels present
  if native automate assert --timeout-ms 3000 'role=text name="Built-in"' > /dev/null 2>&1 &&
     native automate assert --timeout-ms 3000 'role=text name="Connections"' > /dev/null 2>&1; then
    pass "tools panel shows Built-in and Connections tabs"
  else
    fail "tools panel tabs missing"
  fi
  # Toggle close (press the sidebar Tools row again)
  TOOLS=$(find_pressable_by_child_text "Tools")
  native automate widget-action main-canvas "$TOOLS" press > /dev/null 2>&1
  if native automate assert --timeout-ms 3000 'role=textbox name="Message"' > /dev/null 2>&1; then
    pass "tools closes and shows chat"
  else
    fail "tools did not close"
  fi
}
```

Then wire `test_tools` into the `--all` list (the block near line 1102 calling `test_settings`) and add a `--tools)` branch in the usage switch (~line 1129). Also add `tools` to the header comment (line 4).

- [ ] **Step 3: Run the test to verify it fails**

Run: `./tests/frontend_suite.sh --tools` (backend must be running; the suite starts it via `start_backend` — if `start_backend` isn't wired for a single-mode run, run `--all` and expect the tools block to FAIL at "Tools pressable row not found").
Expected: FAIL — the sidebar Tools row has no `on_press` today.

- [ ] **Step 4: Implement the shell**

In `src/main.zig`:

1. Msg enum (near `.open_settings` ~line 273):

```zig
    open_tools,
    close_tools,
    tools_tab_builtin,
    tools_tab_connections,
```

2. `ToolsState` (near `SettingsState` ~line 354):

```zig
const ToolsSection = enum { builtin, connections };

const ToolsState = struct {
    visible: bool = false,
    section: ToolsSection = .builtin,
    loading: bool = false,
};
```

3. Model field: `tools: ToolsState = .{},`

4. `buildView` (~line 2599) — precedence over Settings:

```zig
    const right_panel: AppUi.Node = if (model.tools.visible)
        buildToolsPanel(ui, model)
    else if (model.settings.visible)
        buildSettingsPanel(ui, model)
    else
        buildChatPanel(ui, model);
```

5. Sidebar Tools row (~line 2781) — add `on_press` (same shape as the Settings row at ~2803):

```zig
    nav_nodes[0] = ui.row(.{
        .gap = 8,
        .padding = 8,
        .cross = .center,
        .on_press = .open_tools,
    }, .{
        ui.icon(.{ .style_tokens = .{ .foreground = .text_muted } }, "wrench"),
        ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, "Tools"),
    });
```

6. Handlers (in the `pub fn update` msg switch, mirror `.open_settings` ~line 1498):

```zig
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
```

7. `buildToolsPanel` (place it next to `buildSettingsPanel`, ~line 3113). Header mirrors the Settings header; tabs mirror the role-toggle row style (`ui.button` with `.variant = if active .primary else .ghost, .size = .sm`):

```zig
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
    children[child_count] = ui.row(.{ .gap = 6, .padding = 4, .cross = .center }, tab_nodes[0..2]);
    child_count += 1;

    children[child_count] = ui.scroll(.{ .grow = 1, .padding = 16 }, .{
        ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Loading…"),
    });
    child_count += 1;

    return ui.column(.{ .grow = 1, .style_tokens = .{ .background = .background } }, children[0..child_count]);
}
```

- [ ] **Step 5: Run the frontend test to verify it passes**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS (open, tabs, close). If `role=group` on the Tools row differs from Settings, match whatever `find_pressable_by_child_text` needs (it walks `parent=` links — no role requirement beyond pressable).

- [ ] **Step 6: Gates + commit**

Run: `uv run pytest`, `uv run ruff check src/`, `uv run mypy src/` (from repo root), `uv run native test` and `./tests/frontend_suite.sh --all` (from `native-sdk-experiment/`). Then:

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/src/app.native native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): Tools panel shell — sidebar toggle, tabs, precedence over settings"
```

---

### Task 2: Built-in tools list — fetch, render, search

**Files:**
- Modify: `src/main.zig` (ToolsState fields, effect key, Msg variants, handlers, `buildToolsPanel` builtin branch)
- Test: `tests/frontend_suite.sh` (`test_tools` extended)

**Interfaces:**
- Consumes: `GET http://127.0.0.1:8080/tools?user_id=native_sdk_chat&workspace_id=personal` → `{tools:[{name,description,category,annotations:{read_only,destructive,idempotent,open_world,title},enabled,scope,workspace_ids,source,parameters}], categories:{<cat>:{count,enabled}}}`. Note: annotations keys in the JSON are `readOnly`/`destructive`/`idempotent`/`openWorld`/`title` (camelCase — pydantic dump). `scope` is `"all"` or `"none"`; `enabled` mirrors it.

**Ruling from controller pre-flight scan:** the render loop must be INDEX-based (`for (0..tool_count) |tool_idx|` with `const t = tools[tool_idx]`) as shown below — Task 3 needs `tool_idx` for its `toggle_tool` button; value iteration would require rewriting this loop later.
- Produces: `const max_visible_tools_rows = 200;` `ToolRow` struct; `ToolsState.tools: [max_visible_tools_rows]ToolRow`, `.tool_count: usize`, `.search_text: []const u8`, `.tool_error: []const u8`; Msg variants `tools_loaded: native_sdk.EffectResponse`, `tools_search: canvas.TextInputEvent`; effect key `tools_key: u64`.

- [ ] **Step 1: Write the failing test**

In `test_tools()` (after the tabs assert, before the close-toggle):

```sh
  if native automate assert --timeout-ms 5000 'role=text name="time_get"' > /dev/null 2>&1; then
    pass "built-in tools list shows time_get"
  else
    fail "built-in tools list missing time_get"
  fi
```

(Requires the backend to be running with native tools registered — `start_backend` already does this for the settings tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `./tests/frontend_suite.sh --tools`
Expected: FAIL — the panel still shows "Loading…".

- [ ] **Step 3: Implement fetch + render**

1. Constants near `settings_key` (line 22):

```zig
const tools_key: u64 = 20;
const tools_toggle_key: u64 = 21;
```

2. Msg variants:

```zig
    tools_loaded: native_sdk.EffectResponse,
    tools_search: canvas.TextInputEvent,
```

3. Model state:

```zig
const ToolRow = struct {
    name: []const u8,
    description: []const u8,
    category: []const u8,
    enabled: bool,
    destructive: bool,
};
const max_visible_tools_rows = 200;
```

`ToolsState` gains: `tools: [max_visible_tools_rows]ToolRow = undefined`, `tool_count: usize = 0`, `search_text: []const u8 = ""`, `tool_error: []const u8 = ""`. Initialize empty strings in `initialModel` like other model strings (follow the Settings pattern for allocated strings: dupe on load, free on replace).

4. `.open_tools` handler: after setting `visible`, fire the fetch (copy the `.open_settings` pattern at ~1516):

```zig
            if (fx.fetch(.{
                .key = tools_key,
                .url = "http://127.0.0.1:8080/tools?user_id=native_sdk_chat&workspace_id=personal",
                .on_response = Effects.responseMsg(.tools_loaded),
            })) |err| {
                model.tools.tool_error = err;
            }
```

5. `.tools_loaded` handler — parse `{"tools":[...]}` (JSON parse pattern: follow the `settings_loaded` handler ~1559 exactly; each item reads `name`, `description`, `category`, `enabled`, `annotations.destructive`; skip items with no name; cap at `max_visible_tools_rows`; on parse failure set `.tool_error`).

6. `.tools_search` handler — dupe text into `model.tools.search_text` (mirror `.settings_search`).

7. `buildToolsPanel` — replace the placeholder scroll with the builtin branch:

```zig
    if (model.tools.section == .builtin) {
        // search field
        var list_nodes: [max_visible_tools_rows + 4]AppUi.Node = undefined;
        var list_node_count: usize = 0;
        list_nodes[list_node_count] = blk: {
            var field = ui.el(.textarea, .{
                .text = model.tools.search_text,
                .placeholder = "Search tools...",
                .on_input = AppUi.inputMsg(.tools_search),
                .height = 36,
                .style_tokens = .{ .background = .surface_subtle, .border_color = .border },
            }, .{});
            break :blk field;
        };
        list_node_count += 1;

        if (model.tools.tool_error.len > 0) {
            list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .destructive }, .wrap = true }, model.tools.tool_error);
            list_node_count += 1;
        } else if (model.tools.loading) {
            list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Loading...");
            list_node_count += 1;
        } else {
            var rendered: usize = 0;
            for (0..model.tools.tool_count) |tool_idx| {
                const t = model.tools.tools[tool_idx];
                if (model.tools.search_text.len > 0 and !containsIgnoreCase(t.name, model.tools.search_text) and
                    !containsIgnoreCase(t.description, model.tools.search_text)) continue;
                list_nodes[list_node_count] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
                    ui.text(.{ .size = .sm, .grow = 1 }, t.name),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted } }, t.category),
                });
                list_node_count += 1;
                rendered += 1;
                if (list_node_count >= max_visible_tools_rows) break;
            }
            if (rendered == 0) {
                list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "No tools match");
                list_node_count += 1;
            }
        }
        children[child_count] = ui.scroll(.{ .grow = 1, .padding = 16 }, .{ui.column(.{ .gap = 2 }, list_nodes[0..list_node_count])});
        child_count += 1;
    }
```

(`containsIgnoreCase` already exists at ~line 3118; reuse it.)

- [ ] **Step 4: Run the test**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS — "time_get" visible after opening the panel.

- [ ] **Step 5: Gates + commit**

Gates (see Task 1 Step 6). Commit:

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): built-in tools list with search"
```

---

### Task 3: Enable/disable built-in tools

**Files:**
- Modify: `src/main.zig` (Msg variant, handler, toggle button in the row)
- Modify: `tests/frontend_suite.sh` (`test_tools`)

**Interfaces:**
- Consumes: `PATCH http://127.0.0.1:8080/tools/{name}?user_id=native_sdk_chat&workspace_id=personal` with JSON body `{"scope": "all"}` (enable) / `{"scope": "none"}` (disable). Response `{enabled, scope, workspace_ids}`. The backend resets the user's agent loops — the client must NOT.
- Produces: Msg `tool_toggled: native_sdk.EffectResponse`; helper `fn toolToggleBody(enable: bool) []const u8` returning `{"scope": "all"}` or `{"scope": "none"}`; per-row button `variant = if (t.enabled) .primary else .ghost, size = .sm, on_press = .{ .toggle_tool = idx }`.

- [ ] **Step 1: Write the failing test**

After the "time_get visible" assert:

```sh
    # Find the toggle button for time_get: locate the row text, then the
    # nearest sibling button (snapshot: buttons are role=button; the row
    # is role=group). Use the settings-suite trick: find the button whose
    # name is empty inside the same parent as the time_get text widget.
    TG=$(find_pressable_by_child_text "time_get")
    TG_TOGGLE=$(find_sibling_button "$TG")
```

(Add a small `python3` helper `find_sibling_button <parent_id>` in `frontend_suite.sh` next to `find_pressable_by_child_text` that returns the first `role=button` widget whose parent is `<parent_id>`. **Ruling from controller pre-flight scan:** add this helper UNCONDITIONALLY in this task — Task 5 and Task 6's tests rely on it too, so a conditional "only if missing" would leave later tasks guessing.)

```sh
    if [ -z "$TG_TOGGLE" ]; then fail "toggle button for time_get not found"; return 1; fi
    native automate widget-action main-canvas "$TG_TOGGLE" press > /dev/null 2>&1
    # Wait a beat for the PATCH + response; then assert the row still renders (disabled state has no snapshot-visible change yet, so just assert no error text)
    sleep 2
    if native automate assert --timeout-ms 3000 'role=text name="No tools match"' > /dev/null 2>&1; then
      fail "tools panel errored after toggle"
    else
      pass "tool toggle does not break the panel"
    fi
    # Re-enable to leave state clean
    native automate widget-action main-canvas "$TG_TOGGLE" press > /dev/null 2>&1
    sleep 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `./tests/frontend_suite.sh --tools`
Expected: FAIL — no button exists in the row (nothing pressable).

- [ ] **Step 3: Implement**

1. Msg:

```zig
    toggle_tool: usize,
```

2. Handler (fires the PATCH with `fx.fetch`; uses `tool_toggle_key`; on `tool_toggled` response re-fetch the list (reuse the `tools_key` fetch) to keep server truth; on failure set `tool_error`).

3. Row rendering (inside the built-in branch, replace the two-text row):

```zig
                list_nodes[list_node_count] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
                    ui.text(.{ .size = .sm, .grow = 1 }, t.name),
                    ui.button(.{
                        .on_press = .{ .toggle_tool = tool_idx },
                        .variant = if (t.enabled) .primary else .ghost,
                        .size = .sm,
                        .semantics = .{ .label = if (t.enabled) "Disable" else "Enable" },
                    }, if (t.enabled) "On" else "Off"),
                });
```

- [ ] **Step 4: Run to verify it passes**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS. (Verify manually with curl that the PATCH actually flips: `curl -s -X PATCH 'http://127.0.0.1:8080/tools/time_get?user_id=native_sdk_chat&workspace_id=personal' -H 'Content-Type: application/json' -d '{"scope":"none"}'` → `{"enabled": false...}`; then back to `"all"`.)

- [ ] **Step 5: Gates + commit**

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): per-tool enable/disable via scope PATCH"
```

---

### Task 4: Connections tab — connector catalog + status + disconnect

**Files:**
- Modify: `src/main.zig` (state, Msg variants, fetch, render, disconnect handler)
- Modify: `tests/frontend_suite.sh`

**Interfaces:**
- Consumes: `GET http://127.0.0.1:8080/connectors/catalog?user_id=native_sdk_chat` → `[{name,display,icon,category,description,setup_guide_url,connected,auth_type,required_fields:[{name,label,placeholder,input_type,optional,help_text}]}]`. `DELETE http://127.0.0.1:8080/connectors/disconnect?service={name}&user_id=native_sdk_chat` → `{"status":"disconnected"}`.
- Produces: `const max_connector_rows = 128;` (per Global Constraints — binding; catalog ships 400+ entries, 8 would render a trivial slice) `ConnectorRow { name, display, description, category, auth_type, connected, required_fields: [max_required_fields]RequiredField, field_count: usize }`; `ToolsState.connectors: [max_connector_rows]ConnectorRow`, `.connector_count: usize`, `.connector_error: []const u8`, **`.connectors_loading: bool = false`**; Msg `connectors_loaded: EffectResponse`, `connector_disconnected: EffectResponse`; effect keys `connectors_key: u64 = 22`, `connector_disconnect_key: u64 = 23`.

**Ruling from controller pre-flight scan:** the builtin + connectors fetches both fire on panel open but must not share `ToolsState.loading` (whichever response lands first would clear the other's spinner). Use a separate `connectors_loading` flag: builtin branch shows Loading while `loading`, connections branch while `connectors_loading`.

- [ ] **Step 1: Write the failing test**

In `test_tools`, after switching to the Connections tab:

```sh
  # Switch to Connections tab
  CONN_TAB=$(find_pressable_by_child_text "Connections")
  native automate widget-action main-canvas "$CONN_TAB" press > /dev/null 2>&1
  # The catalog is dynamic (connectkit version pinned) — assert structure, not a specific service.
  if native automate assert --timeout-ms 5000 'role=text name="No connectors"' > /dev/null 2>&1; then
    fail "connections tab shows empty state instead of catalog"
  else
    pass "connections tab renders catalog rows"
  fi
  # Switch back for the rest of the test
  BUILTIN=$(find_pressable_by_child_text "Built-in")
  native automate widget-action main-canvas "$BUILTIN" press > /dev/null 2>&1
```

- [ ] **Step 2: Run to verify it fails**

Run: `./tests/frontend_suite.sh --tools`
Expected: FAIL — tab exists but section body is still the Built-in branch.

- [ ] **Step 3: Implement**

2. Fetch in `.open_tools` (second `fx.fetch` with `connectors_key`); set `model.tools.connectors_loading = true` there and `false` in the handler.
3. `.connectors_loaded` handler — parse each item into `ConnectorRow`; `required_fields` truncated to `max_required_fields = 4`. (ConnectorRow carries `auth_type` and `connected` verbatim.)
3. Render branch `else if (model.tools.section == .connections)`:

```zig
        var list_nodes: [max_connector_rows + 2]AppUi.Node = undefined;
        var list_node_count: usize = 0;
        if (model.tools.connector_error.len > 0) {
            list_nodes[list_node_count] = ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .destructive }, .wrap = true }, model.tools.connector_error);
            list_node_count += 1;
        } else if (model.tools.connectors_loading) {
            list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "Loading...");
            list_node_count += 1;
        } else if (model.tools.connector_count == 0) {
            list_nodes[list_node_count] = ui.text(.{ .style_tokens = .{ .foreground = .text_muted } }, "No connectors");
            list_node_count += 1;
        } else for (0..model.tools.connector_count) |connector_index| {
            const c = model.tools.connectors[connector_index];
            const status_text = if (c.connected) "Connected" else if (c.auth_type == "oauth2") "Not connected" else "Not connected";
            list_nodes[list_node_count] = ui.row(.{ .gap = 8, .padding = 8, .cross = .center }, .{
                ui.column(.{ .grow = 1, .gap = 2 }, .{
                    ui.text(.{ .size = .sm }, c.display),
                    ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = .text_muted }, .wrap = true }, c.description),
                }),
                ui.text(.{ .size = .sm, .style_tokens = .{ .foreground = if (c.connected) .success else .text_muted } }, status_text),
                if (c.connected)
                    ui.button(.{ .on_press = .{ .disconnect_connector = connector_index }, .variant = .ghost, .size = .sm }, "Disconnect")
                else
                    ui.button(.{ .disabled = true, .variant = .primary, .size = .sm }, "Connect"), // wired in Task 5/6 — disabled until then (ElementOptions.disabled verified)
            });
            list_node_count += 1;
        }
        children[child_count] = ui.scroll(.{ .grow = 1, .padding = 16 }, .{ui.column(.{ .gap = 2 }, list_nodes[0..list_node_count])});
        child_count += 1;
```

Add Msg variant `disconnect_connector: usize` and its handler: fires the DELETE with `connector_disconnect_key`, on response re-fetch the catalog. The Connect button stays **inert (no `on_press`)** in this task — Task 5 wires `.connect_connector => |idx|` for api_key connectors and Task 6 extends it for oauth2. Do NOT add a placeholder handler.

- [ ] **Step 4: Run to verify it passes**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS — connections renders rows (non-empty).

- [ ] **Step 5: Gates + commit**

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): connections tab with catalog + disconnect"
```

---

### Task 5: Connect flow — api_key connectors (inline credential form)

**Files:**
- Modify: `src/main.zig` (state: form fields, Msg variants, handler, render)
- Modify: `tests/frontend_suite.sh`
- Create: `tests/api/test_connectors_api.py` (backend contract regression lock)

**Interfaces:**
- Consumes: `POST http://127.0.0.1:8080/connectors/connect?service={name}&user_id=native_sdk_chat` body = `{field_name: value}` for each `required_fields` entry (skip optional). Success → `{"status":"connected"}` for api_key. The router calls `reset_user_sdk_loops` — client must not.
- Produces: `ToolsState.connecting: bool`, `.form_open: bool`, `.connect_service: []const u8` (connector being connected), `.field_buffers: [max_required_fields][]const u8`, `.connect_error: []const u8`; effect key `connector_connect_key: u64 = 25` (declared here, alongside the Task 4 keys); Msg variants `connect_connector: usize`, `submit_connector`, `close_form`, `connector_connected: EffectResponse`, and **four per-field input arms** `tools_field_0..tools_field_3: canvas.TextInputEvent` (one per `max_required_fields` slot — REQUIRED because `AppUi.inputMsg` only constructs `TextInputEvent` payload arms; a `usize` payload variant cannot be used with `inputMsg`).

- [ ] **Step 1: Backend contract tests (new file `tests/api/test_connectors_api.py`)** — these lock the contract the Zig parser depends on. **Ruling from controller pre-flight scan:** use the repo's existing `tests/api/conftest.py` `client` fixture (it builds the app via the same `app` fixture `test_tools_api.py` uses) instead of `TestClient(create_app())`, and point ConnectKit at a temp spec dir via `monkeypatch.setenv("CONNECTKIT_SPEC_DIR", ...)` (ConnectKit reads that env var at bridge construction):

```python
"""Connectors API contract tests — protect the Tools page data shapes."""

import pytest

from tests.api.conftest import client  # same app/client fixtures test_tools_api.py uses

FIXTURE_SPEC = """name: fixture-api
display: Fixture API
icon: fixture
category: test
description: Fixture connector
auth:
  type: api_key
  required_fields:
  - name: api_key
    label: API Key
    placeholder: sk-...
    input_type: password
    optional: false
"""


@pytest.fixture
def fixture_spec_dir(tmp_path, monkeypatch):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "fixture-api.yaml").write_text(FIXTURE_SPEC)
    monkeypatch.setenv("CONNECTKIT_SPEC_DIR", str(spec_dir))
    return spec_dir


def test_catalog_lists_fixture_with_connected_false(client, fixture_spec_dir):
    r = client.get("/connectors/catalog", params={"user_id": "tester"})
    assert r.status_code == 200
    item = next(c for c in r.json() if c["name"] == "fixture-api")
    assert item["auth_type"] == "api_key"
    assert item["connected"] is False


def test_api_key_connect_and_disconnect(client, fixture_spec_dir):
    r = client.post(
        "/connectors/connect",
        params={"service": "fixture-api", "user_id": "tester"},
        json={"api_key": "sk-test"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "connected"
    r = client.get("/connectors/catalog", params={"user_id": "tester"})
    assert next(c for c in r.json() if c["name"] == "fixture-api")["connected"] is True
    r = client.delete(
        "/connectors/disconnect", params={"service": "fixture-api", "user_id": "tester"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disconnected"


def test_connect_missing_credentials_400(client, fixture_spec_dir):
    r = client.post(
        "/connectors/connect", params={"service": "fixture-api", "user_id": "tester"}, json={}
    )
    assert r.status_code == 400
```

(If the conftest `client` fixture is not importable by that name, copy the `app`/`client` fixture pattern from `tests/api/conftest.py` into the test module — match whatever `test_tools_api.py` does. The vault writes to `data/users/tester/connectkit` under the repo — gitignored scratch, acceptable.)

- [ ] **Step 2: Run to verify the backend tests pass (they should — no backend change expected)**

Run: `uv run pytest tests/api/test_connectors_api.py -v`
Expected: PASS. If a test fails because the vault path is outside tmp (it writes to `data/users/tester/connectkit`), that's acceptable — the fixture spec env var is the key isolation; leave a `@pytest.mark.cleanup` note if needed. (If the contract is broken, STOP and fix the backend first — the Zig parser depends on it.)

- [ ] **Step 3: Write the failing frontend test**

In `test_tools` (connections branch), pick an api_key connector from the catalog and drive the form (uses the `native automate` textbox + click pattern from the suite):

```sh
  # Connect flow: pick the first api_key connector (dynamic catalog)
  API_NAME=$(curl -s 'http://127.0.0.1:8080/connectors/catalog?user_id=native_sdk_chat' | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((c['name'] for c in d if c['auth_type']=='api_key' and not c['connected']), ''))")
  if [ -n "$API_NAME" ]; then
    ROW=$(find_pressable_by_child_text "$API_NAME")
    BTN=$(find_sibling_button "$ROW")
    native automate widget-action main-canvas "$BTN" press > /dev/null 2>&1
    if native automate assert --timeout-ms 3000 'role=text name="API Key"' > /dev/null 2>&1; then
      pass "api_key connector opens credential form"
    else
      fail "api_key credential form missing"
    fi
  else
    skip "no api_key connector in catalog"
  fi
```

- [ ] **Step 4: Run to verify it fails**

Run: `./tests/frontend_suite.sh --tools`
Expected: FAIL — no credential form.

- [ ] **Step 5: Implement the api_key form flow**

1. `ToolsState` additions: `connecting: bool = false`, `form_error: []const u8 = ""`, `field_buffers: [max_required_fields][]const u8 = .{ "", "", "", "" }`, `focused_field: usize = 0`, plus remember the connector being connected (`connect_service: []const u8 = ""`).
3. `.connect_connector => |idx|` handler (only for `auth_type == "api_key"`): set `form_open = true`, `connect_service = connector name`, reset `field_buffers`; on `.submit_connector` (new Msg) build the JSON body from non-empty buffers via `std.json.stringify` with a fixed map of the connector's required field names (field name comes from `ConnectorRow.required_fields[i].name`), `fx.fetch` with `connector_connect_key`; on `.connector_connected` (response): refetch catalog, `form_open = false`, clear error; on error: set `connect_error` and render it red. When wiring the handler, replace Task 4's disabled Connect button with `.on_press = .{ .connect_connector = connector_index }` (drop `.disabled`).
4. Render: when `form_open`, replace the connections scroll body with a form card: one `.textarea` per required field (labels from `required_fields[i].label`), each with `.on_input = AppUi.inputMsg(.tools_field_0)` … `.tools_field_3` matching the field index, a submit button (`.variant = .primary`, "Connect") with `.on_press = .submit_connector`, and a Cancel (`.on_press = .close_form`). Show `connect_error` red when non-empty.
5. `.tools_field_0..3` handlers: dupe text into `field_buffers[0..3]` respectively.

- [ ] **Step 6: Run the frontend test to verify it passes**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS (or SKIP if no api_key connector — acceptable, the backend contract test covers it).

- [ ] **Step 7: Gates + commit**

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh tests/api/test_connectors_api.py
git commit -m "feat(tools): api_key connector connect flow with credential form"
```

---

### Task 6: OAuth connector flow — authorize in system browser + poll

**Files:**
- Modify: `src/main.zig` (handler, poll timer, render state)
- Modify: `tests/frontend_suite.sh`

**Interfaces:**
- Consumes: for OAuth services, `POST /connectors/connect` with any required credential fields (usually none) → `{"status":"configured","next_step":"/auth/login?service=...&user_id=..."}`. App then spawns `open "http://127.0.0.1:8080/auth/login?service={name}&user_id=native_sdk_chat"` (macOS). Poll `GET /connectors/catalog?user_id=native_sdk_chat` every 2s while `connecting`; when the item's `connected` flips true, stop.
- Produces: Msg `oauth_open`, `oauth_poll: native_sdk.EffectTimer` (repeat timer), `auth_polled: EffectResponse`; effect key `auth_poll_key: u64 = 24`; helper `fn openSystemBrowser(url: []const u8) !void` spawning `open` via `std.process.Child.run`.

- [ ] **Step 1: Write the failing test**

```sh
  # OAuth path: pick the first oauth2 connector, press Connect,
  # assert the "Authorizing" waiting state appears (no browser opens in CI).
  OAUTH_NAME=$(curl -s 'http://127.0.0.1:8080/connectors/catalog?user_id=native_sdk_chat' | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((c['name'] for c in d if c['auth_type']=='oauth2' and not c['connected']), ''))")
  if [ -n "$OAUTH_NAME" ]; then
    ROW=$(find_pressable_by_child_text "$OAUTH_NAME")
    BTN=$(find_sibling_button "$ROW")
    native automate widget-action main-canvas "$BTN" press > /dev/null 2>&1
    if native automate assert --timeout-ms 3000 'role=text name="Authorize in your browser"' > /dev/null 2>&1; then
      pass "oauth connect shows browser-authorize state"
    else
      fail "oauth authorize state missing"
    fi
  else
    skip "no oauth2 connector in catalog"
  fi
```

- [ ] **Step 2: Run to verify it fails**

Run: `./tests/frontend_suite.sh --tools`
Expected: FAIL — no authorize state (Task 5 handler currently only handles api_key).

- [ ] **Step 3: Implement the OAuth flow**

1. In `.connect_connector`: if `auth_type == "oauth2"` (replace the `.disabled = true` button from Task 4 with `.on_press = .{ .connect_connector = connector_index }` for ALL connector types, not just api_key):
   - if any non-optional required fields exist, open the same form as Task 5 but with a "Connect" button that posts + then runs the browser step (reuse `submit_connector` flow);
   - else directly: `set .connecting = true`, fire `POST /connectors/connect` with `{}` body (ignore 4xx — some services need creds; surface the error from `next_step` or the response), then call `openSystemBrowser`.
2. `openSystemBrowser` helper (top-level, near `containsIgnoreCase`):

```zig
fn openSystemBrowser(url: []const u8) !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const result = try std.process.Child.run(.{
        .allocator = arena.allocator(),
        .argv = &.{ "open", url },
    });
    if (result.term != .Exited or result.term.Exited != 0) return error.OpenFailed;
}
```

3. Start the poll: `fx.startTimer(.{ .key = auth_poll_key, .interval_ms = 2000, .mode = .repeating, .on_fire = Effects.timerMsg(.auth_poll) })` (TimerMode enum is `{ one_shot, repeating }` — verified in `@native-sdk/cli/src/runtime/effects.zig:757`; the existing app code only uses `.one_shot`).
4. `.auth_poll` → `fx.fetch` catalog again (reuse `connectors_key` but with a flag `polling = true` so `.connectors_loaded` doesn't reset the dialog); in `.connectors_loaded`, when `polling`: find the service; if `connected`, set `connecting = false`, `polling = false`, stop the timer via `fx.cancelTimer(auth_poll_key)` (verified API — `effects.zig:10163`), refresh the catalog display.
5. Renderer: when `.connecting` is true, replace the scroll body with a centered waiting state: `ui.text("Authorizing…")` + `ui.text("Authorize in your browser")` + a Cancel button (`.cancel_connect` → stop timer, `connecting = false`).

- [ ] **Step 4: Run the frontend test to verify it passes**

Run: `./tests/frontend_suite.sh --tools`
Expected: PASS (or SKIP if the catalog has no oauth2 connector — the skip line is an accepted outcome; the task's real verification is the waiting state).

Manual verification (not automated — needs real SaaS): with a dev client_id configured in the vault, run `uv run assistant http`, click Connect on a service with real creds, complete the browser flow, watch the panel flip to "Connected" within ~2s of the callback.

- [ ] **Step 5: Gates + commit**

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): oauth connect — system browser + catalog polling"
```

---

### Task 7: Polish — entrance motion, reduced-motion, key handling, docs

**Files:**
- Modify: `src/main.zig` (entrance animation mirror, escape-key close)
- Modify: `native-sdk-experiment/tests/frontend_suite.sh` (reduced-motion + escape assertions)
- Create: `docs/superpowers/plans/2026-08-21-tools-page-phase-a-connectors-builtin.md` (this plan is committed alongside)

**Interfaces:**
- Consumes: nothing new — reuses `model.settings.reduced_motion`, the entrance animation field/step (`settings_entrance` at line 342 and its update loop ~1236 — add a parallel `tools_entrance: f32 = 1` field + the same animation tick in `update`), the escape-key handler in `keyPressed` (mirror `close_settings`).
- Produces: final polish — no new Msg.

- [ ] **Step 1: Entrance animation + reduced motion**

Mirror the Settings pattern exactly:
- Add `tools_entrance: f32 = 1` to Model; on `.open_tools`: `model.tools_entrance = if (model.settings.reduced_motion) 1 else 0`; in `update`'s tick handler, advance `tools_entrance` toward 1 with the same `entrance_step` constant only when `tools.visible` and not reduced-motion; multiply the panel's opacity/translate in `buildToolsPanel` via the same code the settings panel uses (search `settings_entrance` usages and copy).

- [ ] **Step 2: Escape closes the panel**

In the key handler (where `close_settings` fires on Escape): add `model.tools.visible = false` when Escape is pressed and tools is visible (and settings isn't).

- [ ] **Step 3: Tests**

Extend `test_tools`:

```sh
  # Escape closes
  native automate widget-key main-canvas Escape > /dev/null 2>&1
  if native automate assert --timeout-ms 3000 'role=textbox name="Message"' > /dev/null 2>&1; then
    pass "escape closes tools panel"
  else
    fail "escape did not close tools panel"
  fi
```

Also assert reduced-motion doesn't break: with `NATIVE_SDK_REDUCED_MOTION=1` the panel opens without animating (check for absence of crash only — the settings suite does the same).

- [ ] **Step 4: Gates + full suite**

Run all five gates from the repo root + `native-sdk-experiment/`:

```bash
uv run pytest
uv run ruff check src/
uv run mypy src/
cd native-sdk-experiment && zig build test && ./tests/frontend_suite.sh --all
```

Expected: 87+ Zig tests, 37+ frontend tests (new: tools open/close/tabs, time_get list, toggle, connections, api-key form, oauth authorize, escape), all green.

- [ ] **Step 5: Commit**

```bash
git add native-sdk-experiment/src/main.zig native-sdk-experiment/tests/frontend_suite.sh
git commit -m "feat(tools): entrance motion, reduced-motion, escape close"
```

---

## Out of scope (future)

- **Script-built tools** (Phase A.2): script registry + sandboxing + tool registration endpoint — needs a real backend surface; do NOT start it while the Ralph loop is churning on `src/sdk/loop.py`.
- Skills page and Subagents page (Phases B/C in the brainstorm): need their own plans.
- The `time_get` duplicate-call bug: the Ralph loop (`tasks/prd-fix-repeated-tool-call-loop.md`) owns it; this plan never touches `src/sdk/loop.py`, `src/storage/messages.py`, `src/sdk/run_service.py`.

## Risk notes

- **`PATCH /tools/{name}` resets the user's agent loops on the backend** — toggling tools mid-chat is by design; note it in the tools section copy ("changes apply to new conversations").
- **The OAuth browser step cannot be E2E-tested headlessly** — covered by the contract tests + the "Authorizing…" state test; the manual verification script is in Task 6.
- **`app.native` is a markup twin** — keep it in sync cosmetically; `main.zig` `buildView` is the runtime truth.
- **`find_pressable_by_child_text` returns the first match** — sidebar "Tools" is unique; the tab buttons ("Built-in"/"Connections") are matched by child text too; if ambiguity arises, scope the helper with an extra `parent=` filter.
