# Subagent Architecture Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce duplicated subagent policy and lifecycle state while preserving fail-closed launch, runtime governance, restart-safe status, and idempotent parent notifications.

**Architecture:** First inventory actual callers, persisted fields, legacy profiles, notification requirements, and argument-sensitive permission behavior. Then make narrowly scoped changes to legacy-policy normalization, child approval outcomes, manifest identity, and parent-message idempotency. Keep SQLite task state authoritative and do not refactor the general agent loop or middleware unless the audit demonstrates a concrete, testable reduction.

**Tech Stack:** Python 3.11+, Pydantic/AgentProfile, aiosqlite/SQLite WAL, pytest/pytest-asyncio, existing `AgentLoop`, `HITLMiddleware`, `SubagentCoordinator`, `MessageStore`, and `SubagentCompletionBus`.

**Spec:** `docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md`

## Global Constraints

- A child cannot start if a declared capability is missing, disabled, forbidden, denied, or known to require approval before launch.
- The child receives only the immutable effective tool and skill manifest recorded for that run; no hidden mandatory tools or post-preflight silent narrowing.
- The main agent's governance semantics do not change.
- Argument-sensitive permissions and administrator `deny` remain authoritative at tool-call time. A profile declaration or launch manifest is not a blanket approval for arbitrary arguments.
- Task lifecycle status is durable and authoritative. Result-level reasons such as `blocked` and `uncertain` remain distinguishable without implying every rejected launch creates a task row.
- Every routable terminal background-task outcome (success, failure, timeout, cancellation, or blocked) is replayed into its parent conversation after restart and applied idempotently. Tasks without a parent session retain durable task status without a conversation message.
- Existing profiles use a one-time, observable migration: legacy omitted/ambiguous tool selection maps to `SAFE_DEFAULT`; a clearly explicit empty list maps to `NONE`. Never silently widen access.
- Keep SQLite as the Desktop persistence backend and preserve the one-process-per-user-store assumption.
- Provider-backed evaluation stays opt-in; default acceptance uses deterministic tests.
- Do not remove `invoke`, change `SAFE_DEFAULT` breadth, collapse persisted fields, or change profile schema semantics before Task 1 records evidence and the owner approves the decision.

## Review Focus

1. **Legacy profile with no `runtime-policy.json` and an empty tool list:** migration must not silently grant all native tools or silently produce an unexplained no-tool child; Task 1 inventories this and Task 2 pins the approved migration behavior.
2. **Argument-sensitive `ask` after child launch:** child must not continue and report success while a proposal is pending; Task 3 tests the selected terminal behavior without changing main-agent HITL behavior.
3. **Outbox replay after callback success but before acknowledgement:** parent conversation must not receive duplicate completion messages; Task 4 tests crash-window idempotency at the message-storage boundary.
4. **Permission changes after preflight:** a later administrator `deny` still blocks the real call while the task's recorded manifest remains unchanged; Task 3/5 tests this explicitly.
5. **Skill registry reload during a child run:** the child cannot load a skill outside its frozen manifest; Task 5 tests allowed and disallowed skill loads after reload.

---

### Task 1: Establish the architecture facts and resolve product gates

**Files:**
- Modify: `docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md`
- Create: `docs/audits/2026-09-24-subagent-simplification-inventory.md`
- Inspect only: `src/sdk/coordinator.py`, `src/sdk/subagent_capabilities.py`, `src/sdk/subagent_work_queue.py`, `src/sdk/subagent_completion.py`, `src/sdk/middleware_hitl.py`, `src/sdk/loop.py`, `src/sdk/run_service.py`, `src/storage/messages.py`, `src/sdk/tools_core/skills.py`

**Interfaces:**
- Consumes: current implementation and the review questions in the spec.
- Produces: an inventory table of each persisted field, its writer/readers/authority; caller evidence for `invoke`; observed legacy-profile counts/semantics; SAFE_DEFAULT tool count and approximate schema-token cost; outbox/message-store crash window; and a compatibility report for the approved migration and curated-default decisions.

