# Subagent Architecture Simplification and Component Review

**Status:** Proposal for review; no implementation authorization implied  
**Date:** 2026-09-24  
**Scope:** Subagent execution and its boundaries with the SDK loop, middleware, tools, skills, and persistence

## 1. Purpose

Assess whether the subagent reliability work has added more architecture than the product needs, and define a conservative simplification direction. The goal is not to remove safety or durability. It is to make each safety property have one clear owner and avoid maintaining multiple representations of the same state.

This review is based on the current `feat/subagent-capability-reliability` implementation and the stated deployment assumptions: SQLite per user, one process per user store, and local-first desktop as a primary target. The codebase also supports HTTP deployments, so background-task and notification behavior cannot be dismissed solely as desktop concerns.

## 2. Executive Assessment

The safety model is justified; the implementation has some avoidable representation and compatibility complexity. The current design combines:

1. profile selection policy (`AgentProfile.tools`/`skills` plus `runtime-policy.json`),
2. launch-time decisions (`SubagentLaunchPlan`),
3. the effective runtime tool set and allowed skill set,
4. a work-queue task snapshot and terminal fields,
5. a second durable completion-event record, and
6. parent-session message deduplication.

Each has a defensible purpose, but the contract between them is not yet as small or explicit as it could be. In particular, the plan ID is not a content hash of the resolved plan, result/status data is duplicated, and parent-message idempotency currently scans conversation history.

**Recommendation:** Do not perform a broad rewrite. Preserve strict preflight, the run-scoped capability ceiling, authoritative SQLite task state, and durable outbox delivery where background completion notifications are required. Simplify incrementally, starting with representation ownership and idempotency. Require evidence before removing a layer or changing compatibility behavior.

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
| `AgentLoop` | General ReAct execution plus subagent cancellation checks, skill allowlist enforcement, progress hooks, and doom-loop handling | The common dispatch guard is the right place to stop a cancelled child before a tool runs. However, direct `subagent_ctx` knowledge in the general loop increases coupling. | Keep the common pre-dispatch cancellation and skill-boundary checks. Avoid further subagent-specific branches. In a later, separately tested change, assess whether one optional run-control interface can own these hooks without adding a general framework. Do not move them blindly: streaming and non-streaming paths must retain identical behavior. |
| Middleware / HITL | `HITLMiddleware` evaluates actual tool arguments and can deny, allow, or create an approval proposal | Runtime argument checks are important; launch preflight cannot replace them. The child approval behavior needs a precise contract: an argument-specific `ask` can still occur after launch even if tool-level preflight passed. | Keep main-loop middleware behavior unchanged. Specify and test what a child does when runtime governance returns `ask`: fail/stop with a typed outcome, pause/resume, or another explicit policy. Do not claim preflight guarantees no child-level ask unless that is proven for argument-sensitive policies. |
| Capability planner | Resolves declared tools/skills, enablement, permissions, and diagnostics | Valuable fail-closed boundary. `CapabilityDecision` is diagnostic data; the loop only needs the effective manifest. The current `plan_id` hashes profile name, identity, mode, and declared names, but not the resolved effective set, decisions, or policy versions. | Retain one launch-manifest concept. Make its ID identify the canonical serialized effective manifest and relevant policy/version inputs, or rename it so it is not mistaken for a content identity. Keep detailed rejection diagnostics at the boundary; avoid persisting duplicated diagnostic detail unless audit requirements need it. |
| Tool registry / tools | Native registry plus profile-level tool selection; the subagent builder must not add hidden tools | Exact tool execution is a sound invariant. The current annotation-derived `SAFE_DEFAULT` is broader than the approved local/workspace scope, and some read-only tools expose user/workspace identifiers or user-level data. | Measure actual breadth and compatibility, then use a small curated local, workspace-scoped read-only set. Bind identity/workspace in the child tool wrapper and confine paths/symlinks before enabling file tools. Custom/MCP tools remain unsupported in this simplification. |
| Skills | Skill catalog plus `skills_load`; child context now restricts loads to the launch manifest | The separate skill allowlist is necessary because `skills_load` is a generic tool capable of loading many names. Skills are knowledge, but can still affect behavior and expose context. | Keep the name-level runtime restriction. Ensure `skills_reload` cannot widen a running child's already-frozen allowlist. Define whether skill contents are snapshotted or may change between preflight and load; if they can change, record a content/version hash only if reproducibility is a real requirement. |
| Profile policy | `AgentProfile` plus versioned `runtime-policy.json` distinguishes omitted tools, empty tools, and allowlists | The sidecar is justified while the upstream profile schema cannot represent omission distinctly. A permanent `LEGACY` branch adds ongoing complexity. | Keep the sidecar as the compatibility adapter for now. Add telemetry/diagnostics and a documented migration rule, then decide whether to migrate old profiles or extend the profile schema. Do not move the field into undocumented tags. Remove legacy handling only after inventory and compatibility evidence. |
| Coordinator / launch APIs | `start`, synchronous `delegate`, and deprecated `invoke` each validate, preflight, snapshot, and run | Multiple paths multiply safety review and tests. `invoke` is deprecated but still a callable launch path. | Keep `start` and `delegate` as the supported contract. Audit repository and external callers before removal or delegation. Factor only genuinely shared validation/snapshot logic; avoid a generic launch framework. |
| Work queue | SQLite task state, frozen config/manifest, terminal result, timeout/cancel recovery | Appropriate source of truth for asynchronous work and durable status. There is duplication between status/terminal columns and `SubagentResult`, but the authoritative field is not always obvious. | Declare `work_queue.status` authoritative for lifecycle. Keep result payload for output/usage. Retain terminal reason only if it captures distinctions not expressible by status. Avoid adding more mirrored fields without a consumer. |
| Completion outbox / bus | Durable notification, replay, callback acknowledgement, then parent-message deduplication | Justified if restart-safe parent notification is required. Exactly-once effects across the queue DB and conversation DB cannot be achieved by one SQLite transaction; delivery is effectively at-least-once plus an idempotent consumer. | Preserve outbox semantics, but make the consumer idempotency key explicit and indexed. Replace the broad history scan with a small inbox/delivery ledger or a storage-level uniqueness mechanism. Prefer an outbox row containing task/event identity and routing over duplicating task result data if retention guarantees keep the referenced task available. |
| `SubagentContext` | Cancellation, instructions, progress, doom-loop state, and allowed skills | Useful per-run state, but it combines control-plane signals and execution policy. | Keep one per-run context for now. Do not add separate state stores for each signal. If the loop integration is redesigned, move as a whole with parity tests rather than splitting ownership. |

