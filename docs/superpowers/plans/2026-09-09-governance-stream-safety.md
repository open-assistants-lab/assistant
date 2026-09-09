# Governance Stream Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore governance and summarization middleware registration, then make a governance-blocked call terminal before any tool execution path can invoke its tool body.

**Architecture:** Keep the parallel-safe and sequential paths: they are responsible for concurrency and ordering. Extract shared tool-call preparation in `AgentLoop` so input guardrails, argument middleware, and governance run exactly once per dispatch before either normal or streaming executor can run a tool. A blocked preparation produces a single synthetic result and never reaches `_execute_tool()`.

**Tech Stack:** Python 3.11+, pytest/pytest-asyncio, custom SDK `AgentLoop`, Pydantic config, SQLite governance store.

**Spec:** `docs/superpowers/specs/2026-09-09-governance-stream-safety-design.md`

## Global Constraints

- Never run pytest without a `timeout` prefix.
- Full stored history remains audit-visible; this plan only repairs middleware registration and tool authorization.
- A guard-blocked call is terminal: zero tool-body calls, zero execution-record entries, one model-visible result.
- Preserve existing interrupt/HITL UX and the parallel-safe/sequential classification rules.
- Do not stage `.env`, `config.yaml`, Docker configuration, or scratch artifacts.

---

### Task 1: Restore independent middleware registration (issue #20)

**Files:**
- Modify: `src/sdk/runner.py:697-735`
- Create: `tests/sdk/test_runner_middleware_registration.py`

**Interfaces:**
- Produces: `create_sdk_loop(...)` returns an `AgentLoop` whose `middlewares` contains `SummarizationMiddleware` when `summary_config.enabled` is true and `HITLMiddleware` when governance is enabled.
- Consumes: existing local `_persist_summary()` and `_prune_context(session_id: str, keep_messages: int) -> int` callback.

- [ ] **Step 1: Write failing loop-construction tests**

```python
@pytest.mark.asyncio
async def test_enabled_summarization_registers_summary_middleware(monkeypatch):
    loop = await _build_loop(monkeypatch, summarization_enabled=True, governance_enabled=False)
    assert any(isinstance(mw, SummarizationMiddleware) for mw in loop.middlewares)
    assert not any(isinstance(mw, HITLMiddleware) for mw in loop.middlewares)

@pytest.mark.asyncio
async def test_enabled_governance_registers_hitl_without_summarization(monkeypatch):
    loop = await _build_loop(monkeypatch, summarization_enabled=False, governance_enabled=True)
    assert any(isinstance(mw, HITLMiddleware) for mw in loop.middlewares)
    assert not any(isinstance(mw, SummarizationMiddleware) for mw in loop.middlewares)
```

The `_build_loop` fixture must patch the provider factory, paths, and user capabilities using the same test-local patterns already used by runner tests; it must not call a real provider or registry.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `timeout 120 uv run pytest tests/sdk/test_runner_middleware_registration.py -q`

Expected: FAIL because both appends are unreachable after `_prune_context()` returns.

- [ ] **Step 3: Dedent and separate registration**

Keep `_prune_context` limited to its callback:

```python
def _prune_context(session_id: str, keep_messages: int) -> int:
    store = get_message_store(user_id)
    return store.mark_context_excluded(session_id, keep_messages)
```

Append the summarizer after that definition only under `if summary_config.enabled:`. Add a separate `if governance_enabled():` branch (using the project’s existing governance configuration helper) that imports and appends `HITLMiddleware(user_id=user_id)`. Do not nest governance under summarization.

- [ ] **Step 4: Run focused tests and runner regressions**

Run: `timeout 180 uv run pytest tests/sdk/test_runner_middleware_registration.py tests/sdk -q -k "runner or governance"`

Expected: PASS.

- [ ] **Step 5: Commit the security hotfix**

```bash
git add src/sdk/runner.py tests/sdk/test_runner_middleware_registration.py
git commit -m "fix(security): restore governance middleware registration (issue #20)"
```

### Task 2: Add a shared terminal authorization preparation result

**Files:**
- Modify: `src/sdk/loop.py:806-1145`
- Modify: `tests/sdk/test_governance.py`
- Modify: `tests/api/test_governance_stream.py`

**Interfaces:**
- Produces: private `AgentLoop._prepare_tool_call(tc: ToolCall) -> PreparedToolCall`.
- `PreparedToolCall.call: ToolCall | None` is non-null only when execution is permitted.
- `PreparedToolCall.result: ToolResult | None` is non-null when input guardrails or governance block the call.