**Decisions already approved:**
- Legacy profile migration is one-time: omitted/ambiguous tool selection -> `safe_default`; clearly explicit empty list -> `none`; visible diagnostic; never implicit all-native access.
- Child runtime `ask` stops the child with a typed `blocked`/approval-required result, creates no non-resumable child-owned proposal, and leaves main-agent governance unchanged.
- Every routable terminal outcome (success, failure, timeout, cancellation, or blocked) must replay into its parent conversation after restart. Durable `subagent_check` is a fallback, not a substitute for notification; tasks without a parent session retain durable status only.
- SAFE_DEFAULT should become a small curated set of local, workspace-scoped, read-only tools. Exclude networked tools and tools accessing cross-session or broader private data; broader capabilities require explicit allowlisting. Task 1 measures current use and compatibility before the set changes.
- `invoke` remains unchanged unless caller audit and owner approval support removal.

- [ ] **Step 1: Write passing characterization tests for current behavior.**

Add assertions for the current branch before making migration decisions:

```python
plan = _build_plan(monkeypatch, _profile(), ToolSelectionMode.LEGACY)
assert plan.effective_tools == ()  # current behavior; the old builder treated empty as all native tools
```

Also test an argument-sensitive permission: preflight with empty arguments succeeds, but an actual call with the restricted argument resolves to `ask`; assert the current child behavior and whether a proposal is written. Add a consumer test that injects a failure after message persistence but before outbox acknowledgement and records whether replay duplicates the parent message. These are characterization tests, not expected-to-fail tests; implementation regressions are added after the owner decisions.

- [ ] **Step 2: Run the characterization tests and save their observed output.**

Run:

```bash
uv run pytest tests/sdk/test_subagent_launch_preflight.py tests/sdk/test_subagent_completion.py \
  tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_v1.py \
  tests/sdk/test_permission_policy.py -q
```

Expected: tests demonstrate the current legacy interpretation, argument-sensitive `ask` behavior, and duplicate-delivery window. Do not modify runtime code in this task.

- [ ] **Step 3: Audit persisted state and consumers.**

Search for each work-queue column and `SubagentResult` field, each completion-event field, every `get_messages_by_session_id` call, and all `SubagentCoordinator.invoke` callers. Record actual file paths and call sites in the inventory; distinguish API consumers from tests.

- [ ] **Step 4: Measure default tool breadth and profile compatibility.**

Use a deterministic script/test to list effective SAFE_DEFAULT tool names and record serialized schema character count plus a clearly labeled approximate token estimate (for example, characters divided by four); no exact token-count utility currently exists for provider-independent schemas. Benchmark the existing completion history scan against representative seeded conversations (for example, 100, 1,000, and 10,000 messages) and record query time and scanned-row count. Measure current SAFE_DEFAULT use and compatibility impact before choosing exact membership for the approved curated set: classify each current tool by local/workspace-scoped/read-only versus networked/cross-session/broader-private-data access, then add tests pinning the resulting set and explicit-allowlist behavior. Search the configured user data path only when available; otherwise report “profile corpus not available” rather than assuming zero legacy profiles.

- [ ] **Step 5: Trace child runtime governance end to end.**

Document the sequence from `AgentLoop` tool dispatch through `HITLMiddleware.guard_tool_call`, proposal creation, tool result handling, coordinator completion, and `subagent_check`. Include the exact behavior when permission is `ask` and when it is `deny`.

- [ ] **Step 6: Write the inventory and explicit decisions.**

Save findings in `docs/audits/2026-09-24-subagent-simplification-inventory.md`. Update the spec's Open Decisions with the decisions already approved above. `invoke` removal remains gated on caller evidence and owner approval. Do not implement the curated SAFE_DEFAULT set until Task 1 measurement and compatibility tests establish a safe migration path.

- [ ] **Step 7: Commit the inventory and characterization tests.**

```bash
git add docs/audits/2026-09-24-subagent-simplification-inventory.md \
  docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md \
  docs/superpowers/plans/2026-09-24-subagent-architecture-simplification.md \
  tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_completion.py
git commit -m "test: characterize subagent simplification boundaries"
```

---

### Task 2: Normalize legacy profile policy without widening authority

