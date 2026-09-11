# Suite Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the provider-options regression test and make the complete test suite finish within 900 seconds without hiding hangs or allowing outbound network calls.

**Architecture:** Correct the stale test double to preserve the current `AgentLoop._run_impl(..., cost_tracker=...)` contract. Make desktop test setup process-isolated in effect by restoring the entire environment and relevant caches. Diagnose suite duration before applying narrow test timeouts/cleanup fixes to the named straggler.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, FastAPI TestClient, custom SDK AgentLoop.

**Spec:** `docs/superpowers/specs/2026-09-09-suite-reliability-design.md`

## Global Constraints

- Never run pytest without a `timeout` prefix.
- No production behavior change for provider-options; this is a test-double contract repair.
- No global `-k` exclusion, xfail, or blanket suite timeout may hide the mismatched approval tests.
- Tests may use local loopback servers, but must not make outbound HTTP.
- Full-suite acceptance is `timeout 900 uv run pytest tests/ -q` completing; record any independent pre-existing failure separately.
- Do not stage `.env`, `config.yaml`, Docker configuration, or scratch artifacts.

---

### Task 1: Repair the provider-options test-double contract

**Files:**
- Modify: `tests/api/test_provider_options.py:58-70`
- Test: `tests/api/test_provider_options.py`

**Interfaces:**
- Consumes: `AgentLoop._run_impl(self, messages, cost_tracker: CostTracker | None = None)`.
- Produces: spy that observes `self.run_config.provider_options` while forwarding `cost_tracker` unchanged.

- [ ] **Step 1: Preserve the current red proof**

Run: `timeout 100 uv run pytest tests/api/test_provider_options.py::test_provider_options_reach_provider_chat -q`

Expected: FAIL with `KeyError: 'po'` because the old spy cannot accept `cost_tracker`.

- [ ] **Step 2: Update only the spy signature and forwarding call**

```python
async def spy_run_impl(self, messages, *, cost_tracker=None):
    seen["po"] = dict(self.run_config.provider_options or {})
    return await orig(self, messages, cost_tracker=cost_tracker)
```

Do not alter `RunService`, `AgentLoop`, request validation, or provider code.

- [ ] **Step 3: Verify the complete test file**

Run: `timeout 100 uv run pytest tests/api/test_provider_options.py -q`

Expected: 3 passed. This proves the request option reaches the cached loop’s `RunConfig` without claiming a production wiring change.

- [ ] **Step 4: Commit**

```bash
git add tests/api/test_provider_options.py
git commit -m "test: update provider-options spy for cost tracker contract"
```

### Task 2: Make desktop-process state restoration complete

**Files:**
- Modify: `tests/api/test_desktop_server.py:22-50`
- Modify: `tests/api/conftest.py:15-48` only if cache clearing is needed for the regression test
- Test: `tests/api/test_desktop_server.py`
- Test: `tests/api/test_provider_options.py`

**Interfaces:**
- Produces: `desktop_env` teardown that restores `os.environ` exactly to its setup snapshot and resets settings/path/message caches after the desktop server changes global process state.

- [ ] **Step 1: Add an environment-leak regression test**

Invoke the existing desktop-main path under `desktop_env`, make it write a non-tracked temporary environment key, finish the fixture scope, then assert a subsequent test cannot read that key and `DEPLOYMENT_MODE` is restored. Keep the assertion in the desktop test module so fixture teardown actually runs.

- [ ] **Step 2: Run the red test against the fixed-name-only fixture**

Run: `timeout 120 uv run pytest tests/api/test_desktop_server.py tests/api/test_provider_options.py -q`

Expected: the new leak regression fails before the fixture changes.

- [ ] **Step 3: Restore the full environment and caches**

At fixture entry use `original_env = dict(os.environ)`. At teardown:

```python
for key in tuple(os.environ):
    if key not in original_env:
        os.environ.pop(key)
os.environ.update(original_env)
settings_module._config = None
```

Clear the same `MessageStore` and `DataPaths` caches used by the session-scoped API isolation fixture if desktop mode can populate them. Do not restore by assigning a replacement mapping; preserve the `os.environ` object.

- [ ] **Step 4: Verify ordering isolation**

Run: `timeout 180 uv run pytest tests/api/test_desktop_server.py tests/api/test_provider_options.py -q`

Expected: all pass in both collection orderings (`desktop_server` first and `provider_options` first).

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_desktop_server.py tests/api/conftest.py
git commit -m "test: restore desktop environment state completely"
```

### Task 3: Identify and eliminate the full-suite blockers

**Files:**
- Modify only the test(s) demonstrated by duration/hang evidence, expected candidates: `tests/api/test_ws_protocol.py:1208`, `tests/api/test_conversation.py:824,862`, registry test fixtures under `tests/sdk/`.
- Modify: `docs/audits/2026-08-24-deferred-followups.md` with measured residual evidence only if an independent blocker remains.

**Interfaces:**
- Produces: explicit local per-test time bounds/cleanup for known approval hang; mocked registry/provider HTTP boundary; duration evidence.

- [ ] **Step 1: Capture duration baselines**

Run:

```bash
timeout 400 uv run pytest tests/api -q --durations=10 -k "not mismatched_call_id"
timeout 500 uv run pytest tests/sdk -q --durations=10
```

Record the slowest ten test names and durations in the task report. Do not change a test merely because it is slow.

- [ ] **Step 2: Reproduce each mismatched approval test independently**

Run each named test with a hard 60-second process timeout. If it hangs, add the project’s available per-test timeout marker or deterministic websocket/client cleanup so it fails within 15 seconds with a diagnostic. It must remain selected by a normal suite run.

- [ ] **Step 3: Audit and isolate outbound HTTP**

Search test execution seams for `httpx`, `requests`, `urllib`, and registry fetches. Patch the registry HTTP client at its module-level import or supply a local fixture response. Preserve explicit desktop loopback tests; reject non-loopback destinations in the test fixture.

- [ ] **Step 4: Run the full gate and capture the terminal result**

Run: `timeout 900 uv run pytest tests/ -q --durations=10`

Expected: completes green. If an unrelated existing failure remains, identify it by name and prove it reproduces at the pre-task base; do not call the suite green.

- [ ] **Step 5: Commit**

```bash
git add tests docs/audits/2026-08-24-deferred-followups.md
git commit -m "test: make full suite deterministic within gate"
```

### Task 4: Reliability release verification

**Files:**
- Modify: none unless Task 3 finds an evidence-backed documentation residual.

- [ ] **Step 1: Run focused checks**

```bash
uv run ruff check tests/api/test_provider_options.py tests/api/test_desktop_server.py tests/api/test_ws_protocol.py tests/api/test_conversation.py
timeout 200 uv run pytest tests/api/test_provider_options.py tests/api/test_desktop_server.py -q
timeout 900 uv run pytest tests/ -q
```

- [ ] **Step 2: Report release evidence**

Report full-suite count, slowest ten, any explicit per-test timeout, and whether all network boundaries are mocked. Do not tag/push/publish in this task; those require owner approval.
