# Open-Issue Reliability and Security Remediation Design

**Date:** 2026-09-24  
**Status:** Self-reviewed; ready for implementation planning  
**Scope:** GitHub issues #35–#42, excluding deferred #7

## Goal

Close the remaining applicable reliability, receipt-fidelity, session-lifecycle,
and capability-boundary issues in priority order without weakening main-agent
governance or administrator `deny` decisions.

## Deferred scope

Issue #7 is explicitly deferred. MCP may require a separate health, transport,
and discovery redesign. This work will not change MCP behavior or close #7.

## Priority

1. #42 — summarization escape hatch and unbounded summary growth
2. #40 — shell capability boundary
3. #41 — universal execution/receipt contract
4. #37 — dropped stream/session recovery
5. #38 — proposal status/outcome ambiguity
6. #39 — configurable loop depth and exhaustion visibility
7. #35 — custom-command pipeline failure semantics
8. #36 — custom-command output truncation contract

## Constraints

- `shell_execute` defaults to approval-required (`ask`) when governance is
  enabled; it is not globally disabled by this remediation.
- Main-agent governance remains authoritative. No subagent, custom tool, shell
  command, or internal callback may bypass an administrator `deny`.
- A terminal result must never claim success solely because a string was
  returned or a proposal status was advanced.
- Unknown external side effects remain `unknown`/`uncertain`; they are never
  automatically replayed.
- MCP behavior is out of scope.
- TDD is required for every behavior change.
- Existing SQLite single-process deployment assumptions remain unchanged.

## Design

### 1. Shared execution outcome vocabulary (#38, #41)

The existing execution models already define `Outcome`, `EffectState`,
`VerificationState`, `ExecutionCompletion`, and `Receipt`. Remediation will
align the governed and synchronous tool paths to these types rather than add a
second receipt system.

The canonical vocabulary must include the currently emitted values:

- `succeeded`
- `failed`
- `cancelled`
- `timed_out`
- `killed`
- `refused`
- `uncertain`
- `incomplete`

Existing `rejected`/legacy values remain readable during migration. New
writes use one canonical spelling. A receipt cannot report `succeeded` when the
executor is unknown or the requested effect was not verified.

Tool results may carry the outcome as internal structured metadata; provider
schemas and model-visible tool arguments are not expanded unnecessarily.

Governed proposal rows keep `status="executed"` as the historical terminal
meaning “approval consumed and terminal” so replay/idempotency remains intact.
The separate `outcome` field is authoritative for success/failure/timeout/
uncertainty. API responses and audit records must expose both fields and tests
must assert the pair.

### 2. Shell capability boundary (#40)

`shell_execute` is a high-impact capability because it can invoke interpreters
and reach network-accessible processes. Its default permission is `ask` when
governance is enabled, even if no per-item override exists.

The remediation will:

- add a default policy decision for `shell_execute` that resolves to `ask`;
- preserve explicit administrator `deny` and user `allow`/`ask` overrides;
- continue scrubbing secret-like environment variables from sandboxed child
  processes;
- remove general-purpose interpreters from the safe default command allowlist;
- require an explicit deployment opt-in to re-enable an interpreter or broad
  command capability;
- document that shell/network/process access is an explicit capability
  escalation, not a consequence of enabling ordinary governed tools.

A soft sandbox does not claim kernel-enforced network isolation. Documentation
must state that operators requiring a stronger network boundary must use an
isolated backend or deployment-level egress policy.

### 3. Summarization recovery and bounded growth (#42)

The current no-LLM escape hatch must be tested by invoking the actual prune
callable, not by importing the module.

A finite summary budget will be configurable. The update prompt will receive a
bounded previous summary, and generated summaries will be constrained to the
same budget. If the model returns an oversized summary, a deterministic
bounded fallback will be persisted rather than allowing unbounded accumulation.

When summarization fails, the forced-trim path will remove or exclude the
oversized summary from the active model context as well as old message rows.
The session will recover without repeatedly resubmitting the same doomed
summary. The exact storage representation will preserve historical summaries
for audit/replay while preventing the newest unusable summary from being fed
back into every subsequent model call.

The next release tag must contain the corrected import and the bounded-summary
behavior. Existing tags are historical artifacts and are not rewritten.

### 4. Dropped-stream session recovery (#37)

The session registry will track a lease/last-activity timestamp for each active
run. Run execution will refresh activity independently of the client
connection. A bounded stale-session policy will request cancellation and allow
the normal `finally` path to release the lock instead of leaving `session_busy`
forever.

The design must cover both SSE and WebSocket disconnects, cancellation while a
provider call is in flight, persistence of already-collected partial state, and
the case where the client disconnects after the server has produced a proposal
or result. Recovery is bounded and observable; it does not claim exactly-once
network delivery.

### 5. Iteration exhaustion visibility (#39)

A deployment-level `agent.max_iterations` setting will be added with a finite
validated range and an explicit default. It will be wired into `RunConfig` and
profile/bootstrap construction.

When the loop exhausts its iteration budget, the run will produce a distinct
`incomplete`/`iteration_limit` outcome in the run result, audit record, and
user-visible terminal event. A normal model completion remains distinct from
an iteration-budget stop.

### 6. Custom command pipeline semantics (#35)

Custom `TOOL.md` annotations gain an opt-in `pipefail: boolean`, defaulting to
`false` for compatibility. When enabled, the command runs under Bash with
pipefail enabled. A non-zero early pipeline member then produces a failure
result rather than a success-shaped string.

The authoring documentation will state the default behavior and the opt-in
behavior. Tests will use real pipeline commands, including an early failure
and a legitimate early non-zero filter.

### 7. Custom command output ceiling (#36)

The sandbox’s capture-ceiling signal will be propagated to custom-command
results. If output exceeds the capture ceiling, the result will explicitly
report truncation and state that recovery is unavailable. It must not advertise
`tool_result_read` recovery for output that was never captured.

The initial remediation prioritizes truthful failure over an unbounded memory
capture. A later streaming/spill implementation may provide full recovery.

## Testing and release gates

Each issue receives a focused regression test before implementation. The release
gate requires:

- targeted issue tests green;
- full Python suite green;
- Ruff clean;
- scoped mypy for changed non-router modules;
- migration/replay tests for proposal outcomes;
- explicit tests for shell `ask`, `allow`, and `deny` decisions;
- explicit tests for oversized summary and dropped-session recovery;
- no MCP changes in the resulting diff.

No issue will be closed solely because a neighboring architectural component was
implemented. An issue is closed only after its own acceptance criteria and
release evidence are satisfied.

## Review questions

1. Is approval-required (`ask`) the correct default for `shell_execute`, with
   explicit `allow`/`deny` overrides, rather than global disablement?
2. Is explicit truncation acceptable for the first #36 remediation, with a
   later spill/streaming follow-up?
3. The stale-session lease uses a five-minute default and remains
   deployment-configurable for deployments with longer provider/tool calls.