**Files:**
- Modify: `src/sdk/coordinator.py` (`load_tool_selection_mode`, policy persistence/migration)
- Modify: `src/sdk/subagent_capabilities.py` (legacy resolution only if required by Task 1 decision)
- Modify: `src/sdk/tools_core/subagent.py` (profile creation/update policy persistence only if required)
- Modify: `tests/sdk/test_subagent_v1.py`
- Modify: `tests/sdk/test_subagent_tools_async.py`
- Modify: `tests/sdk/test_subagent_capabilities.py`

**Interfaces:**
- Consumes: owner-approved migration rule from Task 1.
- Produces: an explicit, versioned runtime policy for every newly created or migrated profile; `ToolSelectionMode` remains `SAFE_DEFAULT`, `NONE`, or `ALLOWLIST` at runtime. Any temporary `LEGACY` reader is migration-only and emits a visible diagnostic.

- [ ] **Step 1: Add migration tests for missing, valid, malformed, and unknown policy files.**

Pin each input to the approved output mode and diagnostic. Determine omitted-versus-empty from the raw PROFILE.md frontmatter before Pydantic defaults erase field presence: omitted or unprovably ambiguous maps to `SAFE_DEFAULT`; explicitly present `tools: []` maps to `NONE`. Assert malformed or unknown policy never falls back to a wider tool set. Include the case where an empty legacy profile previously meant all native tools.

- [ ] **Step 2: Run the migration tests and verify they fail against current behavior.**

```bash
uv run pytest tests/sdk/test_subagent_v1.py tests/sdk/test_subagent_tools_async.py -k 'runtime_policy or legacy_profile' -q
```

Expected: the legacy empty-profile and malformed-policy cases fail for the reasons found in Task 1.

- [ ] **Step 3: Implement one-time explicit policy migration.**

Use the owner-approved mapping. Treat both a missing policy and an existing `tool_selection: "legacy"` as migration inputs. Persist `runtime-policy.json` atomically using a temporary file and rename; include `version: 1`, `tool_selection`, and a migration source marker. Do not change `PROFILE.md` or user-authored files as part of migration. A missing/legacy policy is migrated once; malformed/unknown policy returns a fail-closed diagnostic rather than silently becoming `LEGACY`.

- [ ] **Step 4: Preserve create/update omitted-versus-empty behavior.**

Test and implement: omitted tools on create -> approved default mode; `tools=[]` -> `NONE`; named tools -> `ALLOWLIST`; `tools=None` on update -> unchanged; `tools=[]` on update -> `NONE`. Persist the corresponding policy beside PROFILE.md.

- [ ] **Step 5: Run the profile and capability contract suites.**

```bash
uv run pytest tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_tools_async.py tests/sdk/test_subagent_v1.py -q
```

Expected: PASS with no all-native expansion on legacy migration.

- [ ] **Step 6: Commit the bounded migration change.**

```bash
git add src/sdk/coordinator.py src/sdk/subagent_capabilities.py \
  src/sdk/tools_core/subagent.py tests/sdk/test_subagent_v1.py \
  tests/sdk/test_subagent_tools_async.py tests/sdk/test_subagent_capabilities.py
git commit -m "fix: migrate legacy subagent policy explicitly"
```

---

### Task 3: Define and enforce child runtime approval outcomes

**Files:**
- Modify: `src/sdk/middleware_hitl.py` (only if a small explicit child mode shares the existing policy resolver)
- Modify: `src/sdk/coordinator.py` (child middleware configuration and terminal result mapping)
- Modify: `src/sdk/subagent_models.py` (only if Task 1-approved typed result needs an added code)
- Modify: `src/sdk/subagent_work_queue.py` (persist the terminal reason atomically)
- Test: `tests/sdk/test_permission_policy.py`
- Test: `tests/sdk/test_subagent_v1.py`
- Test: `tests/sdk/test_subagent_completion.py`

**Interfaces:**
- Consumes: Task 1's approved runtime `ask` behavior and the existing `HITLMiddleware.guard_tool_call(tool_name, tool_input)` contract.
- Produces: a child-specific, argument-aware approval outcome. Main-agent middleware behavior remains byte-for-byte semantically unchanged. Recommended child policy: actual-call `ask` stops execution with `terminal_reason="blocked"`, stable `error_code="approval_required"`, and no non-resumable child proposal; `deny` remains a denial and never executes the tool.

- [ ] **Step 1: Add failing tests for argument-specific ask and deny in a child run.**

