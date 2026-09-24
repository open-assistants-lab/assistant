# Subagent Architecture Simplification and Component Review

**Status:** Decision record; implementation, verification, and read-only review complete on `feat/subagent-capability-reliability`
**Date:** 2026-09-24  
**Scope:** Subagent execution and its boundaries with the SDK loop, middleware, tools, skills, and persistence

## 1. Purpose

Assess whether the subagent reliability work has added more architecture than the product needs, and define a conservative simplification direction. The goal is not to remove safety or durability. It is to make each safety property have one clear owner and avoid maintaining multiple representations of the same state.

This review and its approved decisions were implemented incrementally in Tasks 1–7, including final verification and a read-only follow-up review. Deployment assumptions remain SQLite per user, one process per user store, and local-first desktop as a primary target; HTTP background completion behavior is preserved.

## 2. Executive Assessment

The safety model is justified; the implementation has some avoidable representation and compatibility complexity. The current design combines:

1. profile selection policy (`AgentProfile.tools`/`skills` plus `runtime-policy.json`),
2. launch-time decisions (`SubagentLaunchPlan`),
3. the effective runtime tool set and allowed skill set,
4. a work-queue task snapshot and terminal fields,
5. a second durable completion-event record, and
6. parent-session message deduplication.

Each has a defensible purpose. The implementation now makes the manifest ID content-derived and parent-message application indexed/idempotent. Result/status and outbox payload fields still overlap; they remain until consumers, retention, and replay reconstruction support a safe reduction.

**Outcome:** No broad rewrite was performed. Strict preflight, immutable run-scoped capabilities, authoritative SQLite task state, and durable outbox delivery remain. Tasks 2–6 implemented explicit migration, child runtime-ask termination, idempotent parent-message application, canonical manifests, and workspace/file-tool scope. `invoke`, outbox payload copies, and general loop/middleware integration were retained where removal or consolidation lacked compatibility or parity evidence.

## 3. Requirements That Must Remain True

1. A child cannot start if a declared capability is missing, disabled, forbidden, denied, or known to require approval before launch.
2. The child receives only the immutable effective tool and skill manifest recorded for that run; no hidden mandatory tools or post-preflight silent narrowing.
3. The main agent's governance semantics do not change.
4. Argument-sensitive permissions and administrator `deny` remain authoritative at tool-call time. A profile declaration or launch manifest is not a blanket approval for arbitrary arguments.
5. Task lifecycle status is durable and authoritative. Result-level reasons such as `blocked` and `uncertain` remain distinguishable without implying every rejected launch creates a task row.
6. Every routable terminal background-task outcome is replayed into the parent conversation after restart and applied idempotently; without a parent session, durable task status remains available without a message.
7. Existing profiles are migrated once per profile with a visible diagnostic; omitted/ambiguous tools never widen to all native tools.

## 4. Current Component Review

| Component | Current responsibility | Assessment | Simplification direction |
|---|---|---|---|
| `AgentLoop` | General ReAct execution plus subagent cancellation checks, skill allowlist enforcement, progress hooks, and doom-loop handling | Streaming and non-streaming pre-dispatch cancellation and skill-boundary contracts are covered by parity tests. No safe duplicate-hook consolidation was demonstrated. | Retain the existing integration; do not introduce a run-control framework without new evidence. Main-agent behavior is unchanged. |
| Middleware / HITL | `HITLMiddleware` evaluates actual tool arguments and can deny, allow, or create an approval proposal | Runtime argument checks remain authoritative. A child argument-specific `ask` can occur after launch even when tool-level preflight passed. | Child `ask` terminates with `blocked`/`approval_required` and no child-owned pending proposal. Main-loop proposal behavior is unchanged and regression-tested. |
| Capability planner | Resolves declared tools/skills, enablement, permissions, and diagnostics | Fail-closed boundary. `plan_id` is now the full SHA-256 of canonical resolved content: user/workspace/agent identity, selection mode, requested/effective names, and deterministic decision fields. | Retain one canonical launch-manifest concept. Transient exception text is excluded from identity; rejected plans are not persisted as launchable tasks. |
| Tool registry / tools | Native registry plus profile-level tool selection; the subagent builder must not add hidden tools | Child `SAFE_DEFAULT` is exactly `files_list`, `files_read`, `files_glob_search`, and `files_grep_search`. Child file/skill tool schemas hide identity inputs and bind user/workspace; file paths and outside-root symlinks are confined. | Keep broader tools behind explicit allowlists and normal governance. Custom/MCP child-manifest support remains out of scope. |
| Skills | Skill catalog plus `skills_load`; child context restricts loads to the launch manifest | The per-run allowlist remains frozen across `skills_reload`; child skill tools receive bound identity. Skill contents themselves are not snapshotted. | Keep name-level restriction. Content/version hashing remains open only if reproducibility becomes a product requirement. |
| Profile policy | `AgentProfile` plus versioned `runtime-policy.json` distinguishes omitted tools, empty tools, and allowlists | The sidecar preserves omission-vs-empty semantics. Legacy interpretation is migration-only and fails closed on malformed/unknown policy. | Keep the sidecar until the upstream profile schema can represent field presence. Preserve visible per-profile migration diagnostics. |
| Coordinator / launch APIs | `start`, synchronous `delegate`, and deprecated `invoke` validate, preflight, snapshot, and run | No in-repository production caller of `SubagentCoordinator.invoke` was found, but external consumers are unknown. | Keep `invoke` deprecated with its historical task-ID return contract pending explicit compatibility evidence. Do not remove based only on repository search. |
| Work queue | SQLite task state, frozen config/manifest, terminal result, timeout/cancel recovery | Appropriate source of truth for asynchronous work and durable status. | `work_queue.status` owns lifecycle; `result` owns output/usage/result-level reason; `launch_plan` owns the frozen capability/workspace snapshot. Keep mirrored terminal fields until all API/recovery consumers are proven unnecessary. |
| Completion outbox / bus | Durable notification, replay, callback acknowledgement, then parent-message deduplication | A lock on the shared per-user queue serializes in-process drains across workspace coordinators. A crash after bus publication but before acknowledgement can still repeat a live WebSocket event; conversation-message application is idempotent by indexed `subagent-completion:{task_id}` key. | Preserve the outbox and payload copies until replay-from-task reconstruction and a retention policy are proven. No conversation-history scan remains. Keep at-least-once semantics explicit; socket-level exactly-once is not claimed. |
| `SubagentContext` | Cancellation, instructions, progress, doom-loop state, and allowed skills | Useful per-run state, but it combines control-plane signals and execution policy. | Keep one per-run context for now. Do not add separate state stores for each signal. If the loop integration is redesigned, move as a whole with parity tests rather than splitting ownership. |

