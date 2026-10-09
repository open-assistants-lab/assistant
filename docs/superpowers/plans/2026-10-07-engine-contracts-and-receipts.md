# Shared Engine Contracts and Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Current staging (2026-10-09):** SL/SR first; see the
[solo-first amendment](../specs/2026-10-09-sl-sr-first-delivery-amendment.md) before
re-scoping tasks. Resume partial Task1 with fresh solo provisioning/identity only;
execution stays refused until its policy/admission/evidence/adapters qualify.
Task6's MT-supplied adapter dependency must be split into qualified solo adapters
and deferred multi-tenant worker layout, preserving common dispatch contracts.
This is not an executable solo task map yet or permission to remove startup guards.

**Goal:** Deliver authenticated instance ownership, persistent conversation models, atomic run admission and truthful execution evidence on the existing engine.

**Architecture:** Keep the current loop/providers/CoreMem and extend the current execution kernel/store. Persist instance configuration, conversation configuration, admission and receipts in one instance-owned SQLite engine-state file so local admission/configuration changes need no new transaction framework; CoreMem messages remain a separate store with existing idempotent delivery. SQLite transactions contain no model calls, external effects or human waits. This plan does not qualify arbitrary-code isolation or enable hardened MT by itself.

**Tech Stack:** Existing supported Python, FastAPI, Pydantic, SQLite/aiosqlite, pytest, existing scripted-provider fixtures. No new runtime framework.

