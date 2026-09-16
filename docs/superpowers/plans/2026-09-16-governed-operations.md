# Governed Durable Operations Plan (#21)

**Spec:** `docs/superpowers/specs/2026-09-16-governed-operations-design.md`

## Phase 0: Rename the subagent queue

- [ ] Rename `WorkQueueDB` to `SubagentWorkQueueDB` and `work_queue.py` to `subagent_work_queue.py`; update imports/tests with no compatibility alias (unreleased internal API).
- [ ] Prove subagent lifecycle behavior remains unchanged.

## Phase 1: Durable operation ledger

- [ ] Add governance-db migrations for operations/events and typed operation models.
- [ ] Add atomic `approve_and_create_operation`: one consumed proposal, one operation under duplicate approvals.
- [ ] Add append-only ordered events, terminal idempotency, cancellation request, and `uncertain` reconciliation transitions.
- [ ] Test user isolation and crash-safe state transitions.

## Phase 2: Async governance contract

- [ ] Add explicit `ToolAnnotations.execution_mode: sync|async`; default sync.
- [ ] Preserve synchronous approval/execution contract unchanged.
- [ ] Async approval returns accepted operation without waiting for executor runtime.
- [ ] Add operation read/cancel APIs with existing governance authorization.

## Phase 3: External executor MVP

### 3a: Durable external-operation state
- [ ] Define trusted `external_http` metadata, immutable dispatch envelope, dispatch outbox, and hashed operation-scoped callback capability.
- [ ] Persist proposal/tool/arguments/manifest binding; claim/retry dispatch with one idempotency key; reject unsafe retry/replay states.
- [ ] Implement store-level ordered progress, idempotent terminal completion/failure, callback binding checks, and `uncertain` reconciliation transitions.

### 3b: HTTP dispatch and callbacks
- [ ] Add the bounded dispatcher and executor-authenticated progress/complete/fail callback routes.
- [ ] Add operation read/cancel integration and reconciliation/startup wiring.
- [ ] Prove external dispatch receives one immutable envelope; duplicate callback/approval/delivery does not create a second operation.

## Phase 4: Verification

- [ ] Integration test: approved long-running executor returns promptly, progress survives disconnect, duplicate approval creates no second job, restart reconciliation records terminal or uncertain.
- [ ] Full suite, migration review, security review, owner release approval.