- [ ] **Step 1: Add failing normal and stream discriminator tests**

Use a counting fake tool marked non-destructive (to exercise the batch path) and a second marked destructive (to exercise sequential). Use `HITLMiddleware` configured to return a pending acknowledgement for `explicit_tool`.

```python
assert executions == []
assert guard_calls == ["explicit_tool"]
assert len([m for m in loop.state.messages if m.role == "tool"]) == 1
assert "governance" in loop.state.messages[-1].content
```

For streaming, consume via the same per-step-task consumer used in `test_langfuse_stream.py`:

```python
iterator = loop.run_stream([Message.user("run it")]).__aiter__()
chunks = []
while True:
    try:
        chunks.append(await asyncio.ensure_future(iterator.__anext__()))
    except StopAsyncIteration:
        break
assert executions == []
assert len([c for c in chunks if c.canonical_type == "tool_result"]) == 1
```

Add a test asserting a blocked call is not passed to `_record_executed_tools` by checking `loop.state.extra["_executed_tool_calls"]` is empty.

- [ ] **Step 2: Run the discriminator tests and verify current failure**

Run: `timeout 120 uv run pytest tests/sdk/test_governance.py tests/api/test_governance_stream.py -q`

Expected: #20-related registration tests fail before Task 1; after Task 1, the explicit stream duplicate-dispatch test exposes the issue #19 path or confirms the exact dispatch source.

- [ ] **Step 3: Implement one shared preparation routine**

Introduce a private dataclass near the other loop-private types:

```python
@dataclass(slots=True)
class _PreparedToolCall:
    call: ToolCall | None = None
    blocked_result: ToolResult | None = None
```

Implement `_prepare_tool_call()` to execute input guardrails, copy-and-transform arguments through `wrap_tool_call`, then call `_run_guards`. Convert a `GuardrailTripwire` into the same error `ToolResult` used today. Return either `call=transformed_call` or `blocked_result=result`; never both.

Replace duplicated pre-execution blocks in `_execute_single_tool`, `_execute_tool_batch`, `_execute_single_tool_streaming`, and `_execute_tool_batch_streaming` with this method. Preserve each method’s existing output guardrails, tracing, result serialization, and stream event formatting. A blocked result must return/yield before `_execute_tool` and before `_record_executed_tools`.

- [ ] **Step 4: Run focused regressions**

Run: `timeout 180 uv run pytest tests/sdk/test_governance.py tests/api/test_governance_stream.py tests/api/test_governance_api.py tests/api/test_approve_stream.py -q`

Expected: PASS; every explicit/hard-block call has exactly one guard result and zero body calls before approval.

- [ ] **Step 5: Commit the unified authorization change**

```bash
git add src/sdk/loop.py tests/sdk/test_governance.py tests/api/test_governance_stream.py
git commit -m "fix(governance): make blocked streamed calls terminal (issue #19)"
```

### Task 3: Release gates and emergency patch release

**Files:**
- Modify: none unless a test reveals a narrowly scoped defect.

- [ ] **Step 1: Run lint and type checks**

Run:

```bash
uv run ruff check src/sdk/runner.py src/sdk/loop.py tests/sdk/test_runner_middleware_registration.py tests/sdk/test_governance.py tests/api/test_governance_stream.py
timeout 200 uv run mypy src/sdk/runner.py src/sdk/loop.py
```

Expected: no newly introduced diagnostics. Report pre-existing diagnostics separately.

- [ ] **Step 2: Run the API safety gate**

Run: `timeout 400 uv run pytest tests/api -q -k "not mismatched_call_id"`

Expected: no #19/#20 failures. If the known independent `provider_options` regression remains, report it separately and do not attribute it to this release.

- [ ] **Step 3: Run the full suite timebox**

Run: `timeout 900 uv run pytest tests/ -q`

Expected: completion. If #15’s existing timeout remains, capture the last completed test and duration; do not silently waive it.

- [ ] **Step 4: Tag and publish**

After clean focused gates and API gate:

```bash
git push origin main
git tag -a v0.6.3 -m "v0.6.3 — restore governance registration and prevent stream HITL bypass"
git push origin v0.6.3
```

Confirm the GitHub Actions container publish completes before advising deployment users to pull `ghcr.io/open-assistants-lab/assistant:v0.6.3`.
