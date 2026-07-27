# Settings Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Settings provider cards with a compact provider-grouped model catalog and modal API-key unlock flow.

**Architecture:** Add a dedicated backend `GET /settings/model-catalog` endpoint that returns providers with nested models and shared `key_source` semantics. Update the Native Settings state/parser/view to consume that catalog, render provider headers with model rows, open a modal for locked rows, and serialize Settings request bodies safely.

**Tech Stack:** FastAPI/Python tests with `pytest`; Native SDK/Zig tests with `native test`; UI smoke with `native automate`.

---

### Task 1: Backend Settings Catalog Endpoint

**Files:**
- Modify: `src/http/routers/settings.py`
- Test: `tests/api/test_settings_model_catalog.py`

- [ ] **Step 1: Write failing API tests**

Add tests that call `model_catalog()` directly and assert a `providers` response containing configured and locked providers, `hosted` Agnes with env key, user key precedence, nested models, and provider sorting.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/api/test_settings_model_catalog.py -v`
Expected: FAIL because `model_catalog` does not exist.

- [ ] **Step 3: Implement minimal endpoint**

Add shared provider metadata, key-source helper, static/registry model collection, and `@router.get("/model-catalog")`.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run pytest tests/api/test_settings_model_catalog.py tests/api/test_models_listing.py -v`
Expected: PASS.

### Task 2: Native Catalog Parsing and Safe JSON Bodies

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing Native tests**

Add tests for parsing `providers` catalog responses, configured-first sorting/grouping, locked model click opening modal state, and JSON request escaping for API keys/default models.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL on missing catalog parsing/modal behavior or old endpoint/body assumptions.

- [ ] **Step 3: Implement state/parser/update changes**

Increase catalog capacity, parse `/settings/model-catalog`, derive provider model indices, handle locked row clicks through modal state, and use JSON escaping for request bodies.

- [ ] **Step 4: Run tests and verify pass**

Run: `cd native-sdk-experiment && native test`
Expected: PASS.

### Task 3: Native Settings Catalog View

**Files:**
- Modify: `native-sdk-experiment/src/main.zig`
- Test: `native-sdk-experiment/src/tests.zig`

- [ ] **Step 1: Write failing view tests**

Add tests that Settings renders provider headers and compact model rows instead of Add Key provider cards, search filters by provider/model name, and locked rows display modal copy when activated.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd native-sdk-experiment && native test`
Expected: FAIL because current Settings renders provider cards/inline key expansion.

- [ ] **Step 3: Implement compact grouped list and modal view**

Render provider headers, model line-item buttons, selected/locked labels, search empty state, current model fallback, and centered modal overlay.

- [ ] **Step 4: Run tests and build**

Run: `cd native-sdk-experiment && native test && native build`
Expected: PASS/build succeeds.

### Task 4: Verification

**Files:**
- No planned source changes unless verification exposes bugs.

- [ ] **Step 1: Run backend tests**

Run: `uv run pytest tests/api/test_settings_model_catalog.py tests/api/test_models_listing.py -v`
Expected: PASS.

- [ ] **Step 2: Run Native tests/build**

Run: `cd native-sdk-experiment && native test && native build`
Expected: PASS/build succeeds.

- [ ] **Step 3: Optional live smoke**

Run app and use `native automate` to verify Settings shows grouped catalog and modal unlock.