Use the permission policy fixture with one tool allowed for `{}` but `ask` for a specific argument. Assert the actual tool body is not called, no child-owned pending proposal is written under the recommended policy, queue status is terminal and not `completed`, and the result carries an approval-required code. Add a main-agent regression asserting existing HITL still creates the same proposal.

- [ ] **Step 2: Run the tests to demonstrate the current failure.**

```bash
uv run pytest tests/sdk/test_permission_policy.py tests/sdk/test_subagent_v1.py -k 'ask or deny or proposal' -q
```

Expected: the new child test exposes the current proposal-and-continue behavior while the main-agent control test passes.

- [ ] **Step 3: Add the smallest child-only permission boundary.**

Reuse the same `resolve_permission_for_call` implementation. When current loop context identifies a subagent and resolution is `ask`, return an error result marked with a stable child-block code and set a typed terminal signal on that run's context. Do not create a pending proposal in child mode. Leave `HITLMiddleware` default mode unchanged for main loops.

- [ ] **Step 4: Stop the child loop and persist the typed outcome.**

After dispatch returns the child-block signal, stop further tool calls and do not accept a later model-generated success as the task result. Transition the task once to `TaskStatus.FAILED` (the existing lifecycle enum has no `BLOCKED`) and store result-level `terminal_reason="blocked"` and `error_code="approval_required"`. Preserve run-scoped effective manifest and usage counters in the result.

- [ ] **Step 5: Run child and main governance regression suites.**

```bash
uv run pytest tests/sdk/test_permission_policy.py tests/sdk/test_subagent_v1.py \
  tests/sdk/test_subagent_completion.py tests/api/test_governance_api.py -q
```

Expected: child ask blocks without execution/proposal; child deny remains denied; main-agent approvals remain unchanged.

- [ ] **Step 6: Commit the runtime approval contract.**

```bash
git add src/sdk/middleware_hitl.py src/sdk/coordinator.py src/sdk/subagent_models.py \
  src/sdk/subagent_work_queue.py tests/sdk/test_permission_policy.py \
  tests/sdk/test_subagent_v1.py tests/sdk/test_subagent_completion.py
git commit -m "fix: terminate child runs on runtime approval requirements"
```

---

### Task 4: Make parent completion replay durable and idempotent

**Files:**
- Modify: `src/storage/messages.py` (transactional idempotent append API and additive schema)
- Modify: `src/sdk/run_service.py` (use the idempotent append API; remove conversation-history scan)
- Modify: `src/sdk/coordinator.py` (reconstruct parent workspace routing from the frozen task manifest during outbox replay)
- Modify: `src/sdk/subagent_work_queue.py` (join the retained task manifest into pending outbox reads)
- Modify: `src/sdk/subagent_completion.py` (stable completion event ID if task ID alone is not sufficient)
- Modify: `tests/storage/test_messages_store.py`
- Modify: `tests/sdk/test_subagent_completion.py`

**Interfaces:**
- Consumes: `SubagentCompletion(task_id, session_id, user_id, workspace_id, ...)` and the current MessageStore append API.
- Produces: every routable terminal outcome (success, failure, timeout, cancellation, or blocked) is replayed into its parent conversation/workspace across restart, with idempotent application. The parent workspace comes from the immutable launch plan, not the queue's user-level storage `workspace_id`. Tasks without a parent session still retain durable task status. `MessageStore.add_message_once(delivery_key: str, role: str, content: str, metadata: dict[str, Any], session_id: str) -> bool` adds a reserved `delivery_key` to message metadata and relies on a partial unique SQLite expression index over `(session_id, json_extract(metadata, '$.delivery_key'))`. The normal CoreMem ingest remains responsible for message insertion; a duplicate-key constraint hit returns `False` without a second parent-visible message.

- [ ] **Step 1: Add failing tests for duplicate and crash-window delivery.**

Assert success, failure, timeout, cancellation, and blocked terminal outcomes each insert exactly one session message; the same `delivery_key` does not insert another; and a simulated crash after message commit but before outbox acknowledgement followed by replay still leaves one message. Cover both idle assistant-message and active-loop steer persistence paths. Insert tasks from two different requested workspaces into the same user-level queue and assert replay routes each to its stored launch-plan workspace, not `workspace_id="user"`. When `session_id` is absent, assert the event is acknowledged without a parent message while the durable task result remains queryable.

