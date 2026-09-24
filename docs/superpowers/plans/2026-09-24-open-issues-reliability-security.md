# Open-Issue Reliability and Security Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the applicable open reliability, receipt-fidelity, session-lifecycle, and capability-boundary issues (#35–#42) in priority order while deferring MCP redesign issue #7.

**Architecture:** Reuse the existing execution kernel, outcome vocabulary, permission policy, message metadata, and session registry. Add bounded summary persistence, approval-required shell defaults, explicit outcome propagation, session leases, and truthful custom-command results without introducing a second governance or receipt system.

**Tech Stack:** Python 3.11–3.13, FastAPI, Pydantic v2, aiosqlite/SQLite, asyncio, pytest, Ruff, mypy, Bash pipeline semantics.

**Spec:** `docs/superpowers/specs/2026-09-24-open-issues-reliability-security-design.md`

## Global Constraints

- `shell_execute` defaults to approval-required (`ask`) when governance is enabled; it is not globally disabled by this plan.
- Explicit administrator `deny` always wins; user `allow` cannot weaken an administrator restriction.
- Unknown external effects remain `uncertain`/`unknown` and are never automatically replayed.
- MCP files and behavior are out of scope; issue #7 remains deferred.
- TDD is required: write each regression test, run it red, implement the smallest fix, run it green.
- Do not rewrite historical release tags; remediation ships in the next release.
- Preserve the existing SQLite one-process-per-user deployment model.
- Do not change unrelated local untracked files or the remaining `desktop-d3` worktree.

## Review Focus

- A shell request with no explicit item rule must resolve to `ask`, while administrator `deny` remains final.
- A timed-out governed command must never produce a success-only receipt or proposal projection.
- An oversized persisted summary must stop being selected for model context without deleting its audit history.
- A dropped SSE/WS client must not leave the session registry permanently busy.
- A custom command that exceeds the sandbox capture ceiling must not advertise recoverable output that was never captured.

---

## File Map

### Summarization and storage

- Modify `src/config/settings.py`: bounded summary and session-lease settings.
- Modify `src/sdk/middleware_summarization.py`: bounded previous-summary input, bounded persisted output, and oversized-summary fallback.
- Modify `src/storage/messages.py`: summary context-exclusion metadata and cache-aware summary selection.
- Modify `src/sdk/runner.py`: pass the summary budget and retain the corrected `_prune_context` import.
- Modify `seeds/prompts/summarisation_prompt.md`: document the summary-size contract.
- Test `tests/sdk/test_issue18.py`, `tests/sdk/test_summarization_overhaul.py`, `tests/sdk/test_summarization_incremental.py`, and `tests/storage/test_messages_store.py`.

### Governance and execution outcomes

- Modify `src/sdk/execution_models.py`: canonical `refused`/`killed` outcome values and compatibility aliases.
- Modify `src/sdk/tools.py`: internal `ToolResult` outcome metadata.
- Modify `src/sdk/permission_policy.py` and `src/sdk/governance.py`: shell default `ask`, outcome propagation, and proposal API contract.
- Modify `src/sdk/loop.py`: ensure synthetic refusals, timeouts, cancellations, and budget stops carry outcomes.
- Modify `src/http/routers/governance.py`: expose `status` and authoritative `outcome` together.
- Test `tests/sdk/test_permission_policy.py`, `tests/sdk/test_proposal_outcome.py`, `tests/sdk/test_tool_error_convention.py`, and governance API tests.

### Session lifecycle and loop limits

- Modify `src/sdk/session_worker.py`: activity timestamps, touch, and stale-lock cancellation.
- Modify `src/sdk/run_service.py`: refresh activity and classify iteration exhaustion.
- Modify `src/config/settings.py` and `src/sdk/profile_loader.py`: `agent.max_iterations` wiring.
- Modify `src/sdk/run_models.py`: explicit incomplete/iteration-limit terminal representation.
- Modify `src/http/routers/conversation.py`: regression coverage for cancel/drop recovery and user-visible terminal status.
- Test `tests/sdk/test_run_service.py`, `tests/api/test_stream_cancel_race.py`, `tests/sdk/test_sdk_loop.py`, and `tests/api/test_conversation.py`.

