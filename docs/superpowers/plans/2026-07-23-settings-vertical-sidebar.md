# Settings Vertical Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Settings subnavigation from horizontal tabs to a vertical left sidebar and rename `Providers & Models` to `Models`.

**Architecture:** Keep the existing `SettingsSection` state and messages. Only change `buildSettingsPanel` layout so the sidebar and active content render in a two-column row below the Settings header.

**Tech Stack:** Native SDK Zig UI in `native-sdk-experiment/src/main.zig`, tests in `native-sdk-experiment/src/tests.zig`.

---

### Task 1: Update Settings Sidebar Layout

**Files:**
- Modify: `native-sdk-experiment/src/tests.zig`
- Modify: `native-sdk-experiment/src/main.zig`

- [ ] **Step 1: Update Native tests**

Change Settings tests to expect the first submenu button text to be `Models`, while `General` remains unchanged.

- [ ] **Step 2: Run Native tests to verify they fail**

Run: `native test`

Expected: tests fail because the app still renders `Providers & Models`.

- [ ] **Step 3: Implement vertical sidebar**

In `buildSettingsPanel`, replace the horizontal submenu row with a two-column content row:
- Left column: vertical sidebar card with `Models` and `General` buttons.
- Right column: active Settings content card.
- Preserve existing selected section messages and button variants.

- [ ] **Step 4: Run Native tests**

Run: `native test`

Expected: all Native tests pass.

- [ ] **Step 5: Run Native build**

Run: `native build`

Expected: build succeeds and creates `zig-out/bin/assistant`.

- [ ] **Step 6: Live smoke test**

Restart Native dev with automation, open Settings, and assert `Models` and `General` are visible while `Providers & Models` is absent.