- [ ] **Step 2: Run focused tests and confirm no idempotent storage primitive exists.**

```bash
uv run pytest tests/storage/test_messages_store.py tests/sdk/test_subagent_completion.py -q
```

Expected: new tests fail because MessageStore currently appends on every callback.

- [ ] **Step 3: Add an additive idempotency index in the conversation store.**

After CoreMem creates the `messages` table, add a partial unique index:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_delivery_key
ON messages(session_id, json_extract(metadata, '$.delivery_key'))
WHERE json_extract(metadata, '$.delivery_key') IS NOT NULL;
```

Keep legacy messages unaffected because they have no `delivery_key`. Add the migration to `MessageStore` startup using the existing additive-index pattern. Do not rewrite old rows or introduce a second delivery ledger that cannot share the CoreMem message transaction.

- [ ] **Step 4: Route both completion paths through the idempotent append.**

Use `subagent-completion:{task_id}` as the stable key. Implement the append through CoreMem ingest:

```python
metadata = {**(metadata or {}), "delivery_key": delivery_key}
try:
    self.add_message(role, content, metadata=metadata, session_id=session_id)
except sqlite3.IntegrityError:
    if self._has_delivery_key(session_id, delivery_key):
        return False
    raise
return True
```

`_has_delivery_key` must query the indexed JSON expression, not load conversation history. If CoreMem wraps SQLite errors, inspect the chained cause and only treat an integrity error as a duplicate when this exact key now exists; re-raise unrelated storage failures. Active-loop steer-sink behavior remains single-persist within a live turn and idempotent across restart.

- [ ] **Step 5: Run storage, completion, and conversation regression suites.**

```bash
uv run pytest tests/storage/test_messages_store.py tests/sdk/test_subagent_completion.py \
  tests/sdk/test_session_events.py tests/api/test_conversation.py -q
```

Expected: duplicate replay inserts one message; existing conversation persistence and active steering tests pass.

- [ ] **Step 6: Commit the storage idempotency primitive.**

```bash
git add src/storage/messages.py src/sdk/run_service.py src/sdk/subagent_completion.py \
  tests/storage/test_messages_store.py tests/sdk/test_subagent_completion.py
git commit -m "feat: make subagent completion persistence idempotent"
```

---

### Task 5: Canonicalize launch-manifest identity and field authority

**Files:**
- Modify: `src/sdk/subagent_capabilities.py` (`SubagentLaunchPlan`, canonical serialization/ID)
- Modify: `src/sdk/coordinator.py` (persist and execute the canonical manifest; use `requested_workspace_id`, while retaining user-level profile/queue storage)
- Modify: `src/sdk/subagent_work_queue.py` (migration/read helpers only if required)
- Modify: `src/sdk/subagent_models.py` (remove mirrored fields only if Task 1 consumer inventory proves unused)
- Test: `tests/sdk/test_subagent_capabilities.py`
- Test: `tests/sdk/test_subagent_completion.py`
- Test: `tests/sdk/test_subagent_v1.py`

**Interfaces:**
- Consumes: profile policy, effective tools/skills, and ordered `CapabilityDecision` diagnostics.
- Produces: canonical JSON for the accepted effective manifest and a stable content-derived `plan_id`; task execution always consumes the manifest stored at insertion, never recomputes it.

- [ ] **Step 1: Add failing tests for stable and distinct manifest IDs.**

Assert identical canonical effective manifests produce identical IDs despite dictionary insertion order; changes to effective tools, effective skills, selection mode, workspace/user identity, or relevant resolved permission outcome produce different IDs. Rejected plans must not be persisted as launchable tasks.

- [ ] **Step 2: Run capability and launch tests to confirm current ID collisions.**

```bash
uv run pytest tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_launch_preflight.py -q
```

Expected: a plan whose declarations are unchanged but effective permission outcome changes currently retains its ID; the new test fails.

- [ ] **Step 3: Define canonical accepted-manifest serialization.**

Serialize only deterministic fields: version, user identity, the coordinator's requested execution workspace (not the queue's user-level storage workspace), agent identity, selection mode, requested names needed for audit, effective tool/skill names, and the accepted permission/registry decision identifiers selected by Task 1. Sort mappings and normalize list ordering; do not include transient exception text or timestamps in the hash.

- [ ] **Step 4: Compute plan ID from canonical resolved content.**

Hash the canonical JSON using SHA-256 and retain a short display form only for UI/log correlation. Persist the full canonical manifest JSON with the task. Keep detailed rejection diagnostics outside successful manifest identity.

- [ ] **Step 5: Make authority explicit without premature field deletion.**

Add comments/types/tests stating: `work_queue.status` owns lifecycle; `result` owns output/usage/result-level reason; `launch_plan` owns the execution capability snapshot; outbox owns delivery state/routing. Inspect outbox retention and replay consumers: if the referenced task row is retained for the full outbox lifetime and no consumer needs the copied result/error payload, reduce the event to stable event/task identity plus routing fields; otherwise preserve the payload and document the retention dependency. Remove any other mirrored persisted field only if Task 1 proves no API, receipt, or recovery reader consumes it.

- [ ] **Step 6: Run capability, queue, and API regression suites.**

```bash
uv run pytest tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_completion.py \
  tests/sdk/test_subagent_v1.py tests/api/test_subagents.py -q
