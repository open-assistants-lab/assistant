# Native SDK Early Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gain hands-on experience with Vercel Native SDK to evaluate replacing the Flutter desktop client.

**Architecture:** Progressive — install toolchain, build a standalone mini-app to learn the SDK, then connect to the existing EA Python backend via REST/WebSocket, culminating in an evaluation report.

**Tech Stack:** Zig, Native SDK (native-rendered), Python Assistant backend (existing)

---

### Task 1: Install CLI & Scaffold Default App

**Files:**
- Create: `native-sdk-experiment/` (new directory outside EA repo)
- Test: run the scaffolded counter app

- [ ] **Step 1: Install CLI and scaffold**

```bash
npm install -g @native-sdk/cli
native init native-sdk-experiment
cd native-sdk-experiment
```

Expected: `native init` creates `app.zon`, `src/main.zig`, `src/app.native`, `build.zig`, `build.zig.zon`.

- [ ] **Step 2: Inspect generated files**

Read `app.zon`, `src/main.zig`, `src/app.native`, `build.zig` to understand the project structure.

- [ ] **Step 3: Build and run the counter app**

```bash
zig build run
```

Expected: A native window opens with a counter and increment button.

- [ ] **Step 4: Try dev loop with hot reload**

Make a small change to `src/app.native` (e.g., change button text), observe hot reload.

- [ ] **Step 5: Commit findings**

```bash
git init && git add -A && git commit -m "feat: scaffold Native SDK default app"
```

---

### Task 2: Build Standalone Mini-App (Todo List)

**Files:**
- Create: `native-sdk-experiment/src/app.native` (rewrite)
- Create: `native-sdk-experiment/src/main.zig` (rewrite)
- Test: build and run

- [ ] **Step 1: Design the todo app model**

Model: list of items with `id`, `text`, `done` fields. Msg: `add`, `toggle`, `delete`, `update_text`.

- [ ] **Step 2: Write the markup view**

```html
<!-- src/app.native -->
<column padding="16" gap="8">
  <text size="heading">Todos</text>
  <row gap="4">
    <input value="{input_text}" on-change="input_changed" placeholder="Add a todo..." grow="1" />
    <button on-press="add">+</button>
  </row>
  <for each="items" key="id" as="item">
    <row gap="8" align="center">
      <checkbox checked="{item.done}" on-toggle="toggle:{item.id}" />
      <text grow="1" decoration="{item.done | if-done}">{item.text}</text>
      <button on-press="delete:{item.id}">✕</button>
    </row>
  </for>
</column>
```

- [ ] **Step 3: Write the Zig logic**

```zig
// src/main.zig — Model, Msg, update, view
```

- [ ] **Step 4: Build and run**

```bash
zig build run
```

Expected: A native window with a working todo list — add, toggle, delete items.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: todo list mini-app"
```

---

### Task 3: Connect to EA Backend (Status Panel)

**Files:**
- Create: `native-sdk-experiment/src/app.native` (add status panel)
- Create: `native-sdk-experiment/src/main.zig` (add HTTP fetch)
- External: Assistant backend running at `127.0.0.1:8080`

- [ ] **Step 1: Start Assistant backend**

```bash
cd /Users/eddy/Developer/Python/assistant
uv run assistant http &
```

- [ ] **Step 2: Add health check fetch to the app**

Use `fx.fetch` to call `GET http://127.0.0.1:8080/health` and display status.

- [ ] **Step 3: Build and verify**

```bash
zig build run
```

Expected: App shows "Backend: connected" or similar status.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: Assistant backend status panel"
```

---

### Task 4: Build Chat Shell (REST)

**Files:**
- Modify: `native-sdk-experiment/src/app.native` (add chat UI)
- Modify: `native-sdk-experiment/src/main.zig` (add POST /message)

- [ ] **Step 1: Add chat input and message list to the view**

- [ ] **Step 2: Wire POST /message via fx.fetch**

Send user message, display response.

- [ ] **Step 3: Build and test**

```bash
zig build run
```

Expected: Type a message, see AI response from Assistant backend.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: chat shell with REST backend"
```

---

### Task 5: Evaluation Report

**Files:**
- Create: `docs/superpowers/specs/2026-07-11-native-sdk-evaluation.md`

- [ ] **Step 1: Compare Native SDK vs Flutter for each EA feature**

| Feature | Flutter | Native SDK | Gap? |
|---------|---------|------------|------|
| Chat streaming | WS | ? | |
| Tool events | WS | ? | |
| HITL approve/reject | WS | ? | |
| Canvas/editor surfaces | WebView | WebView | |
| Settings panel | Forms | Native UI | |
| Workspace panel | Tabs, lists | Native UI | |
| Tools panel | Tree, switches | Native UI | |
| Skills panel | List | Native UI | |
| Subagents panel | List | Native UI | |
| Theming | Riverpod | Tokens | |

- [ ] **Step 2: Identify gaps and showstoppers**

- [ ] **Step 3: Migration effort estimate**

- [ ] **Step 4: Go/no-go recommendation**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: Native SDK evaluation report"
```
