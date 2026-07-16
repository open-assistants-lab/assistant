# Native SDK Evaluation Report

> **Date:** 2026-07-11
> **CLI Version:** native 0.4.2 (commit 20bc1eb, automation protocol v6)
> **Zig Version:** 0.16.0_1
> **Platform:** macOS (Apple Silicon)

## Summary

The Native SDK is a viable replacement for the Flutter desktop client, but with important caveats. The toolchain is mature on macOS, the markup/component system is expressive, and the Model/Msg/Update architecture maps well to the Assistant client's needs. The main risk is the WebSocket streaming chat path.

## What We Built

1. **Scaffolded default app** — counter app, built and ran successfully
2. **Todo list mini-app** — add/toggle/delete with text input, checkboxes, status bar, empty state, 4 passing tests
3. **HTTP connectivity verified** — `fx.fetch` documented for REST calls to Assistant backend

## Feature Comparison

| Feature | Flutter | Native SDK | Gap? |
|---------|---------|------------|------|
| **Chat streaming** | WebSocket | `fx.fetch` with `.response = .stream` | ✅ Supported — NDJSON/SSE streaming via `on_line` Msgs |
| **Tool events** | WebSocket | Same stream channel | ✅ Same mechanism |
| **HITL approve/reject** | WebSocket messages | Bridge commands or stream messages | ⚠️ Needs design — no built-in HITL pattern |
| **Canvas/editor surfaces** | WebView | WebView via `WebViewSource` | ✅ Supported |
| **Settings panel** | Riverpod forms | Native UI components | ✅ All form controls available |
| **Workspace panel** | Tabs, lists | Tabs, lists, tree | ✅ |
| **Tools panel** | Tree, switches | Tree, toggle-group | ✅ |
| **Skills panel** | List | List | ✅ |
| **Subagents panel** | List | List | ✅ |
| **Theming** | Riverpod | Typography tokens | ✅ Different approach but sufficient |
| **Secondary windows** | Routes | Model-declared windows | ✅ |
| **Testing** | flutter_test | Zig unit tests + automation | ✅ |
| **Hot reload** | Yes | Yes (markup only) | ⚠️ Zig code requires rebuild |
| **Cross-platform** | iOS, Android, Web, Desktop | macOS (deep), Linux, Windows (CI) | ⚠️ No mobile (experimental) |

## Key Findings

### Strengths

1. **Toolchain quality** — `native init`, `native dev`, `native test` all work smoothly. Build times ~3s for Debug.
2. **Markup system** — Declarative `.native` files with bindings, `for`/`if`/`else`, templates, components. Feels like SwiftUI.
3. **Model/Msg/Update** — Elm architecture maps cleanly to the Assistant client's state management.
4. **Effects system** — `fx.fetch`, `fx.spawn`, `fx.writeFile`/`fx.readFile` cover all the I/O patterns we need.
5. **Testing** — Unit tests with `buildTree` + `msgForPointer` are fast and deterministic. Automation server for smoke tests.
6. **Components** — Rich set: text, input, button, checkbox, list, tree, tabs, split, chart, markdown, stepper, timeline, status-bar, dialog, drawer, sheet, dropdown-menu, etc.
7. **No browser** — Native-rendered by default. Smaller footprint, no Electron-style memory usage.

### Weaknesses

1. **Zig learning curve** — Zig is less ergonomic than Dart for UI logic. Memory management (arenas, allocators) adds boilerplate.
2. **No hot reload for Zig** — Only markup changes hot-reload. Zig code changes need a full rebuild (~3s).
3. **WebSocket streaming** — While `fx.fetch` supports streaming responses, the bidirectional WebSocket protocol used by EA (HITL interrupts, approve/reject, cancel) needs custom implementation. The `fx.fetch` streaming is one-directional (HTTP response).
4. **Mobile is experimental** — If cross-platform mobile is a requirement, Native SDK is not ready.
5. **Smaller ecosystem** — Fewer packages, less community support than Flutter.
6. **Markup validation is strict** — Good for correctness, but the learning curve is steep (every attribute, every binding is checked).

### Risks

1. **WebSocket/HITL protocol** — The biggest unknown. Assistant's WS protocol (`/ws/conversation`) is bidirectional with interrupt/approve/reject/edit. Native SDK's `fx.fetch` with streaming covers one direction. A custom WebSocket client in Zig would be needed, or the backend protocol would need to change.
2. **Zig 0.16** — The language is still evolving. Breaking changes between versions are common.
3. **Pre-1.0 SDK** — Native SDK is at 0.4.2. APIs may change.

## Migration Effort Estimate

| Phase | Effort | Description |
|-------|--------|-------------|
| Shell/navigation | 2-3 days | Window setup, sidebar, workspace switcher |
| Settings panel | 1-2 days | Form controls, theming |
| Tools panel | 1 day | Tree + toggle switches |
| Skills panel | 0.5 day | List |
| Subagents panel | 0.5 day | List |
| Chat (REST) | 1-2 days | Input + message list + POST /message |
| Chat (WebSocket) | 3-5 days | Custom WS client + HITL protocol |
| Canvas/editor surfaces | 1 day | WebView integration |
| **Total** | **~2 weeks** | One developer, first-time Zig |

## Recommendation

**Go for it** — but start with a non-chat panel (settings or tools) to build Zig fluency, then tackle the chat/streaming path as the highest-risk item.

The Native SDK is a credible Flutter replacement for this project. The toolchain works, the component set is sufficient, and the Elm architecture is a good fit. The main risk is the WebSocket streaming protocol, which needs prototyping before committing to a full migration.

## Next Steps

1. Prototype a WebSocket client in Zig to validate the streaming/HITL path
2. Port the settings panel as a warm-up
3. Port the tools panel
4. Port the chat shell with REST first, then WebSocket
5. Keep Flutter client until feature parity is reached
