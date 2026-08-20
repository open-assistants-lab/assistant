# Frontend Automated Test Cases

Automated tests for the Native SDK chat app, run via `native automate`.

## Test Files

| File | Purpose | Tests |
|------|---------|-------|
| `tests/frontend_smoke.sh` | Quick smoke test (4 tests, ~30s) | Basic send/receive, sidebar, new chat, theme toggle |
| `tests/frontend_suite.sh` | Full suite (16 sections, 51 tests, ~8min) | All smoke tests + advanced scenarios |
| `tests/virtuallist_stress.sh` | Virtual list perf stress test (~2min) | 10k-message transcript: widget_nodes bounded while scrolling full extent, frame p90 stages under 60fps budget, no widget budget errors |

## Running

```bash
# Smoke test (quick verification)
env -u AGENT VERIFICATION_ENABLED=false bash tests/frontend_smoke.sh

# Full suite
env -u AGENT VERIFICATION_ENABLED=false bash tests/frontend_suite.sh --all

# Individual sections
bash tests/frontend_suite.sh --textarea    # Textarea behavior
bash tests/frontend_suite.sh --midstream   # Model switch mid-stream
bash tests/frontend_suite.sh --keyboard     # Keyboard interaction
bash tests/frontend_suite.sh --chats        # Multi-chat management
# (see --help for all options)
```

## Test Cases

### 1. Record/Replay: Chat Flow (`test_record_replay`)

**Scope:** Core send-message-and-receive-response flow.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 1.1 | Send message and receive response | Type "Reply exactly: ok" via `widget-key`, click Send button. Assert `role=text name="ok"` appears within 60s. |
| 1.2 | Sidebar shows chat with title | Assert `role=listitem name="Reply exactly: ok"` appears in sidebar within 5s. |
| 1.3 | New chat shows empty state | Click "New chat" button. Assert `role=text name="How can I help"` appears within 5s. |

**Key automation pattern:** Use `widget-key` (not `set_text`) to type text — `widget-key` dispatches `input_changed` which updates `draft_text`. Use `widget-click` (not `widget-action press`) for buttons — `widget-click` reliably dispatches `on_press`.

### 2. Screenshot Diffing: Visual Regression (`test_screenshot`)

**Scope:** Visual consistency between runs.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 2.1 | Dark mode screenshot matches baseline | Capture screenshot in dark mode, compare byte-for-byte against `tests/baselines/`. |
| 2.2 | Light mode screenshot matches baseline | Toggle theme, capture screenshot, compare against baseline. |

### 3. Keyboard Interaction (`test_keyboard`)

**Scope:** Keyboard event handling.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 3.1 | Enter key sends message | Focus textbox, type text, press Enter. Assert response appears. |
| 3.2 | Escape key handled without crash | Press Escape. Assert app still running (no crash). |
| 3.3 | Tab key handled without crash | Press Tab. Assert app still running (no crash). |

### 4. Bridge Testing: WebView Surface (`test_bridge`)

**Scope:** WebView bridge channel is operational.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 4.1 | Bridge channel operational | Send a bridge command. Assert delivery succeeded (even if permission denied). |
| 4.2 | No unexpected WebView surfaces | Assert no unexpected WebView surfaces appear. |

### 5. Chat Management: Multi-chat (`test_chats`)

**Scope:** Creating, switching between, and managing multiple chat sessions.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 5.1 | Chat 1 response received | Create chat, send "Reply exactly: alpha", assert "alpha" response. |
| 5.2 | Chat 2 response received | New chat, send "Reply exactly: beta", assert "beta" response. |
| 5.3 | Third chat shows empty state | New chat, assert "How can I help?" empty state. |

### 6. Suggestion Buttons (`test_suggestions`)

**Scope:** Empty-state suggestion buttons fill the draft.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 6.1 | "Triage my inbox" button fills draft | Click button, assert textbox contains the prompt text. |
| 6.2 | "Draft a weekly summary" button fills draft | Click button, assert textbox contains the prompt text. |
| 6.3 | "Find contacts in marketing" button fills draft | Click button, assert textbox contains the prompt text. |

### 7. Settings Panel (`test_settings`)

**Scope:** Settings panel open, tab navigation, close.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 7.1 | Settings panel opens | Click Settings row, assert `role=button name="Models"` appears. |
| 7.2 | Tab navigation works | Navigate Models → General, assert `role=text name="Appearance"` renders. |
| 7.3 | Settings panel closes | Press Settings again, assert Models tab gone. |

### 8. Tools Section in Settings (`test_tools`)