```

Expected: stable ID and stored-manifest execution tests pass; legacy result payloads still load.

- [ ] **Step 7: Commit manifest identity and ownership clarification.**

```bash
git add src/sdk/subagent_capabilities.py src/sdk/coordinator.py \
  src/sdk/subagent_work_queue.py src/sdk/subagent_models.py \
  tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_completion.py \
  tests/sdk/test_subagent_v1.py
git commit -m "refactor: make subagent launch manifest authoritative"
```

---

### Task 6: Close cross-component regressions and make only evidence-backed removals

**Files:**
- Modify: `src/sdk/loop.py` only if Task 1 demonstrates duplicate hook paths that can be consolidated without changing behavior
- Modify: `src/sdk/coordinator.py` only for a caller-audited `invoke` deprecation/removal decision
- Modify: `src/sdk/subagent_capabilities.py` (curated SAFE_DEFAULT membership, based on Task 1 evidence)
- Modify: `src/sdk/native_tools.py` only if Task 1 confirms intended custom/MCP support for child manifests
- Modify: `src/sdk/tools_core/skills.py` only if skill reload semantics require a boundary fix
- Modify: `src/sdk/tools_core/filesystem.py` and `src/sdk/tools_core/file_search.py` (only to enforce scoped file access and reject outside-root symlinks)
- Modify: `src/sdk/coordinator.py` (bind child user/workspace identity to file and skill tools; use requested workspace for preflight/manifest/execution)
- Test: `tests/sdk/test_sdk_loop.py`
- Test: `tests/sdk/test_subagent_v1.py`
- Test: `tests/sdk/test_subagent_tools_async.py`
- Test: `tests/sdk/test_skills_tools.py`
- Test: `tests/sdk/test_subagent_capabilities.py`
- Test: `tests/unit/test_filesystem_tools.py`
- Test: `tests/unit/test_file_search_perf.py`

**Interfaces:**
- Consumes: owner decisions and evidence from Task 1 plus earlier task contracts.
- Produces: no broad framework. Changes only a demonstrated bug or a duplication removal whose replacement has parity tests. If audit finds no safe removal, this task records “retain as-is” and adds the missing contract tests.

- [ ] **Step 1: Add parity tests before changing loop/middleware integration.**

Cover cancellation before dispatch, cancellation arriving after model response but before tool dispatch, non-streaming and streaming runs, child skill allowlist before/after `skills_reload`, unchanged main-agent HITL behavior, exact SAFE_DEFAULT tool-set membership, and rejection of networked/cross-session/broader-private-data/write tools from that default set. For each curated file tool, assert the child schema omits `user_id`/`workspace_id`, runtime invocation binds those values from the coordinator, parent-directory/absolute paths are rejected, and file-search symlinks resolving outside the workspace are not read or reported.

- [ ] **Step 2: Run the parity tests against current integration.**

```bash
uv run pytest tests/sdk/test_sdk_loop.py tests/sdk/test_subagent_capabilities.py \
  tests/sdk/test_subagent_v1.py tests/sdk/test_skills_tools.py \
  tests/sdk/test_permission_policy.py tests/unit/test_filesystem_tools.py \
  tests/unit/test_file_search_perf.py -k 'curated or scoped or symlink or bound_identity'