### Custom command execution

- Modify `src/sdk/tools.py`: `ToolAnnotations.pipefail`.
- Modify `src/sdk/tools_custom.py`: parse/pass `pipefail` and propagate capture truncation.
- Modify `src/sdk/sandbox.py`: preserve capture-ceiling signal through custom-command results.
- Modify `seeds/skills/cli-toolkit/SKILL.md`: document pipeline and output contracts.
- Test `tests/sdk/test_custom_tool_results.py`, `tests/sdk/test_custom_tool_sandbox.py`, `tests/sdk/test_pipeline_signal_death.py`, and new large-output tests.

---

## Task 1: Bound and Recover Summaries (#42)

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/sdk/middleware_summarization.py`
- Modify: `src/storage/messages.py`
- Modify: `src/sdk/runner.py`
- Modify: `seeds/prompts/summarisation_prompt.md`
- Test: `tests/sdk/test_issue18.py`
- Test: `tests/sdk/test_summarization_overhaul.py`
- Test: `tests/sdk/test_summarization_incremental.py`
- Test: `tests/sdk/test_runner.py`
- Test: `tests/storage/test_messages_store.py`

**Interfaces:**
- Produces `SummarizationConfig.max_summary_chars: int = 24_000`.
- Produces `MessageStore.mark_summary_context_excluded(summary_id: str) -> bool`.
- Produces a bounded summary helper that preserves the beginning and end of an oversized text block with the exact marker `[... summary omitted ...]`.
- `SummarizationMiddleware` receives `max_summary_chars` and passes it to the bounded-summary helpers.

- [ ] **Step 1: Write failing tests for the real escape hatch and summary bound.**

Add tests to the existing summarization test modules using their `_Provider`, `_state`, and `_context` helpers. Define a `store` fixture with `MessageStore("test_user", base_dir=tmp_path)`. The tests are:

```python
def test_prune_context_callback_is_invoked_with_session_and_boundary(middleware):
    calls: list[tuple[str, int]] = []
    middleware.context_pruner = lambda session_id, keep: calls.append((session_id, keep)) or 1

    assert middleware.context_pruner("session-1", 20) == 1
    assert calls == [("session-1", 20)]


def test_excluded_summary_is_not_selected_for_model_context(store):
    summary_id = store.add_summary_message(
        "x" * 50_000,
        session_id="s",
        metadata={
            "compression_reason": "threshold",
            "summarized_message_ids": [],
            "preserved_message_ids": [],
        },
    )
    store.mark_summary_context_excluded(summary_id)

    messages = store.get_messages_with_summary("s")
    assert all(message.id != summary_id for message in messages)


def test_oversized_summary_uses_bounded_fallback(middleware):
    bounded = middleware._bounded_summary("x" * 50_000)

    assert len(bounded) <= middleware.max_summary_chars
    assert "[summary omitted" in bounded
```

The tests must assert persisted history remains available through the audit/store path while model-context selection excludes the unusable summary.

- [ ] **Step 2: Run the new tests and verify they fail for the missing behavior.**

Run:

```bash
uv run pytest -q \
  tests/sdk/test_issue18.py \
  tests/sdk/test_summarization_overhaul.py \
  tests/sdk/test_summarization_incremental.py \
  tests/storage/test_messages_store.py
```

Expected failures must identify the oversized-summary selection, missing summary exclusion, or missing budget—not test setup errors.

- [ ] **Step 3: Add the configuration and storage primitives.**

Implement:

```python
class SummarizationConfig(_BaseSettings):
    max_summary_chars: int = Field(default=24_000, ge=1_000, le=200_000)
