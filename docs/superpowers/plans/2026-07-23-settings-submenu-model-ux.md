# Settings Submenu Model UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Settings into `Providers & Models` and `General`, and improve the model catalog so status appears on model rows instead of far-away provider headers.

**Architecture:** Keep the existing Native `SettingsState` and backend catalog endpoints. Add a small Settings section enum in `native-sdk-experiment/src/main.zig`, render a submenu at the top of Settings, move Appearance/About into General, and make model rows carry selected/add-key state near the model name.

**Tech Stack:** Native SDK/Zig view tests with `native test`, build with `native build`, backend regression checks with `pytest` for provider/catalog behavior.

---

### Task 1: Native Settings Submenu State

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing tests**

Add tests that open Settings and assert `Providers & Models` and `General` submenu buttons render, with Providers & Models active by default.

- [ ] **Step 2: Run Native tests**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL because submenu labels do not exist.

- [ ] **Step 3: Implement minimal submenu state**

Add `SettingsSection` enum, `section` field on `SettingsState`, and message variants to switch sections.

- [ ] **Step 4: Run Native tests**

Run: `cd native-sdk-experiment && native test`
Expected: PASS for submenu state tests.

### Task 2: Providers & Models View

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing tests**

Add tests that provider headers have no RHS `Ready`, selected model rows show `✓` close to the option, locked model rows show `Add key`, and `Env` is absent from model/composer labels.

- [ ] **Step 2: Run Native tests**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL because provider headers still show `Ready` and composer can show `Env`.

- [ ] **Step 3: Implement row-local status**

Remove provider-header status, render selected model row with `✓`, locked rows with `Add key`, keep provider background fill, and map composer source labels to user-facing copy or omit them.

- [ ] **Step 4: Run Native tests**

Run: `cd native-sdk-experiment && native test`
Expected: PASS.

### Task 3: General View

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing tests**

Add tests that Appearance/About appear under General and are absent from Providers & Models.

- [ ] **Step 2: Run Native tests**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL because current Settings shows Appearance/About in the same catalog view.

- [ ] **Step 3: Implement section-specific rendering**

Render the catalog only in Providers & Models; render Appearance/About only in General.

- [ ] **Step 4: Run Native tests and build**

Run: `cd native-sdk-experiment && native test && native build`
Expected: PASS/build succeeds.

### Task 4: Verification and Live Smoke

**Files:**
- No planned source changes unless verification exposes issues.

- [ ] **Step 1: Run backend regression checks**

Run: `uv run python -m pytest tests/sdk/test_providers.py tests/api/test_settings_model_catalog.py tests/api/test_models_listing.py -v`
Expected: PASS.

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/sdk/providers/factory.py src/http/routers/settings.py tests/sdk/test_providers.py tests/api/test_settings_model_catalog.py`
Expected: PASS.

- [ ] **Step 3: Run Native verification**

Run: `cd native-sdk-experiment && native test && native build`
Expected: PASS/build succeeds.

- [ ] **Step 4: Restart Native dev and smoke test**

Run `native dev -Dautomation=true`, open Settings, and assert `Providers & Models`, `General`, provider headers, row-local `✓`/`Add key`, and no `Env` text.
