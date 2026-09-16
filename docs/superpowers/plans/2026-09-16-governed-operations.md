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

- [ ] Define immutable dispatch envelope and operation-scoped callback capability.
- [ ] Implement authenticated idempotent progress/complete/fail callbacks.
- [ ] Persist proposal/tool/arguments/manifest binding and reject mismatched callbacks.
- [ ] Reconciliation marks unresolved dispatched work `uncertain`; never blind-retries a side effect.

## Phase 4: Verification

- [ ] Integration test: approved long-running executor returns promptly, progress survives disconnect, duplicate approval creates no second job, restart reconciliation records terminal or uncertain.
- [ ] Full suite, migration review, security review, owner release approval.