## 5. Contracts Verified by Tasks 1–7

- Legacy policy is migrated once: explicit non-empty tools -> `ALLOWLIST`; explicit empty -> `NONE`; omitted/ambiguous -> `SAFE_DEFAULT`; malformed/unknown policy fails closed with a diagnostic.
- A runtime argument-specific child `ask` can occur after launch. It now terminates with result-level `blocked` and `approval_required`, without creating a child-owned pending proposal. Main-agent HITL semantics remain unchanged.
- The launch ID is a full content-derived SHA-256 over canonical resolved manifest fields; task execution and replay consume the stored manifest. A malformed/legacy task lacking frozen tool and skill lists is failed closed.
- Queue status owns lifecycle; result owns output/usage/result-level reason; launch plan owns frozen execution capabilities and requested workspace; outbox owns delivery state. Duplicated event payload fields remain pending proven task-row replay and an explicit retention contract.
- Conversation completion application is at-least-once plus a unique indexed message delivery key; the old history scan has been removed.
- SAFE_DEFAULT is the four workspace file tools, whose user/workspace identity is coordinator-bound. Child paths are workspace-relative and symlinks escaping that workspace are excluded.
- Coordinator instances are cached by `(user_id, requested_workspace_id)` so reuse cannot retain another request's workspace. Profile and work-queue storage remain user-level; both cache insertion orders are regression-tested through launch preflight.

## 6. Implemented Architecture Contract

### 6.1 Profile configuration

A profile expresses requested capabilities; the versioned `runtime-policy.json` sidecar preserves omitted-vs-empty selection semantics. Migration is one-time and visible: explicit non-empty tools -> `ALLOWLIST`, explicit empty -> `NONE`, omitted/ambiguous -> `SAFE_DEFAULT`; malformed or unknown policy fails closed.

### 6.2 Launch boundary

All launch entry points (`start`, `delegate`, and deprecated `invoke`) perform enablement/definition validation and strict preflight before queue/provider/LLM work. The contract is:

1. profile and subagent enablement validation,
2. strict tool/skill resolution against current registries and capability policy,
3. permission preflight without weakening administrator deny,
4. construction of a frozen effective manifest,
5. rejection before queue/provider/LLM side effects if any required capability is unavailable,
6. persistence of an execution-profile snapshot plus the effective manifest and enough requested-profile metadata to explain resolution. The current implementation copies effective tools/skills into the stored profile, so it should not be described as an unchanged copy of the user's original profile.

The implementation hashes canonical resolved content with SHA-256, including user/requested-workspace/agent identity, selection mode, requested/effective names, and deterministic capability decisions. The frozen manifest stored at insertion is authoritative for execution and replay; a task missing effective tool/skill snapshots fails closed. Policy changes do not mutate an already-stored manifest, while actual tool calls still pass runtime governance.

### 6.3 Runtime enforcement

The child receives exactly the stored manifest. Skill loads are restricted to its frozen names, including after `skills_reload`. Child file and skill tools hide model-visible identity fields and bind user/workspace from the coordinator. The four SAFE_DEFAULT file tools reject absolute and parent-traversing paths; list/glob/grep exclude symlinks resolving outside the requested workspace. Governance still evaluates actual arguments; runtime `ask` yields a typed blocked result rather than task success.

