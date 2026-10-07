# #41 — Execution receipt and recovery contract

Status: design draft for user review; not implementation authorization or shipped guarantees.

Companion: [#40 authority boundaries](2026-10-07-execution-authority-boundaries-design.md).
Source baseline: local main `7e2d9778` (v0.6.36).

## 1. Intent and scope

Make execution evidence truthful across built-ins, custom commands, MCP, synchronous
tools and asynchronous jobs. Extend `ExecutionKernel`, `ReceiptStore`, existing
lifecycle events and observations; do not create a parallel receipt system or
scheduler. Keep external-operation terminal evidence distinct and adapt it through
an explicit, validated observation contract rather than an ad hoc receipt bridge.

One execution admission/lifecycle contract does not mean one giant universal
executor. Existing specific adapters implement the same invariants. Clients observe
server-owned state; they do not infer authoritative outcomes from visible prose.

This is a breaking cutover with no automatic migration or backward-compatibility
requirement. Start a fresh qualified store; retained old stores remain untouched.
Never synthesize historical actor, tenant, authority or effect evidence. Keeping
existing Python classes as implementation building blocks does not promise old
schema/API compatibility.

## 2. Questions a receipt must answer independently

| Dimension | Required meaning |
|---|---|
| Authorization | Was this actor permitted to attempt this bound action at dispatch? |
| Executor state | Did the actual execution boundary start, remain running, terminate or become unknown? |
| Outcome | How did the execution attempt complete or fail? |
| Effect state | Was the requested effect applied, not applied, inapplicable or unknown? |
| Verification | Did suitable evidence establish the requested condition? |

Preserve the existing vocabulary:

- `Outcome`: `succeeded`, `failed`, `cancelled`, `timed_out`, `killed`, `uncertain`,
  `incomplete`, `refused`, `rejected`.
- `ExecutorState`: `not_started`, `running`, `terminal`, `unknown`.
- `EffectState`: `not_applicable`, `applied`, `not_applied`, `unknown`.
- `VerificationState`: `not_requested`, `pending`, `verified`, `not_verified`, `unknown`.

Do not add an overlapping `unknown` outcome merely because other dimensions have
one. A timeout may retain outcome `timed_out` with unknown executor/effect state.
Use `uncertain` when the attempt's completion itself cannot be determined.

`Succeeded` alone is not proof that the requested business condition holds.
A zero exit code, MCP success envelope or self-reported text may establish wrapper
completion but not the external effect. A process can fail after applying a write.
`not_verified` means evidence did not establish the condition; it does not imply
`not_applied`. Finalization time is when evidence was recorded, not proof of when
an executor actually stopped.

Use `not_applicable` only when the requested operation is known not to require an
effect. Potentially effectful custom code must not receive that classification
solely from read-only/idempotent annotations. This is an evidence rule, not a new
72-tool taxonomy. Preserve original failures; do not produce success-shaped errors.

## 3. Normalized request and resolved-run evidence

Extend the existing request/receipt contract with server-derived bindings sufficient
to identify:

- Logical instance/tenant and authenticated initiating user.
- Session/conversation, run, tool call and parent/delegation identity where present.
- Admitted request identity and definition/arguments fingerprint.
- Actual target executor and provisioned execution identity.
- Resource/integration/credential references and bounded authority provenance.
- Instance/infrastructure policy revisions, grants and approval identity/scope.
- Resolved model/provider and profile/tool/skill definition revision for the run.
- Server admission/start/finalization times, termination reason, observations and
  owned artifact references.

Keep actor, definition owner, credential owner and executor distinct. No role/title
or caller-supplied namespace implies authority. Main/subagent model semantics are
specified in #40: persistent conversation choices, instance defaults captured at
creation, launch-time inheritance, and no mid-run model switch.

Keep sensitive arguments/results in authorized private storage. Fingerprints refer
to canonical effective arguments and secret references/versions without storing
secret values. Redacting two different requests into equal display payloads must
not make them equal deduplication identities. Operational projections must not
expose private arguments or permit low-entropy secret inference through public
fingerprints. No model/provider keys or dispatch credentials belong in a receipt.

## 4. Admission and exclusive execution ownership

Authenticate and check session/resource access before exposing an active run or
receipt. Atomically admit one active turn per tenant/instance session, following
server admission order, not device timestamps. The losing overlapping prompt is
not queued or added to accepted history. Return busy/not-accepted plus the active
run identity, then provide an authorized current snapshot and stream of that run.
This does not create another executor or duplicate the user's accepted message.

Admission and idempotency are related but distinct:

1. Request identity is scoped to instance/tenant and actor, and bound to session,
   effective arguments, definition and resolved execution request.
2. Retry of that same client intent returns the original admitted request/run/receipt;
   it does not start another execution. Retrieve its first-admission resolved binding
   before applying new defaults/definitions; a retry must not become a different
   request merely because configuration changed after admission. Checking current
   read permission still applies before returning private recorded data.
3. Reusing the identity with incompatible content is rejected before dispatch.
4. An independent later prompt while the session is busy is not an idempotent retry;
   it receives the busy/attach behavior.
5. Finishing a run and admitting its successor must not race two dispatch owners.

One process-local mutex is not a cross-process ownership guarantee. Establish one
active owner per admitted run, with an ownership generation/fence checked at dispatch
and evidence admission. Replacement requires retiring or fencing the old owner;
a stale worker cannot submit accepted state transitions or newly dispatched effects.
Never run old and replacement owners concurrently under the same unfenced authority.
Fencing cannot retract an already transmitted external request.

A browser/socket disconnect is not execution failure or automatic cancellation.
An explicit cancellation requires the addressed run identity and cancellation
permission, so a stale client cannot cancel a different successor run.

## 5. Execution sequence and durable recording

```text
validate identity, access, request and session admission
persist admitted intent and resolved-run binding
recheck dispatch authority and definition/approval binding
persist dispatch/start ownership
perform effect outside storage mutation transactions
record source-bound observations
record immutable attempt completion
publish authoritative completion and derived client state
```

Authorization/admission does not imply an executor started. Authority can be revoked
between admission and dispatch; recheck at dispatch and record refusal without an
effect. No approval silently authorizes new code/arguments or broader permissions.
Capture the admitted effective input, but do not freeze permission forever.

If required durable intent/start recording fails, do not begin a new effectful
action. If storage fails after dispatch, stop admitting further effectful work on
that uncertain authority path, report the recording failure where possible, and
reconcile after reopening. Never invent success or assume rollback of the external
effect. Already-dispatched work may continue; storage failure cannot undo it.

Keep external effects, model calls and human waits outside storage transactions.
For cross-store conversation/document atomicity, use the existing CoreMem upstream
unit-of-work track, not another local transaction abstraction. This spec does not
claim that separate message and receipt stores already commit atomically.

Authoritative execution state and completion are published only after their record
is committed. Token/progress persistence and full primary-run durability remain
linked work: either persist progress before publishing it or explicitly label it
transient. Do not claim every token is durable merely because final receipts are.
The UI must tolerate restoration to the last committed snapshot.

## 6. Evidence and terminal immutability

An observation identifies its receipt/request correlation, source, kind, observed
time, verification basis and private payload/artifact references. An adapter must
validate external job/callback identity, tenant, operation and correlation before
accepting it. A worker's `authoritative: true` assertion or stdout is not sufficient
proof. Callback credentials authorize evidence for a bound operation, not unrelated
execution or read access.

Required evidence depends on the operation:

- Local storage mutation: committed operation identity/revision from the trusted
  storage path can establish application of that local effect.
- External mutation: provider-specific acknowledgement, operation handle or
  trustworthy read-back establishes only the claims its semantics support.
- Process/MCP completion: establish actual observed completion without assuming
  stronger business evidence.
- Async job: distinguish job admission, still-running status, terminal executor
  evidence and later verification; an HTTP acknowledgement is not terminal evidence.

Terminal attempt fields are immutable. Repeating the same finalization is harmless;
conflicting finalization is rejected. Observations are immutable and deduplicated by
source-bound identity; reuse with incompatible evidence is rejected, not ignored.

Later authoritative evidence may be appended after an interrupted/finalized attempt.
Preserve its original outcome, executor/effect/verification states and termination
reason. Expose a derived current evidence assessment separately from those original
fields, with the observations that justify it. It may clarify that an effect applied
without turning the original timeout into a successful attempt. Corrected or
conflicting external evidence is another observation, never an overwritten payload.

Existing `record_observation()` and immutable `finalize()` are useful seams; their
current implementation alone is not the full source validation/reconciliation
contract above.

## 7. Cancellation, timeout and synchronous code

Separate cancellation requested, local waiter ended, executor termination observed
and external-effect assessment. Cancelling an await, thread wrapper, HTTP stream or
MCP wait does not prove that the action-capable executor stopped.

- Verified child-process termination may establish `terminal` with `killed`,
  `cancelled` or `timed_out`; the effect can still be unknown.
- If a thread or remote job may continue, record unknown/running executor state as
  supported by evidence, not a fabricated terminal state.
- Cooperative cancellation is a request, not an undo guarantee.
- A task never dispatched can record `not_started` and, for an intended mutation,
  `not_applied` when non-dispatch is established.
- Failed/unknown cleanup retains its uncertainty; it does not become success.

Synchronous tool paths need a first-class asynchronous adapter with explicit
lifetime/termination behavior. `asyncio.to_thread()` alone does not make a function
cancellable. Where stopping arbitrary non-cooperative code is required, use the
process boundary from #40; no forced Python-thread termination abstraction.

Subagent lifecycle ownership is separate from access authority. Foreground owned
work must drain or be explicitly accounted for before declaring its parent execution
fully stopped. Explicit background work can survive a parent's client disconnect
or cancellation according to recorded lifecycle policy, never by silently detaching
under broader authority. Do not introduce another task graph product in this scope.

## 8. Restart, reconciliation and replay

On restart, inspect interrupted admitted/started requests before granting new
execution ownership. Mark work pending reconciliation rather than presume failure
or success. Reacquire external handles/read-back where supported; expose uncertain
work to the owner/authorized operator when it cannot be resolved.

Automatic replay of uncertain writes is prohibited by default. Replay requires
all of the following:

1. Recorded operation semantics establish replay safety (effect-free operation or
   genuine downstream idempotency), not merely an authored `safe` annotation.
2. A stable original downstream key/handle and effective input are available.
3. The original and current definition/policy permit that replay.
4. Current actor/resource/credential authority is revalidated.
5. The executor/resource binding remains the admitted one; a changed environment
   cannot silently redirect replay into another workspace/integration.
6. Exclusive execution ownership is established and the prior executor cannot
   continue a duplicate local dispatch under stale authority.

Request deduplication within the engine is not exactly-once delivery at an external
service. If a write may have occurred and its service lacks suitable reconciliation/
idempotency, preserve unknown effect and require explicit operator/user resolution;
do not present a routine retry as safe. Explicitly authorized compensation is a
new governed action with linked evidence, not rewriting history or assuming undo.

Stopped-copy restore does not automatically restart uncertain writes or schedules.
Schema/version incompatibility is a clear refusal, not silent repair. Host/power-loss
durability must be stated separately from process-crash durability, based on the
actual store settings and verified behavior.

## 9. Client observation, privacy and administration

Native clients hydrate authorized current state and follow incremental updates.
Reconnect or slow-consumer recovery may replace transient UI deltas with a current
snapshot. Bounded observation streams are not audit journals: all required lifecycle/
receipt facts remain in durable evidence. A client's lack of an update does not
prove work stopped. Streaming to a second device still checks current read access
and stops after revocation; previously delivered authorized content cannot be retracted.

The native client must show:

- Accepted prompt versus busy/not-accepted prompt and the active run being observed.
- Actual run model and saved conversation model versus instance default.
- Approval required/refused without representing either as completed execution.
- Cancellation requested versus known stopped/unknown executor.
- Applied/not-applied/unknown effect and verification where relevant.
- Clear connection/version failures rather than silently weaker policy.

Keep private prompts, commands, SQL, results, observations and artifacts within
resource/receipt access rules. Team publishing does not publish historical receipts.
Authorized admins receive only #40's redacted operational projection. Artifact paths,
private resource names and inference/tracing contents do not leak through error
messages or operation metadata. Collection is not consent to external tracing;
operational OTLP and semantic/model tracing remain distinct destinations.

## 10. Failure-injection and acceptance matrix

| Injected condition | Required result |
|---|---|
| Intent/start persistence fails | No new effectful dispatch |
| Authority revoked after admission | Refusal at dispatch; no borrowed stale grant |
| Two devices/replicas submit to one session | One admitted run; loser busy/attaches, no queued prompt |
| Same request retried | Same recorded run/receipt, no duplicate dispatch |
| Same identity, different input | Rejected before effect |
| Local timeout/cancel, remote/thread continues | Honest executor/effect uncertainty |
| Effect occurs, completion record fails | No invented failure/rollback; reconcile, no blind replay |
| Old worker survives replacement | Fenced new dispatch/evidence; transmitted external effect still uncertain |
| Callback crosses tenant/operation | Rejected; no content/authority leak |
| Conflicting observation identity | Rejected; original evidence unchanged |
| Later trusted evidence arrives | Additional observation/assessment; original attempt immutable |
| Definitions/resources change before recovery | Revalidate; no automatic broadened or redirected replay |
| Prohibited/unavailable saved conversation model | Explicit error, no silent fallback |
| Client disconnect/reconnect | Work not cancelled; authorized current state/stream |
| Restore/version mismatch | No unsafe automatic replay or invented schema compatibility |

Run the matrix against built-in local writes, actual custom command boundaries,
MCP adapters, sync wrappers, async jobs and delegated execution. Begin with synthetic
stores, deterministic fake/scripted providers and external-service doubles; no
production credentials, live customer actions or live fixture inference. Qualified
Linux Docker execution tests are a separate explicit gate, not mocked proof.

Native SL/SR and hardened MT scenarios are required acceptance, including two
observers, server restart, persistent conversation model choice and private evidence.
The native app's existing successful build/117 unit tests do not establish those
scenarios. CLI implementation and MU hosting remain separate scope.

No new fairness/CPU/memory/storage/spend quotas are required. Preserve necessary
output/observation bounds and existing timeouts; absent quotas do not imply an
availability guarantee. Record performance baseline and changed dispatch overhead
without hiding security failures as timing optimizations.

## 11. Rollout and source observations

Write/review the linked specs, then a separate reviewed implementation plan. Stage
implementation around existing seams, with negative tests before isolation claims.
Enable hardened MT only after #40's real execution gates and this recovery matrix
pass. SL/SR must not require multi-tenant identity provisioning; native compatibility
must be qualified for the breaking server/client contract.

Current seams and gaps:

- `src/sdk/execution_models.py`: existing request, observation, completion and receipt
  types; authenticated tenant/actor/provenance require extension.
- `src/sdk/execution_kernel.py`: durable create/start/finalize and interrupted states;
  current timeout/cancellation branches set terminal executor state, which is not
  always evidence of physical/remote termination.
- `src/sdk/execution_store.py`: request-content mismatch checks, lifecycle events,
  appendable observations and immutable finalization; source-bound evidence conflicts
  and a derived late-evidence assessment are target requirements, not proved coverage.
- `src/sdk/governance.py`: approved execution uses the kernel, but currently supplies
  `expected_effect=NOT_APPLICABLE`; effect fidelity needs operation-specific evidence.
- `src/sdk/tools.py`: synchronous execution uses `asyncio.to_thread()` today.
- `src/sdk/session_worker.py`, `src/sdk/run_service.py`, `src/http/routers/ws.py` and
  `src/http/routers/conversation.py`: current run/session/client paths to audit for
  tenant binding, atomic admission and busy/current-stream semantics.
- `src/sdk/governance_operations.py`: existing external operation lifecycle remains
  distinct from execution receipts; any adaptation must validate evidence provenance.

Learn intent/effect/outcome separation, explicit cancellation and snapshot-based
observation from [Pi Durable's README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md)
and [specification](https://github.com/earendil-works/pi/blob/main/packages/durable/docs/spec.md).
Its trusted Session ownership is not tenant authorization, and replay declarations
must not bypass our authority checks. Borrow structured client lifecycle/approval
contracts from [Codex app-server](https://developers.openai.com/codex/app-server/)
without adopting unrestricted personal-host execution endpoints or assuming its
experimental remote transport is a production tenant boundary.

Research and source inspection are not deployment or runtime verification. This
draft does not authorize a merge, release, push, Docker startup or customer changes.