```

Add `include_in_model_context: bool = True` to summary metadata handling. `mark_summary_context_excluded()` must update the summary row metadata, invalidate `_summary_cache[session_id]`, and leave the row readable to audit/export queries.

Update `_find_newest_summary_state()` and backward summary selection so summaries with `include_in_model_context=False` are skipped. The existing `mark_context_excluded()` path remains responsible for old non-summary rows.

- [ ] **Step 4: Bound previous-summary input and generated output.**

In `SummarizationMiddleware`:

```python
previous_summary = self._bounded_summary(previous_summary)
```

The update prompt must state the exact maximum character budget. After provider generation, apply the same deterministic bound before persistence. If the generated result is over budget, persist a bounded fallback containing the exact omission marker rather than the oversized text. The helper must split the remaining budget between the beginning and end of the text so the total, including the marker, never exceeds `max_summary_chars`.

On a failed summary call, if the newest summary exceeds the budget, mark that summary context-excluded before the forced message trim. The in-memory replacement must omit the unusable summary as well.

- [ ] **Step 5: Preserve the corrected runner import and add the direct-call test.**

Keep `_prune_context` importing from:

```python
from src.storage.messages import get_message_store
```

The regression test must invoke the closure through the middleware context-pruner seam; it must not merely import `runner.py`.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest -q \
  tests/sdk/test_issue18.py \
  tests/sdk/test_summarization_overhaul.py \
  tests/sdk/test_summarization_incremental.py \
  tests/storage/test_messages_store.py
uv run ruff check src/config/settings.py src/sdk/middleware_summarization.py src/storage/messages.py src/sdk/runner.py tests/sdk/test_issue18.py tests/sdk/test_summarization_overhaul.py tests/sdk/test_summarization_incremental.py tests/storage/test_messages_store.py
```

Commit:

```bash
git add src/config/settings.py src/sdk/middleware_summarization.py src/storage/messages.py src/sdk/runner.py seeds/prompts/summarisation_prompt.md tests/sdk/test_issue18.py tests/sdk/test_summarization_overhaul.py tests/sdk/test_summarization_incremental.py tests/sdk/test_runner.py tests/storage/test_messages_store.py
git commit -m "fix: bound summary recovery context"
```

---

## Task 2: Require Approval for Shell by Default (#40)

**Files:**
- Modify: `src/sdk/governance.py`
- Modify: `src/sdk/permission_policy.py`
- Modify: `src/config/settings.py`
- Modify: `config.yaml`
- Modify: `seeds/skills/cli-toolkit/SKILL.md`
- Test: `tests/sdk/test_permission_policy.py`
- Test: `tests/api/test_governance_api.py`
- Test: `tests/config/test_settings_resolution.py`

**Interfaces:**
- `GovernanceService._default_permission("shell_execute")` returns `ask` when no stronger administrator/user rule applies.
- Existing `PermissionPolicy.resolve()` continues to make administrator `deny` and stricter user rules win.

- [ ] **Step 1: Write failing permission tests.**

Add:

```python
def test_shell_defaults_to_ask_when_governance_is_enabled(tmp_path, monkeypatch):
    service = GovernanceService(data_root=tmp_path)
    monkeypatch.setattr(governance, "governance_enabled", lambda: True)
    assert service.resolve_permission("user", "shell_execute") == "ask"


def test_admin_deny_still_wins_for_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"shell_execute":"deny"}}')
    # reload settings, resolve, assert deny


def test_user_allow_cannot_weaken_shell_ask(monkeypatch, tmp_path):
    # user allow + shell fallback ask => ask
```

- [ ] **Step 2: Run the tests red.**

```bash
uv run pytest -q tests/sdk/test_permission_policy.py tests/api/test_governance_api.py
```

Expected: the unconfigured shell case currently resolves `allow`.

- [ ] **Step 3: Implement the narrow default policy.**

Add a named high-impact tool set or explicit branch in `_default_permission()`:

```python
if tool_name == "shell_execute":
    return "ask"
```

Do not change administrator or user policy precedence. Keep the default `governance.enabled` behavior unchanged; the `ask` decision applies when governance middleware is active.

- [ ] **Step 4: Remove unsafe interpreter defaults and document the escalation.**