### 6.4 Lifecycle and delivery

SQLite task state remains authoritative. Terminal transition and outbox insertion are atomic. Outbox publication is at-least-once; the parent consumer applies messages idempotently using `subagent-completion:{task_id}` and a unique indexed key in the conversation store. This is not cross-database exactly-once. Completion payload copies are retained because there is no retention policy and reduced-payload replay has not been proven.

## 7. Implementation Record

### Task 1 — Contract map and decisions

- Persisted field authority, caller evidence, policy migration, SAFE_DEFAULT breadth, workspace scope, and outbox/message-idempotency behavior are recorded in `docs/audits/2026-09-24-subagent-simplification-inventory.md`.

### Tasks 2–5 — Reliability contracts

- Explicit legacy policy migration, typed child runtime-ask termination, canonical manifests, workspace routing, and indexed idempotent parent-message application are implemented and covered by deterministic tests.

### Task 6 — Evidence-based scope reduction

- `invoke` remains deprecated because external callers are unknown; old `SubagentManager.invoke`/scheduler callers belong to a distinct legacy path.
- SAFE_DEFAULT is exactly the four tested workspace file tools; user/workspace fields are coordinator-bound and outside-root paths/symlinks are rejected or omitted.
- No general loop/middleware consolidation or custom/MCP support was justified. Outbox payload copies remain until retention and replay reconstruction are proven.

Tasks 1–6 found no safe loop/middleware hook consolidation and made none. Streaming/non-streaming cancellation, skill-boundary, and unchanged main-agent HITL behavior remain covered by parity/regression tests.

## 8. Acceptance Criteria

- Each safety property has one documented enforcement owner and at least one regression test.
- The persisted launch manifest exactly matches runtime tool and skill access.
- `ask`/`deny` outcomes remain truthful and do not become false success or bypass approvals.
- Task lifecycle status is unambiguous and terminal transitions cannot be overwritten; result-level reasons (`blocked`, `uncertain`) are represented truthfully without implying every launch rejection has a task row.
- Completion retries do not duplicate parent-visible messages; idempotency uses an indexed key rather than unbounded conversation scanning.
- Profile migration is observable and one-time per profile; ambiguous legacy state never widens authority, and malformed/unknown policy fails closed.
- Every routable terminal event is replayed after restart and applied idempotently; tasks without a parent session retain status without a conversation message.
- SAFE_DEFAULT includes only the approved local/workspace-scoped read-only tools, with runtime-bound identity and path/symlink confinement; broader tools require explicit allowlisting.
- Any removed layer has a demonstrated replacement and tests; no safety behavior is removed merely to reduce line count.
- Measurements exist for default tool breadth and notification dedup cost before changing those policies.

## 9. Decisions and Remaining Open Questions

### Approved decisions

1. **Completion replay:** replay every routable terminal outcome (success, failure, timeout, cancellation, or blocked) into the parent conversation after restart. Without a parent session, retain durable task status without a conversation message.
2. **SAFE_DEFAULT:** implemented as exactly `files_list`, `files_read`, `files_glob_search`, and `files_grep_search`; runtime identity binding and path/symlink confinement have deterministic regression tests. Broader tools require explicit allowlisting.
3. **Child runtime ask:** terminate the child with typed `blocked`/approval-required result and no non-resumable child-owned proposal; main-agent HITL remains unchanged.
4. **Custom/MCP:** keep unsupported in subagent manifests for this simplification; do not add another discovery/authorization path.
5. **Retention:** no task/outbox deletion path was found. Preserve task rows and current event payload until replay-from-task tests prove a smaller reference-only event safe.
6. **Legacy profiles:** one-time migration per profile. Explicit non-empty tools -> `ALLOWLIST`; explicit empty -> `NONE`; omitted/ambiguous -> `SAFE_DEFAULT`, with visible diagnostic and no all-native fallback. Malformed/unknown policy fails closed.

### Verification and remaining open decisions

- Is deprecated `SubagentCoordinator.invoke` used by supported external consumers? Retain it until caller/deprecation evidence supports removal.
- What task/outbox retention policy should replace indefinite retention, if any? No retention changes are included in this pass.
- Skill file contents are not snapshotted; only skill names and authorization decisions are frozen. Add content hashes only if reproducibility requirements justify the additional lifecycle.
- Task 7 verification: focused reliability suites -> 468 passed; completion/coordinator/API regressions after the outbox-lock change -> 163 passed; final full suite -> 3270 passed and 27 skipped. Ruff passed over `src/` and the changed test; scoped mypy passed for the changed source modules; working-tree and committed-range whitespace checks passed. The read-only reviewer confirmed the workspace-cache fix and found no duplicate task execution. A per-user work-queue lock serializes in-process drains across workspace coordinators; the crash-after-publish-before-ack window remains at-least-once.