**Spec:** [#40](../specs/2026-10-07-execution-authority-boundaries-design.md) and [#41](../specs/2026-10-07-execution-receipt-contract-design.md).

## Global Constraints

- This is an explicitly breaking cutover. No automatic migration or backward compatibility is required from the old engine/configuration/storage contracts.
- Never delete, reinterpret, attribute or modify retained customer data as a side effect of adopting the new contracts.
- Configuration belongs to each logical company/assistant instance, even when instances share one server/container.
- Existing conversations retain their saved model when the instance default changes; user selection updates only the addressed conversation.
- The later same-session request is not queued and its prompt is not added to accepted conversation history.
- Automatic replay of uncertain writes is prohibited by default.
- Terminal attempt fields are immutable.
- There is no new mandatory fairness, per-user CPU/memory/storage quota or model-spend control in the first scope.
- Native required, interactive CLI deferred; MU provisioning is separate.
- All testing uses isolated stores and scripted providers, no production/customer credentials, live customer calls or live inference. Do not load the checkout's `.env`.
- Recheck current main, owned files and dirty work before execution/integration. No reset, stash, global cleanup, push, release or merge implied.

## Review Focus

1. Same user name in two companies must not collide in paths, caches, grants or receipts — Tasks 1/2.
2. Two client payloads redacted identically must still reject incompatible request-ID reuse — Task 4.
3. A repeated request after model/profile defaults change must return its original binding — Tasks 3/4.
4. A busy optimistic prompt must never enter CoreMem or run history — Tasks 4/7.
5. A recorded timeout followed by trusted effect evidence must remain a timeout — Tasks 5/6.

---

## File responsibilities and fixed interfaces

New files are narrow domain components, not a new identity/scheduler platform:

- `src/sdk/authority_models.py`: frozen `Actor`, `InstancePolicy`, `ResourceGrant`, `ConversationConfig`, `RunSnapshot`, `AdmissionResult`, `ExecutionOwner` models.
- `src/storage/instance_state.py`: `InstanceState(path: Path)` with fresh version-2 schema and domain operations only. Use the same path for `SQLiteReceiptStore`; no public generic transaction/unit-of-work API.
- `src/http/authority.py`: authenticated identity/membership -> `Actor`; no trust in request-supplied roles/owners.
- `src/sdk/authority_policy.py`: access/dispatch policy, using actual instance and infrastructure restrictions.
- `src/sdk/run_observation.py`: authorized snapshots and bounded transient stream delivery; receipt facts remain durable.
- `src/sdk/execution_evidence.py`: trusted observation admission and derived late-evidence assessment.
- `tests/refactor/conftest.py`: synthetic `red`/`blue` instances, `ceo`/`staff` users, `test:model-x`/`test:model-y`, registered scripted providers and private/granted resources; empty isolated environment and deny external network. Use existing integration fake-provider semantics, not production fixture mode.

Reuse `CanonicalModel` from `src/sdk/run_models.py`, `AgentProfile` from `src/sdk/agent_profile.py`, `DataPaths` from `src/storage/paths.py`, and existing execution enums/store errors rather than creating duplicate types. All I/O-backed interfaces below are `async def` and awaited; pure model/policy projections remain synchronous. `InstanceState`'s synchronous constructor only binds the file path.

`Actor`: `instance_id`, `user_id`, `key_id`, `role` (`owner|admin|staff`), `scopes`, `membership_revision`.
`InstancePolicy`: `instance_id`, `revision`, `default_model`, allowed models/providers/tools/destinations and operational-retention setting; restrictions are intersections, never unions that broaden infrastructure policy.
`ResourceGrant`: resource ID/kind, owner scope (`user|team|company`), owner ID, read/use/maintain principals and revision. Keep ownership separate from all three permissions.
`ConversationConfig`: instance/session/owner IDs, canonical model, revision.
`RunSnapshot`: instance/actor/session/run IDs, conversation revision, canonical model, profile/tool/skill revision references, policy/grant revisions, admitted server timestamp; no secret values.
`AdmissionResult`: `status` (`accepted|existing|busy`), addressed `run_id`, `snapshot`; only `accepted` permits a new run owner.
`ExecutionOwner`: run ID, owner ID, generation; identity alone does not permit dispatch.

One engine-state database per opaque instance ID contains instance policy, company membership/team membership/resource grants, conversation configuration, run admissions/owners and existing execution receipt tables. Mutable user payload files/CoreMem stay outside this privileged database. Fresh layout: `Instances/{opaque_instance_id}/private/engine-state.db`; users' writable resources under `Instances/{opaque_instance_id}/Users/{opaque_user_id}`. IDs used in paths are opaque stored IDs, never raw OIDC subjects, names or email addresses. This is a fresh layout, not automatic migration.

Existing `TenancyStore` permits one hierarchical membership and does not redirect user paths: do not silently reuse it as the new many-team authority store. Retire its authority use on the new-contract route; keep unrelated billing/legacy analysis behavior outside the cutover until explicitly updated.

## Verification runner used by every task

Run new tests through `scripts/verify_refactor.py`, created in Task 1: standard-library subprocess launcher, fresh temporary data/credential roots, empty `.env` shadow only in the isolated worktree if required, explicit test environment, external-network deny guard and bounded process-tree shutdown. It prints pytest summary **and process exit**; it removes only artifacts it created. Invoke the already installed project Python/pytest without `uv sync` or downloads.

Syntax: `python3 scripts/verify_refactor.py -- tests/refactor/test_NAME.py -q`.
Expected RED is the named new assertion/import failure, not credentials, missing dependencies or network. Expected GREEN is all selected assertions passing and exit 0. Every task commits only its named files after GREEN and `git diff --check`; unrelated inherited diagnostics are reported, not silently fixed.

### Task 1: Fresh instance authority and identity binding

**Files:** Create the authority models, instance state, HTTP authority and verification runner above; modify `src/http/auth/resolver.py`, `src/auth/resolver.py`, `src/auth/keys.py`, `src/auth/oidc.py`, `src/http/auth/__init__.py`, `src/http/main.py`, `src/http/routers/auth_keys.py`, `src/storage/paths.py`; create `tests/refactor/test_instance_authority.py` and fixtures.

**Interfaces:** Produce `resolve_actor(request: Request, state: InstanceState) -> Actor`, `InstanceState.initialize() -> None`, `InstanceState.authorize(identity: UserIdentity, instance_id: str) -> Actor`, and `instance_user_paths(actor: Actor) -> DataPaths`. Add verified issuer/subject/key-instance binding to `UserIdentity`; company membership comes from protected state, not an `admin` string in a client body.

- [ ] **RED tests:** `test_equal_user_names_have_distinct_roots`, `test_caller_cannot_assert_other_instance`, `test_multiple_team_memberships`, `test_shared_key_is_not_person_identity`, `test_old_layout_refused_without_mutation`.
  Assertions: `red_paths != blue_paths`; forged instance raises permission error; staff joins two teams without becoming another tenant; shared key has no individual MT actor; byte snapshots of incompatible old files remain equal after refused initialization. Verify solo setup creates one explicit instance/user without multi-tenant provisioning.
  Required assertions within those named tests:
  ```python
  assert red_paths.root != blue_paths.root
  assert retained_bytes_after == retained_bytes_before
  assert len(staff_team_ids) == 2
  ```
- [ ] Run the new test file through the verification runner and confirm named RED failures.
- [ ] Implement frozen models, fresh schema/version check, opaque path mapping and authenticated membership resolution. Reject malformed IDs/ambiguous membership before filesystem creation. SL launch identity must be explicitly provisioned/authenticated; no MT/MU fallback to `default_user`, shared key or `SOLO_BYPASS`. Inventory every HTTP/WS/SDK/messaging ingress and background/callback route, including aliases/internal/localhost routes. Add parametrized guards proving private routes derive actor context rather than accepting body/header user/role assertions. Public exemptions must reveal no private state. Deployment key administration is infrastructure authority, not company-admin/private-content access.
- [ ] Run the new file plus `tests/api/test_auth_binding_b3.py`, `tests/api/test_ws_auth_fail_closed.py`; preserve relevant invariants while updating intentional contract assertions. Require exit 0.
- [ ] Commit: `feat: bind authenticated actors to fresh instance authority`.

### Task 2: Instance policy, resource ownership and profile permissions

**Files:** Create `src/sdk/authority_policy.py`, `src/http/routers/instances.py`, `src/http/routers/resource_grants.py`, `tests/refactor/test_resource_authority.py`; modify `src/storage/instance_state.py`, `src/http/routers/profile.py`, `src/http/routers/subagents.py`, `src/sdk/tools_core/subagent.py`, `src/sdk/coordinator.py`, `src/sdk/profile_loader.py`.

**Interfaces:** Produce `require_resource(actor: Actor, resource_id: str, permission: Literal['read','use','maintain'], state: InstanceState) -> ResourceGrant`; `require_instance_admin(actor: Actor, state: InstanceState) -> None`; `InstanceState.patch_policy(actor: Actor, expected_revision: int, patch: dict[str, object]) -> InstancePolicy`; `resolve_policy(actor: Actor, state: InstanceState, infrastructure: InstancePolicy) -> InstancePolicy`.

- [ ] **RED tests:** `test_ceo_staff_private_access_is_symmetric`, `test_team_maintainer_can_edit_not_read_private`, `test_company_admin_cannot_edit_private_profile`, `test_red_policy_update_does_not_change_blue`, `test_infrastructure_deny_wins`, `test_suspension_does_not_grant_content`, `test_membership_revocation_invalidates_grants`.
  Assertions: private cross-user reads/edits raise permission error; explicit maintainer edits shared profile; company-policy revision only changes red; forbidden provider remains forbidden; suspended private content is still private.
  ```python
  assert private_read_response.status_code == 403
  assert private_edit_response.status_code == 403
  assert shared_maintainer_response.status_code == 200
  assert blue_policy_after == blue_policy_before
  ```
- [ ] Run `test_resource_authority.py`, confirm RED.
- [ ] Implement revisioned policy/grant APIs and owner/maintainer checks. Proposed routes: `GET/PATCH /v1/instance/config`, `GET/PATCH /v1/resources/{resource_id}/grants`; require expected revision for edits. Use resource IDs, not filesystem paths; no membership/role endpoint invents an identity provider. Pin immutable definition assets before run/launch, and prohibit authority expansion through profile edit.
- [ ] Run the new tests plus profile reload/subagent capability/preflight tests through the runner; require exit 0.
- [ ] Commit: `feat: enforce instance policy and explicit resource maintenance`.

### Task 3: Persistent conversation models and resolved subagent models

**Files:** Create `src/sdk/run_configuration.py`, `src/http/routers/conversation_config.py`, `tests/refactor/test_conversation_models.py`; modify instance state, `src/sdk/runner.py`, `src/sdk/coordinator.py`, `src/sdk/subagent_work_queue.py`, `src/subagent/schedule_service.py`, `src/subagent/scheduler.py`, `src/http/routers/settings.py`, `src/config/user_settings_service.py`.

**Interfaces:** Produce `InstanceState.create_conversation(actor: Actor, session_id: str, model: CanonicalModel | None) -> ConversationConfig`, `InstanceState.patch_conversation(actor: Actor, session_id: str, expected_revision: int, model: CanonicalModel) -> ConversationConfig`; `resolve_run(actor: Actor, conversation: ConversationConfig, state: InstanceState, profile: AgentProfile) -> RunSnapshot`; `resolve_subagent_model(profile: AgentProfile, parent: RunSnapshot | None, instance_default: CanonicalModel) -> CanonicalModel`.

- [ ] **RED tests:**
  ```python
  # Helpers use the public interfaces above and Task 1 fixtures.
  assert old_conversation.model == 'test:model-x'  # after default -> model-y
  assert new_conversation.model == 'test:model-y'
  assert reopened_conversation.model == 'test:model-y'  # explicit saved change
  assert unchanged_other_conversation.model == 'test:model-x'
  assert resolve_subagent_model(unpinned_profile, parent_x, 'test:model-y') == 'test:model-x'
  assert resolve_subagent_model(pinned_y_profile, parent_x, 'test:model-x') == 'test:model-y'
  ```
  Add named tests for stale expected revision (409), model change during active run, explicit forbidden/unavailable model (no provider fallback), standalone scheduled admission snapshot, and failed persistence leaving the old choice unchanged.
- [ ] Run `test_conversation_models.py`, confirm RED.
- [ ] Implement `GET/PATCH /v1/conversations/{session_id}/config` and atomic default capture/config revision in engine-state storage. Remove new-contract use of global user `default_model` updates; do not silently convert old preferences. Existing main profile model fields cannot bypass these semantics. Pass parent snapshot to coordinator/work queue at admission; scheduled launches capture effective input once, not at delayed execution.
- [ ] Run model/profile/coordinator/new conversation tests; assert secret-free snapshot serialization and exit 0.
- [ ] Commit: `feat: persist conversation model choices and launch snapshots`.

### Task 4: Durable run admission, request identity and execution fencing

**Files:** Modify instance state, `src/sdk/session_worker.py`, `src/sdk/run_service.py`, `src/storage/messages.py`, `src/sdk/execution_models.py`, `src/sdk/execution_store.py`; create `tests/refactor/test_run_admission.py`.

**Interfaces:** Produce `InstanceState.admit(actor: Actor, session_id: str, request_id: str, intent: dict[str, object]) -> AdmissionResult`, `claim_run(actor: Actor, run_id: str, owner_id: str) -> ExecutionOwner`, `check_owner(owner: ExecutionOwner) -> None`, `finish_run(owner: ExecutionOwner) -> None`. Request fingerprinting precedes redaction and excludes actual secret values by referring to bound secret references/versions; stale conflicts use domain errors mapped to 409, unauthorized reads to 403.

- [ ] **RED tests:** `test_two_connections_admit_one_turn`, `test_busy_prompt_not_ingested`, `test_retry_after_default_change_returns_original_run`, `test_redacted_equal_inputs_do_not_alias`, `test_replaced_worker_cannot_dispatch`, `test_stale_cancel_cannot_cancel_successor`.
  Assertions: sorted result statuses equal `['accepted','busy']`; only first prompt exists in messages; retry run/snapshot IDs equal original; changed private effective input raises conflict; stale owner generation raises permission/state error. Use separate SQLite connections/processes, not only `asyncio.gather` around one lock.
  ```python
  assert sorted(result.status for result in results) == ['accepted', 'busy']
  assert repeated.run_id == original.run_id
  assert repeated.snapshot == original.snapshot
  assert accepted_prompts == [first_prompt]
  ```
- [ ] Run `test_run_admission.py`, confirm RED.
- [ ] Implement admission/config snapshot/owner tables in the existing instance database using SQLite transactions and uniqueness constraints. Same request lookup uses its first-admission binding before re-resolving defaults. Integrate `MessageStore.add_message_once` only after accepted admission; delivery failure prevents dispatch and remains recorded, without claiming cross-store atomicity. No new outbox/UOW framework.
- [ ] Run new admission tests and conversation persistence tests; exercise two processes and crash before message delivery/dispatch; require exit 0.
- [ ] Commit: `feat: atomically admit and fence session execution`.

### Task 5: Receipt context, honest termination and immutable evidence

**Files:** Modify `src/sdk/execution_models.py`, `src/sdk/execution_store.py`, `src/sdk/postgres_receipt_store.py`, `src/sdk/execution_kernel.py`; create `src/sdk/execution_evidence.py`, `tests/refactor/test_receipt_fidelity.py`.

**Interfaces:** Extend `ExecutionRequest` with actor/run snapshot/executor/definition/authority bindings. Add `executor_state: ExecutorState` and `termination_reason: str | None` to `ExecutionCompletion`; default terminal is allowed only for normal confirmed completion. Produce `EvidenceAssessment` (`effect_state: EffectState`, `verification_state: VerificationState`, `supporting_observation_ids: tuple[str, ...]`) and `assess_evidence(receipt: Receipt) -> EvidenceAssessment`; `accept_observation(actor: Actor, receipt: Receipt, observation: Observation, verified_source: str) -> Receipt`. Verified source is produced by a trusted adapter, never accepted from client/stdout. Existing ReceiptStore methods remain the persistence boundary.

- [ ] **RED tests:** `test_wait_timeout_does_not_claim_thread_stopped`, `test_failed_executor_can_have_applied_effect`, `test_stdout_cannot_assert_authoritative`, `test_conflicting_observation_id_rejected`, `test_late_applied_evidence_keeps_timeout`, `test_cross_tenant_evidence_rejected`.
  Assertions: timeout effect/executor remain unknown when unproved; original outcome stays `Outcome.TIMED_OUT` after assessment becomes `APPLIED`; conflicting observation payload raises conflict; terminal mutation raises `ReceiptStateError`. Store/restore/rebuild produce equivalent evidence.
  ```python
  assert original.outcome is Outcome.TIMED_OUT
  assert original.executor_state is ExecutorState.UNKNOWN
  assert assessment.effect_state is EffectState.APPLIED
  assert receipt_after.outcome is original.outcome
  ```
- [ ] Run the new file, confirm RED.
- [ ] Update kernel interruption handling to consume explicit adapter termination evidence, not infer physical termination from a cancelled await. Validate observation content against existing identities before duplicate acceptance. Implement deterministic derived assessment without mutating original attempt. Keep current enum vocabulary. Apply fresh-schema changes consistently to SQLite/Postgres adapters; no legacy data migration. Set the qualified SQLite engine-state durability policy explicitly (`journal_mode=WAL`, `synchronous=FULL`) and test crash/reopen behavior. Document that process-crash tests do not independently prove host/power-loss durability.
- [ ] Run execution models/kernel/store/public API tests and new fidelity tests. Use database doubles for Postgres unit coverage; actual external database execution requires separate authorization. Require exit 0.
- [ ] Commit: `feat: preserve truthful executor and effect evidence`.

### Task 6: Unified governed adapters and safe reconciliation

**Files:** Modify `src/sdk/governance.py`, `src/sdk/governance_operations.py`, `src/sdk/tools.py`, `src/sdk/loop.py`, `src/sdk/tools_custom.py`, `src/sdk/tool_index.py`, `src/sdk/coordinator.py`, `src/sdk/tools_core/mcp_bridge.py`, `src/sdk/run_service.py`; create `src/sdk/execution_dispatch.py`, `tests/refactor/test_execution_adapters.py`, `tests/refactor/test_execution_recovery.py`.

**Interfaces:** Produce `authorize_dispatch(actor: Actor, request: ExecutionRequest, state: InstanceState, owner: ExecutionOwner) -> ExecutionRequest`; `dispatch(request: ExecutionRequest, executor: ExecutionExecutor, kernel: ExecutionKernel) -> Receipt`; `reconcile(actor: Actor, receipt: Receipt, owner: ExecutionOwner) -> EvidenceAssessment`. Preserve `ExecutionExecutor = Callable[[ExecutionRequest], Awaitable[ExecutionCompletion]]`. Original all-mode plan: actual worker/credential adapters are supplied by the MT-boundaries plan, not bypassed here. Under solo-first staging, revise this dependency explicitly: qualify SL/SR adapters behind the same dispatch/kernel contracts without requiring MT's multi-tenant layout. No legacy direct-execution bypass or unqualified adapter fallback; exact solo adapter design/tasks still require review.

- [ ] **RED tests:** parametrized `test_all_adapters_obey_admission` for builtin/local write, command, reconstructed command, MCP, sync and async job; `test_revoked_approval_refuses_dispatch`, `test_effect_then_record_failure_no_replay`, `test_safe_annotation_not_replay_authority`, `test_external_key_and_input_stable`, `test_changed_environment_blocks_replay`, `test_background_work_has_bounded_authority`.
  Assertions: failing durable intent invokes executor zero times; built-in revision evidence may establish applied; command success alone does not; async acknowledgement is not terminal; stale grants refuse; uncertain external write is called once even after reopen; effect-free replay still revalidates authority.
  ```python
  assert executor_calls_after_intent_failure == 0
  assert external_write_calls_after_restart == 1
  assert unverified_command_receipt.effect_state is EffectState.UNKNOWN
  assert background_actor.scopes == admitted_background_actor.scopes
  ```
- [ ] Run both new files, confirm RED.
- [ ] Route every catalog/ad-hoc execution through common dispatch checks and existing kernel. Unknown custom effects are not `NOT_APPLICABLE` merely due to annotations. Move non-cooperative code to the boundary adapter rather than force-stop threads. Implement source-verified external operation observations, and explicit reconciliation using stable handles; no implicit replay based solely on authored flags. Add `test_foreground_children_drain_before_parent_stopped` and `test_compensation_is_new_governed_action`: parent completion accounts for owned child lifetimes, while compensation has a new linked request/receipt. Stale-worker evidence is rejected; new trusted external evidence can be accepted under the current fenced reconciliation owner. Recovery owner fencing and storage failure stop new effects, not pretend to undo old ones.
- [ ] Run governance/local/external-operation/callback, tool dispatch, subagent and execution regression slices; require exit 0. Inspect source for raw execution paths bypassing dispatch and add their regression tests before GREEN.
- [ ] Commit: `refactor: unify governed execution and reconciliation`.

### Task 7: Native-facing identity, snapshot/stream and operational projection

**Files:** Create `src/http/routers/identity.py`, `src/http/routers/run_observation.py`, `src/http/routers/operations.py`, `src/sdk/run_observation.py`, `src/sdk/operational_projection.py`, `tests/refactor/test_run_observation.py`, `tests/refactor/test_operational_privacy.py`; modify `src/http/main.py`, `src/http/routers/v1.py`, `src/http/routers/conversation.py`, `src/http/routers/ws.py`, `src/http/routers/webhooks.py`, `src/http/models.py`, `src/sdk/run_models.py`.

**Interfaces:** New contract version `2`: `GET /v1/identity` returns authenticated instance/user/mode/version and granted configuration abilities (no secrets). `GET /v1/runs/{run_id}` returns private authorized snapshot; `GET /v1/runs/{run_id}/events` sends SSE snapshot then incremental events, replacing overflowing transient deltas with a new snapshot. Existing POST run/message routes return explicit busy (409) with `active_run_id` and observation links; native follows them. Keep WS as an adapter over these semantics. `project_operation(receipt: Receipt) -> dict[str, object]` is an allowlist, not recursive best-effort redaction.

- [ ] **RED tests:** `test_second_client_attaches_without_new_execution`, `test_disconnect_is_not_cancel`, `test_revoked_reader_stops_delivery`, `test_wrong_tenant_gets_no_snapshot`, `test_slow_reader_gets_replacement_snapshot`, `test_admin_projection_excludes_private_fields`, `test_retention_does_not_delete_private_receipts`, `test_version_mismatch_fails_clearly`.
  Assertions: both readers observe same run ID/content; executor count is one; optimistic rejected text is absent; current read revocation stops delivery; admin JSON lacks prompt/SQL/path/query/name/secret fingerprints; only operational projection retention is purged.
  ```python
  assert first_observer.run_id == second_observer.run_id
  assert execution_count == 1
  assert rejected_prompt not in accepted_history
  assert post_revocation_delivery == []
  assert private_sentinel not in serialized_admin_projection
  ```
- [ ] Run new files, confirm RED.
- [ ] Implement authenticated version/identity discovery, durable lifecycle snapshot and explicitly transient token stream. Recheck rights before subsequent delivery. Audit operational-view reads, use opaque private names, and keep telemetry exporters opt-in. Cancellation addresses exact run ID. Native authentication does not depend on desktop-only bootstrap for remote identity.
- [ ] Run REST/SSE/WS/auth/governance regression slices, plus two observer/restart tests; require exit 0. Apply the same Actor/RunSnapshot checks to `src/http/routers/webhooks.py`, the existing ingress used by external messaging/trigger adapters, without building a new messaging transport. Add a synthetic webhook/messaging ingress test for actor mapping, busy rejection and no adapter-only authority bypass; no unbound legacy webhook secret may assert a new-contract actor.
- [ ] Commit: `feat: expose authorized run observation and redacted operations`.

### Task 8: Core-contract qualification and breaking-change documentation

**Files:** Create `tests/refactor/test_core_contract_acceptance.py`, `docs/audits/2026-10-07-core-contract-qualification.md`; modify `DEPLOYMENT.md`, `docs/builder-deployment-guide.md`, `CHANGELOG.md`, `AGENTS.md` only after preceding tasks qualify.

**Interfaces:** Deliver the version-2 server contract consumed by the native plan and the dispatch/state interfaces consumed by the MT plan. Hardened MT stays unavailable until that plan qualifies its backend. Document the fresh-store cutover and that SL helper/native are not shipped by core API tests alone.

- [ ] **RED tests:** end-to-end scripted main+subagent run, two process owners, forbidden model, company default update across old/new conversations, storage failure after external effect and late evidence. Assert the full spec matrix, including no secret traces or rejected prompt delivery.
  ```python
  assert persisted_model_after_reopen == 'test:model-y'
  assert forbidden_model_provider_calls == 0
  assert reconciled_attempt.outcome is Outcome.TIMED_OUT
  assert cross_instance_response.status_code == 403
  ```
- [ ] Run new acceptance file, confirm missing integration assertions fail.
- [ ] Complete missing wiring only in the owning modules; update docs with actual contract/compatibility and minimum operations. Measure repeated cold/warm dispatch baseline/current and report overhead, no fabricated latency/fairness gate.
- [ ] Run all project Python tests through the offline runner, changed-source Ruff and scoped mypy; compare inherited diagnostics with the baseline. Require process exit 0 for claimed test success and publish exact evidence. Do not repair unrelated inherited issues silently.
- [ ] Commit: `docs: qualify core authority and receipt contracts`.

## Completion boundary

This plan delivers a testable server contract and lifecycle core. It does **not** alone ship native DMG, prove Linux worker isolation or establish MU containment. Complete the linked MT/native plans before corresponding public readiness claims.