Change the safe `ShellToolConfig.allowed_commands` default to exclude `python3` and `node`. Keep those commands available only when explicitly configured. Update deployment/skill documentation to state that shell, interpreters, process execution, and network access are explicit capability escalations and that soft sandboxing does not provide kernel-enforced network isolation.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_permission_policy.py tests/api/test_governance_api.py
uv run ruff check src/sdk/governance.py src/sdk/permission_policy.py src/config/settings.py
```

Commit:

```bash
git add src/sdk/governance.py src/sdk/permission_policy.py src/config/settings.py config.yaml seeds/skills/cli-toolkit/SKILL.md tests/sdk/test_permission_policy.py tests/api/test_governance_api.py tests/config/test_settings_resolution.py
git commit -m "fix: require approval for shell capability"
```

If the shell skill path does not exist, update the existing shell documentation discovered by `rg -l "shell_execute" seeds docs`.

---

## Task 3: Unify Execution Outcomes (#41 and #38)

**Files:**
- Modify: `src/sdk/execution_models.py`
- Modify: `src/sdk/tools.py`
- Modify: `src/sdk/tool_results.py`
- Modify: `src/sdk/loop.py`
- Modify: `src/sdk/governance.py`
- Modify: `src/http/routers/governance.py`
- Test: `tests/sdk/test_proposal_outcome.py`
- Test: `tests/sdk/test_tool_error_convention.py`
- Test: `tests/sdk/test_governance.py`
- Test: `tests/api/test_governance_api.py`

**Interfaces:**
- `Outcome` accepts `refused` and `killed`; legacy `rejected` remains readable during migration.
- `ToolResult` carries optional internal outcome metadata without changing provider tool schemas.
- Governed proposal responses expose both `status` and authoritative `outcome`.
- Every `AgentLoop` tool return is normalized to a `ToolResult` with an outcome. Read-only ungoverned tools emit that outcome in the tool/audit boundary; durable governed receipts remain owned by the execution kernel.

- [ ] **Step 1: Write failing contract tests.**

Add tests using the existing `ToolResult` and `Outcome` imports. The tests are:

```python
def test_timeout_result_carries_timed_out_outcome():
    result = ToolResult(content="timed out", is_error=True, outcome=Outcome.TIMED_OUT)
    assert result.outcome == Outcome.TIMED_OUT


def test_refusal_result_carries_refused_outcome():
    result = ToolResult(content="denied", is_error=True, outcome=Outcome.REFUSED)
    assert result.outcome == Outcome.REFUSED


def test_killed_result_carries_killed_outcome():
    result = ToolResult(content="killed", is_error=True, outcome=Outcome.KILLED)
    assert result.outcome == Outcome.KILLED


def test_legacy_rejected_outcome_is_readable():
    assert Outcome("rejected") == Outcome.REJECTED
```

The tests must assert that a timeout cannot be represented only as `is_error=False` or `status=executed` without a separate timeout outcome.

- [ ] **Step 2: Run the tests red.**

```bash
uv run pytest -q tests/sdk/test_proposal_outcome.py tests/sdk/test_tool_error_convention.py tests/api/test_governance_api.py
```

- [ ] **Step 3: Align the shared vocabulary.**

Extend `Outcome` with canonical `REFUSED = "refused"` and `KILLED = "killed"` values. Preserve `REJECTED` as a compatibility alias or migration reader, never as a second new write spelling.

- [ ] **Step 4: Add internal outcome propagation to `ToolResult`.**

Add an optional internal field or structured metadata field that is not included in model tool schemas. `ToolResult.from_raw()` derives `succeeded` for ordinary successful values; explicit error results must be constructed with the correct outcome by the execution boundary.

Update synthetic `AgentLoop` results for unknown tool, disabled tool, permission refusal, timeout, cancellation, and tool-call budget exhaustion. Normalize every ordinary tool return through the same helper so a plain string cannot bypass outcome classification.

- [ ] **Step 5: Make proposal responses authoritative about outcome.**

Keep the database `status="executed"` transition required for replay/idempotency. Ensure `get_pending()` and HTTP responses return the stored `outcome` beside status. Update documentation and tests so consumers do not infer success from status alone.

- [ ] **Step 6: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_proposal_outcome.py tests/sdk/test_tool_error_convention.py tests/api/test_governance_api.py
uv run ruff check src/sdk/execution_models.py src/sdk/tools.py src/sdk/tool_results.py src/sdk/loop.py src/sdk/governance.py src/http/routers/governance.py
```