**Scope:** The Tools page now lives inside Settings (Models / General / Tools sidebar). No sidebar Tools row exists anymore.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 8.1 | Tools section opens | Settings → click `Tools` section button, assert `Built-in` + `Connections` sub-tabs. |
| 8.2 | Built-in list renders | Assert `role=text name="time_get"` within 5s. |
| 8.3 | Tool toggle flips backend scope | Press `time_get`'s On/Off, curl `/tools/time_get` → `enabled=false`; press again → `true` (state left clean). |
| 8.4 | Connections catalog renders | Assert `Connected`/`Not connected` status text appears (fails only on empty/error states). |
| 8.5 | api_key credential form opens | First non-connected api_key connector → Connect → assert its first required-field label renders; Cancel. |
| 8.6 | OAuth authorize state | First no-creds oauth2 connector → Connect → assert `Authorize in your browser`; Cancel (stops poll). |
| 8.7 | Escape closes settings | `widget-key Escape` → chat textbox visible. |
| 8.8 | Reduced-motion path | General → Reduced motion On → Tools renders; Escape closes. |

### 8b. Credential Form Node Budget (`test_connect_form_4field`)

**Scope:** Regression for the OOB array write (Critical #1, final review): a 4-required-field connector must render its form without crashing.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 8b.1 | 4-field fixture form renders | Backend started with `CONNECTKIT_SPEC_DIR` fixture; Settings → Tools → Connections → fixture row Connect → all four labels (`Host`, `API Key`, `Client ID`, `Secret`) render. |

Run standalone: `bash tests/frontend_suite.sh --connectform`

### 8. Streaming Cancel (`test_cancel`)

**Scope:** Cancel a streaming response mid-flight.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 8.1 | Cancel mid-stream restores Send button | Send a message, press Stop while streaming, assert Send button reappears. |

> Note: `test_cancel`'s echo header says `=== 8.` like `test_tools` — cosmetic duplication, known deferred minor.

### 9. Chat Search (`test_search`)

**Scope:** Sidebar search filters sessions by title.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 9.1 | Search shows matching chat | Type search query, assert matching listitem visible. |
| 9.2 | Search hides non-matching chat | Assert non-matching listitem not visible. |
| 9.3 | Clear search restores all chats | Clear search box, assert both chats visible. |

> **Known flake:** 9.1/9.3 occasionally fail on a cold build (first `--all` run after a rebuild). Re-running passes — verified pre-existing at base, unrelated to the tools work.

### 10. Model Cycling (`test_model`)

**Scope:** Cycle through available models.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 10.1 | Model cycle changes label | Press model button, assert label changed to a different model. |

### 11. Sidebar Resize (`test_sidebar`)

**Scope:** Drag the split divider to resize sidebar.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 11.1 | Sidebar resize without crash | Drag divider, assert app still running. |

### 12. Unread Dot (`test_unread`)

**Scope:** Unread indicator on non-active chat sessions.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 12.1 | Non-active chat shows unread dot | Send message to chat 1, switch to chat 2, assert unread indicator on chat 1. |
| 12.2 | Switching to chat clears unread | Switch to chat 1, assert unread indicator removed. |

### 13. Textarea Behavior (`test_textarea`)

**Scope:** Enter key, line breaks, textarea height auto-growth.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 13.1 | Single-line textarea height is ~36px | Type "hello", assert textbox height < 40px. |
| 13.2 | Enter adds a newline, textarea grows | Press Return, assert textbox height > 40px (grew to ~48px). |
| 13.3 | Multiple newlines grow textarea proportionally | Press Return again, type "line3", assert height > 60px (3 lines). |
| 13.4 | After send, textarea height resets | Click Send, assert textbox height < 40px (back to ~36px). |

**Note on Enter vs Shift+Enter:** The Native SDK's `TextInputEvent` does not carry modifier state, so the app cannot distinguish Enter from Shift+Enter. Both insert `\n`. The app uses `on_submit` (Cmd+Enter) for sending. Plain Enter/Shift+Enter both add a newline. The Send button or Cmd+Enter sends the message.

### 14. Model Switch Mid-Stream (`test_model_midstream`)

**Scope:** Model button state during and after streaming.

| # | Test | Expected Behavior |
|---|------|-------------------|
| 14.1 | Model button is pressable before streaming | Assert `role=button` with model name exists before sending. |
| 14.2 | Model button disabled (text only) during streaming | Send a message, assert model widget is `role=text` (not `role=button`) while streaming. |
| 14.3 | Model button re-enabled after streaming completes | Wait for response, assert model widget is `role=button` again. |

**Rationale:** The model is frozen per-request (sent in the JSON body). Switching mid-stream would only affect the next message. The UI prevents confusion by rendering the model as plain text (non-clickable) during streaming, then re-enabling it as a button when the response completes.

## Known Issues

1. **`set_text` automation does not reliably update `draft_text`** — Use `widget-key` to type text (dispatches `input_changed`).
2. **`widget-action press` does not reliably dispatch `on_press`** — Use `widget-click` for button presses.
3. **`ui.row` with `on_press` does not dispatch press events** — Use `ui.button` for pressable elements. (SDK limitation)
4. **`test_search` cold-build flake** — 9.1/9.3 fail on the first `--all` run after a rebuild; a re-run passes. Pre-existing and unrelated to the tools/settings work.
5. **Snapshot `name` is the semantics label** — For buttons with a `.semantics.label` (e.g. tool toggles `Enable`/`Disable`), `role=button name=...` matches the label, not the button text. Assert backend state (curl) for behavior, not the label text.