```

Expected: the new contract tests fail against current behavior because SAFE_DEFAULT currently exposes 23 tools, child tool schemas expose identity parameters, and grep follows outside-root file symlinks. Existing unrelated tests are deselected.

- [ ] **Step 3: Apply only approved, evidence-backed removals.**

If repository/external caller search shows `invoke` has no supported consumers and the owner approves removal, remove the method and its tests in this step. If callers remain, retain it and mark the deprecation policy. After Task 1's compatibility report, replace SAFE_DEFAULT's current all-read-only selection with exactly `files_list`, `files_read`, `files_glob_search`, and `files_grep_search`. Whenever a child receives one of these tools (including through an explicit allowlist), clone the tool definition with `user_id` and `workspace_id` removed from the model-visible schema and bind both from the coordinator's immutable run scope. Reject absolute/parent-traversing file paths and ensure glob/grep never report or read symlinks resolving outside the requested workspace. Bind the same identity fields for `skills_load`; broader tools remain available only through explicit allowlists that pass capability and governance preflight. Do not add custom/MCP support unless it is in the approved product scope.

- [ ] **Step 4: Run loop, middleware, tools, skills, and API suites.**

```bash
uv run pytest tests/sdk/test_sdk_loop.py tests/sdk/test_permission_policy.py \
  tests/sdk/test_skills_tools.py tests/sdk/test_subagent_capabilities.py \
  tests/sdk/test_subagent_v1.py tests/sdk/test_subagent_tools_async.py \
  tests/unit/test_filesystem_tools.py tests/unit/test_file_search_perf.py \
  tests/api/test_subagents.py -q
```

Expected: streaming/non-streaming parity and main governance contracts pass.

- [ ] **Step 5: Commit the minimal cross-component result.**

```bash
git add src/sdk/loop.py src/sdk/coordinator.py src/sdk/subagent_capabilities.py \
  src/sdk/native_tools.py src/sdk/tools_core/skills.py src/sdk/tools_core/filesystem.py \
  src/sdk/tools_core/file_search.py tests/sdk/test_sdk_loop.py \
  tests/sdk/test_subagent_v1.py tests/sdk/test_subagent_tools_async.py \
  tests/sdk/test_skills_tools.py tests/sdk/test_subagent_capabilities.py \
  tests/unit/test_filesystem_tools.py tests/unit/test_file_search_perf.py
git commit -m "refactor: reduce verified subagent integration duplication"
```

---

### Task 7: Final documentation, focused checks, full suite, and review

**Files:**
- Modify: `docs/SUBAGENT_RESEARCH.md`
- Modify: `docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md`
- Test: focused suites listed below; no new runtime source files

**Interfaces:**
- Consumes: completed Tasks 1–6 and their recorded decisions.
- Produces: final documented authority map, confirmed open decisions, and test/lint/type-check evidence.

- [ ] **Step 1: Update architecture documentation from implemented behavior only.**

Document profile migration semantics, exact manifest authority, child runtime-ask behavior, task-status/result-reason distinction, outbox idempotency boundary, and any retained legacy paths. Do not document planned-but-unimplemented behavior as current behavior.

- [ ] **Step 2: Run the focused reliability suite.**

```bash
uv run pytest tests/sdk/test_subagent_capabilities.py \
  tests/sdk/test_subagent_launch_preflight.py tests/sdk/test_subagent_completion.py \
  tests/sdk/test_subagent_tools_async.py tests/sdk/test_subagent_v1.py \
  tests/sdk/test_sdk_loop.py tests/sdk/test_permission_policy.py \
  tests/sdk/test_skills_tools.py tests/storage/test_messages_store.py \
  tests/unit/test_filesystem_tools.py tests/unit/test_file_search_perf.py \
  tests/api/test_subagents.py tests/api/test_governance_api.py -q