Commit:

```bash
git add src/sdk/execution_models.py src/sdk/tools.py src/sdk/tool_results.py src/sdk/loop.py src/sdk/governance.py src/http/routers/governance.py tests/sdk/test_proposal_outcome.py tests/sdk/test_tool_error_convention.py tests/sdk/test_governance.py tests/api/test_governance_api.py
git commit -m "fix: make execution outcomes authoritative"
```

---

## Task 4: Recover Dropped Sessions (#37)

**Files:**
- Modify: `src/sdk/session_worker.py`
- Modify: `src/sdk/run_service.py`
- Modify: `src/config/settings.py`
- Modify: `src/http/routers/conversation.py`
- Test: `tests/sdk/test_session_worker.py`
- Test: `tests/api/test_stream_cancel_race.py`
- Test: `tests/api/test_conversation.py`
- Test: `tests/config/test_settings_resolution.py`

**Interfaces:**
- `SessionLock.touch()` refreshes activity.
- `SessionWorkerRegistry.reap_stale(max_idle_seconds: int) -> list[str]` requests cancellation for stale locks without deleting a still-running lock.
- `message/cancel` remains the user-facing cancellation path.

- [ ] **Step 1: Write failing dropped-stream tests.**

Cover:

```python
async def test_stale_session_is_cancelled_without_concurrent_release():
    registry = SessionWorkerRegistry()
    lock = await registry.acquire("chat-1")
    lock._last_activity = 0.0

    assert await registry.reap_stale(max_idle_seconds=1) == ["chat-1"]
    assert lock.cancelled
    assert "chat-1" in registry.active_sessions
```

The test must simulate client disconnect while the run is still active, then assert the registry is not permanently busy and collected partial state is persisted once.

- [ ] **Step 2: Run red tests.**

```bash
uv run pytest -q tests/sdk/test_session_worker.py tests/api/test_stream_cancel_race.py tests/api/test_conversation.py
```

The existing cleanup paths remain covered by the disconnect tests. The lease reaper is an additional safety net for a run that does not observe generator cancellation.

- [ ] **Step 3: Add activity tracking and stale cancellation.**

Add a five-minute default setting:

```python
session_lease_timeout_seconds: int = Field(default=300, ge=30, le=3600)
```

Add a monotonic `last_activity` field to `SessionLock`, a `touch()` method, and registry stale detection. `SessionWorkerRegistry.acquire()` invokes `reap_stale()` before the busy check, and `RunService` touches the lock at event/provider/tool boundaries. Reaping requests cancellation and leaves lock removal to the existing `finally` release path, preventing concurrent ownership. The existing `/message/cancel` endpoint remains the explicit user recovery path if a non-cooperative provider call has not yet released its lock.

- [ ] **Step 4: Verify, document, and commit.**

```bash
uv run pytest -q tests/sdk/test_session_worker.py tests/api/test_stream_cancel_race.py tests/api/test_conversation.py
uv run ruff check src/sdk/session_worker.py src/sdk/run_service.py src/config/settings.py src/http/routers/conversation.py
```

Commit:

```bash
git add src/sdk/session_worker.py src/sdk/run_service.py src/config/settings.py src/http/routers/conversation.py tests/sdk/test_session_worker.py tests/api/test_stream_cancel_race.py tests/api/test_conversation.py tests/config/test_settings_resolution.py
git commit -m "fix: recover dropped session runs"
```

---

## Task 5: Expose Iteration Limits (#39)

**Files:**
- Modify: `src/config/settings.py`
- Modify: `config.yaml`
- Modify: `src/sdk/profile_loader.py`
- Modify: `src/sdk/loop.py`
- Modify: `src/sdk/run_models.py`
- Modify: `src/sdk/run_service.py`
- Test: `tests/sdk/test_sdk_loop.py`
- Test: `tests/sdk/test_run_service.py`
- Test: `tests/fixtures/run_contracts/run_result.json`
- Test: `tests/fixtures/run_contracts/turns_response.json`
- Test: `tests/fixtures/run_contracts/run_events.json`
- Test: `tests/config/test_settings_resolution.py`