## 5. Current Contract Gaps to Verify Before Simplifying

These are implementation questions, not reasons for an immediate rewrite:

- The current `LEGACY` planner path uses the profile's declared tool list as its selected set. An empty list historically meant all native tools in the old tool builder, but now resolves to an empty effective manifest. Approved migration is one-time: explicit non-empty list -> `ALLOWLIST`; explicit empty -> `NONE`; omitted or ambiguous -> `SAFE_DEFAULT`, with a visible diagnostic and no all-native fallback.
- Generic `skills_load` preflight checks empty arguments, while `HITLMiddleware` evaluates the actual skill name. An item-level runtime `ask` can therefore arise after launch. The approved child behavior is a typed terminal `blocked`/approval-required result without a non-resumable child proposal; main-agent HITL is unchanged.
- The current plan ID is derived from declarations and mode rather than the fully resolved effective plan. It should not be treated as a unique audit fingerprint until canonicalization is implemented.
- The queue's terminal status, terminal-reason columns, serialized result, and outbox copy have overlapping information. Confirm which fields are consumed by APIs, receipts, and replay before consolidating.

## 6. Target Architecture

### 6.1 Profile configuration

A profile expresses requested capabilities and a versioned selection mode. The runtime policy file remains the compatibility adapter until profile-schema support and old-profile migration are proven. New profiles must preserve the semantic difference between omitted tools and an explicit empty tool list.

### 6.2 Launch boundary

All launch entry points must obey one launch contract and shared resolution semantics. Use a common helper for validation, preflight, and snapshot construction only if it removes real duplication; a new orchestration layer is not a goal by itself. The contract is:

1. profile and subagent enablement validation,
2. strict tool/skill resolution against current registries and capability policy,
3. permission preflight without weakening administrator deny,
4. construction of a frozen effective manifest,
5. rejection before queue/provider/LLM side effects if any required capability is unavailable,
6. persistence of an execution-profile snapshot plus the effective manifest and enough requested-profile metadata to explain resolution. The current implementation copies effective tools/skills into the stored profile, so it should not be described as an unchanged copy of the user's original profile.

The plan ID must be based on the resolved manifest (including effective names and the policy context needed to explain it), not only the profile's declared names. Whether policy-version changes invalidate an already-running manifest is an explicit policy decision; they must not silently mutate the recorded manifest.

### 6.3 Runtime enforcement

The child receives exactly the manifest stored with its task. Tool discovery and execution consume that manifest; they do not infer defaults from an empty list. Skill loading checks the same frozen skill set. Governance still evaluates the actual tool call and arguments. If runtime governance returns `ask`, child behavior must produce a typed, truthful outcome rather than claim task success while work remains pending.

### 6.4 Lifecycle and delivery

SQLite task state remains authoritative. Terminal transition and outbox insertion are atomic. Outbox publication may retry, so the parent-session consumer must be idempotent on a stable event/task ID. Avoid a second source of truth for result/status when a task-row reference is sufficient and task retention supports it.

## 7. Proposed Simplification Work, in Order

### Phase A — Contract map, no runtime changes

- Document every persisted field and who owns it.
- Search repository and packaging boundaries for callers of `invoke` and legacy profile modes.
- Measure SAFE_DEFAULT tool count, schema cost, actual subagent use, and workspace/user scope guarantees.
- Trace the child `ask` path for argument-sensitive policy from tool call through task terminal status.
- Apply the approved restart-notification contract: replay every routable terminal outcome; retain durable status only when no parent session exists.

### Phase B — Low-risk consistency fixes

- Define canonical launch-manifest serialization and stable identity.
- Make lifecycle authority explicit in code/docs: queue status is authoritative; result data is payload.
- Replace parent-message history scanning with indexed idempotency keyed by task/event ID.
- Add tests proving no hidden tool additions, no out-of-manifest skill load (including after reload), and typed runtime-ask outcomes.

### Phase C — Compatibility reduction, only with evidence

- Keep `invoke` unless repository and external caller evidence supports a separately approved removal.
- Migrate each legacy profile once: explicit non-empty tools -> `ALLOWLIST`; explicit empty -> `NONE`; omitted/ambiguous -> `SAFE_DEFAULT`; malformed/unknown policy fails closed.
- Replace annotation-derived SAFE_DEFAULT with the measured, tested local/workspace-scoped read-only set; identity and path/symlink confinement are prerequisites.
- Consolidate task/outbox payload fields only if task retention and recovery semantics remain clear.

No broad loop or middleware refactor is proposed until Phase A demonstrates a concrete reduction in complexity without weakening cancellation, governance, streaming parity, or auditability.

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
2. **SAFE_DEFAULT:** curate a small local, workspace-scoped read-only set. The audit identifies `files_list`, `files_read`, `files_glob_search`, and `files_grep_search` as candidates only after runtime identity binding and path/symlink confinement pass regression tests. Broader tools require explicit allowlisting.
3. **Child runtime ask:** terminate the child with typed `blocked`/approval-required result and no non-resumable child-owned proposal; main-agent HITL remains unchanged.
4. **Custom/MCP:** keep unsupported in subagent manifests for this simplification; do not add another discovery/authorization path.
5. **Retention:** no task/outbox deletion path was found. Preserve task rows and current event payload until replay-from-task tests prove a smaller reference-only event safe.
6. **Legacy profiles:** one-time migration per profile. Explicit non-empty tools -> `ALLOWLIST`; explicit empty -> `NONE`; omitted/ambiguous -> `SAFE_DEFAULT`, with visible diagnostic and no all-native fallback. Malformed/unknown policy fails closed.

### Still open

- Is deprecated `SubagentCoordinator.invoke` used by supported external consumers? Retain it until caller/deprecation evidence supports removal.
- What task/outbox retention policy should replace indefinite retention, if any? No retention changes are included in this pass.