```

Expected: all deterministic tests pass; provider-backed evaluations remain skipped unless explicitly enabled.

- [ ] **Step 3: Run Ruff and mypy on changed source.**

```bash
uv run ruff check src/sdk/subagent_capabilities.py src/sdk/coordinator.py \
  src/sdk/subagent_work_queue.py src/sdk/subagent_completion.py \
  src/sdk/subagent_context.py src/sdk/subagent_models.py src/sdk/loop.py \
  src/sdk/middleware_hitl.py src/sdk/run_service.py src/sdk/tools_core/skills.py \
  src/sdk/tools_core/subagent.py src/sdk/tools_core/filesystem.py \
  src/sdk/tools_core/file_search.py src/storage/messages.py
uv run mypy src/sdk/subagent_capabilities.py src/sdk/coordinator.py \
  src/sdk/subagent_work_queue.py src/sdk/subagent_completion.py \
  src/sdk/subagent_context.py src/sdk/subagent_models.py src/sdk/loop.py \
  src/sdk/middleware_hitl.py src/sdk/tools_core/skills.py \
  src/sdk/tools_core/subagent.py src/sdk/tools_core/filesystem.py \
  src/sdk/tools_core/file_search.py src/storage/messages.py
```

Expected: both commands pass with no new ignores or broad type suppressions.

- [ ] **Step 4: Run the full default suite.**

```bash
uv run pytest -q
```

Expected: all default tests pass; live-provider evaluations remain opt-in under `RUN_HTTP_EVALS=1`.

- [ ] **Step 5: Request a fresh read-only architecture review.**

Ask the reviewer to inspect the final diff for: tool/skill authority widening, main-agent governance changes, outbox/message idempotency, task/result status inconsistency, migration regressions, and unnecessary new abstractions. Resolve every P0/P1 and either fix or explicitly accept each lower-severity finding with the owner.

- [ ] **Step 6: Commit docs and evidence references.**

```bash
git add docs/SUBAGENT_RESEARCH.md \
  docs/superpowers/specs/2026-09-24-subagent-architecture-simplification-review.md
git commit -m "docs: record subagent architecture simplification outcomes"
```

---

## Spec Coverage Self-Check

- Profile policy and legacy behavior: Tasks 1–2.
- Exact immutable tool/skill manifest and stable identity: Tasks 1, 5, and 6.
- Main-agent governance isolation and child runtime `ask`/`deny`: Tasks 1, 3, and 6.
- Durable task lifecycle and terminal vocabulary: Tasks 1, 3, and 5.
- Outbox replay and parent-message idempotency: Tasks 1 and 4.
- AgentLoop/middleware/tool/skill integration review: Tasks 1 and 6.
- SAFE_DEFAULT: Task 1 measures compatibility before implementing the approved curated local/read-only set. `invoke`, custom/MCP, and payload-retention changes remain gated on evidence and owner approval.

## Plan Self-Review

- Re-read the spec's requirements, component table, phase ordering, acceptance criteria, and open decisions against this plan. Approved decisions are recorded directly; the exact curated SAFE_DEFAULT membership and `invoke` removal remain evidence-gated.
- Corrected the initial Task 1 draft so characterization tests assert current behavior instead of containing an unresolved expected-value placeholder.
- Kept child lifecycle status distinct from result-level `blocked`: because the existing `TaskStatus` enum has no `BLOCKED`, the proposed terminal mapping is `TaskStatus.FAILED` plus `terminal_reason="blocked"` and `error_code="approval_required"`; the API must report both truthfully.
- Avoided claiming a distributed exactly-once transaction across separate SQLite databases. The outbox remains at-least-once; the conversation store enforces idempotent application through a unique indexed delivery key.
- Replaced an unsupported claim of exact provider-independent token counting with reproducible schema-character counts and a labeled estimate; included a benchmark of the current history-scan cost before replacement.
- Residual risk: child-specific runtime `ask` handling touches loop/middleware contracts and must be implemented only after tracing the actual dispatch and result propagation in Task 1. If a small child-only boundary cannot stop the loop without changing main-agent behavior, stop and revise the design rather than adding generalized framework machinery.
- Residual risk: Task 4's unique JSON-expression index depends on SQLite JSON1 and CoreMem surfacing integrity failures. Verify both in the repository's supported SQLite versions and wrap only the specific duplicate-delivery failure; if that seam is unsuitable, return to the owner with an alternative storage-level idempotency design before implementation.