**Interfaces:**
- `AgentConfig.max_iterations: int = 25` with a validated finite range.
- `LoopSpec.run_config_kwargs` includes `max_iterations` when a profile/bootstrap source is used.
- `RunStatus.INCOMPLETE` and `RunResult.termination_reason="iteration_limit"` distinguish budget exhaustion from completion/failure.

- [ ] **Step 1: Write failing configuration and outcome tests.**

```python
def test_agent_max_iterations_setting_wires_to_run_config():
    settings = get_settings()
    settings.agent.max_iterations = 7
    assert settings.agent.max_iterations == 7


async def test_iteration_exhaustion_is_incomplete(loop):
    result = await loop.run([Message.user("keep going")])
    assert result[-1].metadata["termination_reason"] == "iteration_limit"
```

- [ ] **Step 2: Run red tests.**

```bash
uv run pytest -q tests/sdk/test_sdk_loop.py tests/sdk/test_run_service.py tests/config/test_settings_resolution.py
```

- [ ] **Step 3: Implement the setting and terminal state.**

Add the validated setting and document the default in `config.yaml`. Pass it into `RunConfig` whenever no more-specific profile value exists. At the end of the ReAct loop, record `termination_reason="iteration_limit"` and return an incomplete terminal result rather than an ordinary completion.

- [ ] **Step 4: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_sdk_loop.py tests/sdk/test_run_service.py tests/config/test_settings_resolution.py
uv run ruff check src/config/settings.py src/sdk/profile_loader.py src/sdk/loop.py src/sdk/run_models.py src/sdk/run_service.py
```

Commit:

```bash
git add src/config/settings.py config.yaml src/sdk/profile_loader.py src/sdk/loop.py src/sdk/run_models.py src/sdk/run_service.py tests/sdk/test_sdk_loop.py tests/sdk/test_run_service.py tests/fixtures/run_contracts/run_result.json tests/fixtures/run_contracts/turns_response.json tests/fixtures/run_contracts/run_events.json tests/config/test_settings_resolution.py
git commit -m "feat: expose loop iteration limits"
```

---

## Task 6: Add Explicit Custom Pipeline Semantics (#35)

**Files:**
- Modify: `src/sdk/tools.py`
- Modify: `src/sdk/tools_custom.py`
- Modify: `src/sdk/tool_index.py`
- Modify: `seeds/skills/cli-toolkit/SKILL.md`
- Test: `tests/sdk/test_custom_tool_results.py`
- Test: `tests/sdk/test_pipeline_signal_death.py`
- Create or modify: `tests/sdk/test_custom_tool_pipefail.py`

**Interfaces:**
- `ToolAnnotations.pipefail: bool = False`.
- `run_custom_command(rendered: str, user_id: str, workspace_id: str, timeout_seconds: float | None, pipefail: bool = False)` selects Bash pipefail mode when requested.
- A non-zero pipeline member becomes a `ToolResult(is_error=True)` with a failure outcome.

- [ ] **Step 1: Write failing real-pipeline tests.**

```python
def test_pipefail_opt_in_reports_early_pipeline_failure(tmp_path):
    tool = make_tool(tmp_path, command="false | cat", annotations={"pipefail": True})
    result = tool.function()

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content["outcome"] == "failed"


def test_default_pipeline_semantics_remain_compatible(tmp_path):
    tool = make_tool(tmp_path, command="false | cat")
    result = tool.function()

    assert result == "(no output)"
