# SL/SR-first delivery amendment

Status: operator approved direction; written staging amendment for review before
revising implementation tasks. No runtime/verification/resource execution by this
document. Execution preference remains inline/native; independent final branch
review remains required when reviewer tooling is available.

## Intent

Deliver useful solo workflows before qualifying multi-tenant hosting. Operator
states there are no customers currently. Preserve all existing/private stores and
unrelated worktrees anyway; no migrations, customer-resource changes or publication.
**One engine/codebase/release: SL → SR → MT → MU**, not four engines or rewrites.

This changes delivery order and acceptance grouping of the original roadmap, not
identity, credential, execution or evidence semantics of #40/#41. MT is parked at
[its precise checkpoint](../../audits/2026-10-09-mt-paused-checkpoint.md); MU/CLI remain
deferred. A selector entry is not support for an unqualified mode.

## Active milestones and acceptance

### 1. SL developer workflow

One person uses the existing native app with the owned local Python engine: explicit
solo provisioning, bound identity, chosen model, conversation, permitted tool,
approval/cancellation, persisted session/model and honest restart/recovery. First
acceptance uses isolated scripted providers and synthetic stores/credentials.
Developer-build proof is not an installable/production DMG claim.

Keep actor/instance/path/credential/admission/receipt primitives already introduced;
finish only their solo runtime/API integration first. Preserve AgentLoop/providers,
CoreMem/kernel, durable intent/evidence and no-blind-uncertain-write replay. Never
enable v2 startup merely by removing its refusal: qualify its initialization/context
and routing first. Solo users do not need company/team provisioning or admin UI.

### 2. SR workflow

Same engine/server contract in a dedicated single-owner remote instance, used by the
native client from multiple devices. Explicit individual authentication/TLS, isolated
connection credentials/caches, persistent model choice, one accepted session turn,
busy/attach, disconnect/reconnect and restart evidence. Remote failure must never
execute locally or silently start the SL helper. Native SSO/platform signup is not
silently added; use existing validated individual/server-issued credential contracts.

Qualify actual execution/credential/termination boundaries of the chosen SR host.
Dedicated single ownership is **not** permission to expose server secrets to tools,
use unauthenticated public ingress, or relabel an unqualified shared MT server as SR.
If SR uses the blocked Linux/bwrap recipe, that boundary still blocks SR arbitrary-
code readiness; choose/review a supported dedicated-host recipe separately, not a
soft/null fallback. SL/SR security remains mode-specific and evidence-gated.

### 3. SL distribution + combined solo acceptance

Bundled helper/runtime, artifact resources outside checkout, state outside .app,
correct app/helper contracts and clean supported Mac without developer Python/Docker.
Signing/notarization/build dependencies need separate authorization. Qualify actual
native SL and SR flows, not only Python/Zig compilation. Unsupported MT/MU fail
explicitly until later qualified. No signing, release or publishing authority here.

## What remains common and what is deferred

**Common now:** explicit ownership, opaque scoped state, bound credential routing,
protected integrations, model/revision semantics, admission, cancellation/termination,
receipts/recovery, target isolation and native API/helper lifecycle.

**Defer to MT:** runtime company/team provisioning/admin/share UX, multi-tenant worker
layout/transport/identity provisioning, cross-company negative tests and native MT
acceptance. Existing many-team models/tests are retained, not deleted or made weaker.
**Defer to MU:** public hosted onboarding, untrusted-tenant containment/abuse/service
operation. No new fairness/quota/spend guarantees are added by ordering stages.

Do not make Gate L or MT integrated acceptance a prerequisite for reporting separately
qualified **solo** milestones. Do not relax SL/Mac or SR/host boundaries because MT
is paused. Full #40/#41/SL+SR+MT completion still requires the later MT gates.

## Existing plan reuse and immediate next slice

Use the existing core and native plans as interface/source references; do not create
a parallel engine. Revise task scope/checklists into solo-first increments after
written amendment approval:

1. Resume partial core Task1 at **qualified solo provisioning/runtime/context and
   identity ingress**, not another namespace attribution or debugger experiment.
2. Wire existing native protocol/connection/config/run tasks for SL/SR; company/team
   controls remain deferred while future-mode-compatible types are retained.
3. Qualify owned SL helper/macOS boundary and dedicated SR host, then frozen SL
   artifact/solo end-to-end acceptance. Report developer versus distribution proof.
4. Resume MT from its checkpoint only when that stage is selected.

Latest normal full verification is still red. Post-reboot60s capture is incomplete
and not a fix. A separately bounded offline full baseline run is the sensible next
verification gate; no run-until-green, bypass, skipped failure or unlimited diagnostics.
Native shell/host failures still block claims for the affected solo execution path.
Dependencies/resources/signing/network remain separately authorized; preserve dirty
main/unrelated state. This amendment is not permission to merge or enable unqualified
runtime paths. Final independent whole-branch review remains pending.

## User review gate

Confirm this short written staging amendment before revising implementation tasks.
Then retain the chosen inline/native execution method and implement test-first
solo slices, with each gate measured by a working user flow rather than an audit
count. No further requirements workshop or wholesale rewrite is proposed.