```

Copy the existing `make_tool()` helper from `tests/sdk/test_custom_tool_sandbox.py` into the new test module and use a real early failure such as `false | cat`, plus a legitimate early non-zero filter to document compatibility.

- [ ] **Step 2: Run red tests.**

```bash
uv run pytest -q tests/sdk/test_custom_tool_pipefail.py tests/sdk/test_custom_tool_results.py tests/sdk/test_pipeline_signal_death.py
```

- [ ] **Step 3: Implement the annotation and Bash path.**

Add the boolean to `ToolAnnotations`, parse it from custom tool annotations, and pass it to the command wrapper. When true, execute with Bash pipefail enabled; when false, preserve the current shell behavior. Convert non-zero results to structured error results rather than success strings.

- [ ] **Step 4: Document and commit.**

Update the authoring skill with default and opt-in semantics.

```bash
uv run pytest -q tests/sdk/test_custom_tool_pipefail.py tests/sdk/test_custom_tool_results.py tests/sdk/test_pipeline_signal_death.py
uv run ruff check src/sdk/tools.py src/sdk/tools_custom.py tests/sdk/test_custom_tool_pipefail.py
```

Commit:

```bash
git add src/sdk/tools.py src/sdk/tools_custom.py src/sdk/tool_index.py seeds/skills/cli-toolkit/SKILL.md tests/sdk/test_custom_tool_pipefail.py tests/sdk/test_custom_tool_results.py tests/sdk/test_pipeline_signal_death.py
git commit -m "feat: expose custom pipeline failure semantics"
```

---

## Task 7: Surface Custom Output Capture Limits (#36)

**Files:**
- Modify: `src/sdk/tools_custom.py`
- Modify: `src/sdk/sandbox.py`
- Modify: `src/sdk/tool_results.py`
- Test: `tests/sdk/test_custom_tool_results.py`
- Test: `tests/sdk/test_custom_tool_output_ceiling.py`
- Create: `tests/sdk/test_custom_tool_output_ceiling.py`

**Interfaces:**
- `run_custom_command()` returns an error result containing `truncated: true` and `capture_limit_bytes` when `SandboxResult.stdout_truncated` is true.
- The result does not advertise `tool_result_read` recovery for uncaptured output.

- [ ] **Step 1: Write a real oversized-output regression test.**

```python
def test_custom_command_above_capture_ceiling_is_explicitly_truncated(tmp_path):
    tool = make_large_output_tool(tmp_path, size=1_000_000)
    result = tool.function()

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content["truncated"] is True
    assert result.structured_content["capture_limit_bytes"] > 0
    assert "tool_result_read" not in result.content
```

Define `make_large_output_tool(tmp_path, size)` in the new test module using the existing `make_tool()` helper and a Python command that writes `size` bytes to stdout. Pin `SANDBOX_BACKEND=soft` and `max_output_kb=100`; use `size=1_000_000`, which exceeds the resulting 819,200-byte capture ceiling. The command must emit more than `max_output_bytes * 8`, not merely mock a result object. Assert the result is not a success-shaped string and does not claim recoverable output.

- [ ] **Step 2: Run the test red.**

```bash
uv run pytest -q tests/sdk/test_custom_tool_output_ceiling.py tests/sdk/test_custom_tool_results.py
```

- [ ] **Step 3: Propagate the capture signal and truthful result.**

Consume `SandboxResult.stdout_truncated` in `run_custom_command()`. Return a structured failure/partial result containing the actual capture ceiling and a clear statement that full recovery is unavailable. Preserve existing `format_output()` recovery for output that was actually captured.

- [ ] **Step 4: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_custom_tool_output_ceiling.py tests/sdk/test_custom_tool_results.py
uv run ruff check src/sdk/tools_custom.py src/sdk/sandbox.py src/sdk/tool_results.py
```

Commit:

```bash
git add src/sdk/tools_custom.py src/sdk/sandbox.py src/sdk/tool_results.py tests/sdk/test_custom_tool_output_ceiling.py tests/sdk/test_custom_tool_results.py
git commit -m "fix: surface custom command output truncation"
```

---

## Final Verification and Issue Update

- [ ] Run all issue-focused tests together.
- [ ] Run the full Python suite.
- [ ] Run `uv run ruff check src/`.
- [ ] Run scoped mypy for changed non-router modules and record any pre-existing baseline failures separately.
- [ ] Run `git diff --check`.
- [ ] Verify no MCP files are present in the remediation diff.
- [ ] Update each issue with test evidence and release status; close only issues whose own acceptance criteria are green.
- [ ] Leave #7 open and mark it deferred for MCP redesign.
- [ ] Prepare the next release tag only after versioning is normalized and release checks pass.
