# Execution Authority and Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stage #40/#41 into reviewable core, qualified MT and required native deliverables without replacing the engine.

**Architecture:** One modular-monolith engine/release. Existing execution/receipt components own evidence; trusted tenant and unprivileged user processes enforce authority where required. The linked plans specify exact interfaces and test gates; implementation is not authorized by this document.

**Tech Stack:** Existing Python/FastAPI/SQLite/CoreMem and Zig native SDK, separately authorized Linux Docker qualification and macOS packaging tools.

**Spec:** [#40 execution authority](../specs/2026-10-07-execution-authority-boundaries-design.md), [#41 receipts/recovery](../specs/2026-10-07-execution-receipt-contract-design.md).

Status: implementation-plan draft for user review and execution-method selection.
Baseline: `7e2d9778` (v0.6.36); specs at `3f82e67f` in `docs/execution-authority-specs`. No runtime implementation, dependency installation, Docker startup, merge or publication is authorized/performed by drafting these plans.

## Global Constraints

- This is an explicitly breaking cutover. No automatic migration or backward compatibility is required from the old engine/configuration/storage contracts.
- Never delete, reinterpret, attribute or modify retained customer data as a side effect of adopting the new contracts.
- The native client is a required product acceptance surface. An interactive CLI can wait.
- The first hardened multi-tenant target is Linux Docker MT.
- Failure blocks hardened MT; do not fall back to the soft/null sandbox.
- Automatic replay of uncertain writes is prohibited by default.
- There is no new mandatory fairness, per-user CPU/memory/storage quota or model-spend control in the first scope.
- Existing timeouts/output bounds and necessary containment remain; resource exhaustion/availability risk is accepted, not solved.
- Deployer identity remains authoritative; no new identity/scheduler/vault/fleet platform, universal compiler or local CoreMem UOW replacement.
- Preserve ConnectKit and generic coding/file/browser capabilities; apply authority/receipt checks rather than delete capabilities to pass security tests.
- Isolated synthetic fixtures only; no live inference/customer credentials, production actions or changes to unrelated worktrees/stores/processes.

## Review Focus

1. Existing tenancy is single hierarchical membership, not company many-team authorization: core Task 1 replaces authority use on fresh stores.
2. Code/configuration/default changes during retries: core Tasks 3/4 bind original intent before resolving new defaults.
3. OS/backend success cannot be inferred from mocks: MT Task 1 is a real negative-control feasibility stop gate.
4. Native compile/test success is not a finished SL product: native Tasks 4–6 gate owned helper/macOS boundary, frozen artifact, clean-Mac and signing proof.
5. Independently green tracks may still disagree at dispatch/client edges: core Task 8, MT Task 6 and native Task 6 require integrated scripted acceptance.

---

## Linked plans and execution order

| Stage | Plan | Testable deliverable | Depends on |
|---|---|---|---|
| A | [Shared engine contracts and receipts](2026-10-07-engine-contracts-and-receipts.md), Tasks 1–5 | Fresh qualified actor/config/admission/store model with API/test contracts | Reviewed plan and selected execution method |
| Gate L | [MT execution boundaries](2026-10-07-mt-execution-boundaries.md), Task 1 | Real Linux feasibility findings, not permanent backend code | Separate Docker/resource authorization; may run alongside A if separately isolated |
| B | Core Tasks 6–8 | Unified governed adapters and version-2 observation/config APIs | A; hardened arbitrary-code claims still withheld |
| C | MT Tasks 2–5 | Qualified tenant/user workers, egress, protected MCP and shared resources | Gate L passed and core dispatch/state interfaces |
| D | [Native engine product](2026-10-07-native-engine-product.md), Tasks 1–3 | Authenticated remote client/config/busy/receipt flows | B; does not require C for SR synthetic acceptance |
| Gate M | Native Task 4 | Owned SL helper and actual macOS boundary qualification | B; Mac probe failure stops for reviewed backend choice |
| E | Native Task 5 | Copied frozen helper/app/DMG artifact | Gate M and D; separately authorized build prerequisites |
| F | MT Task 6 + native Task 6 | Integrated native SL/SR/MT qualification | All above; MT readiness requires both server and actual native checks |

The two final tasks share acceptance evidence; neither may claim MT ready while
waiting for the other to independently claim MT ready. First run the integrated
fixture/app/Linux matrix, then both record the same immutable evidence references.

Implementation tasks are reviewable domain changes, not an instruction to execute
all tracks without checkpoints. Do not parallelize edits to shared authority,
runner/coordinator, loader, HTTP or native entry files. Give each task only its
required spec sections, predecessor Interfaces and owned paths.

## Gates and decisions intentionally not guessed

- **Linux backend:** candidate is existing bubblewrap under provisioned non-root identities; its current root refusal/network constraints remain. Failure needs reviewed alternative backend/probe, not a bypass.
- **macOS SL boundary:** qualify actual filesystem/process/network/credential enforcement separately. Linux tests cannot establish it; unsupported mechanisms require review, not undocumented weak fallback.
- **Store:** fresh per-instance SQLite engine-state file extends existing receipt storage with authority/config/admission domain tables. CoreMem remains a separate idempotent message store; no cross-store atomicity claim. SQLite/Postgres receipt adapters preserve matching evidence semantics; first runtime target is SQLite.
- **Authentication:** begin with deployer-issued individual credentials plus existing validated identity integration. Native SSO/account provisioning is not silently added. Shared deployment keys are not MT people.
- **Package:** plan recommends isolated pinned PyInstaller onedir helper (not a second engine). Build dependency/artifact acquisition needs authorization; clean-Mac signing/notarization is separate evidence and publication remains separately authorized.
- **Rollback/upgrades:** fresh cutover refusal is not a future-data-disposal or automatic rollback policy. Drain admissions, retain persistent stores and expose uncertain work before updates.

## Verification conventions

Core plan creates the offline verification runner. For each new Python test file,
run `python3 scripts/verify_refactor.py -- <exact-test-path> -q`; all task-specific
files are named in linked plans. GREEN means assertions passed **and exit 0**.
Native command set: in `native-sdk-experiment`, `native check`, `native test`,
`native build`; UI lane is `bash tests/engine_contract_suite.sh` against its new
isolated scripted server/app ID, never the existing destructive live-capable lane.
Actual Linux tests are executed in the authorized probe/recipe with unique recorded
resource IDs. Their negative controls must succeed in the uncontained test fixture
and fail only under the candidate boundary: a Python DNS/socket guard or an outer
network filter must not masquerade as worker containment. External access stays
denied; only owned local synthetic services are reachable for this proof.

Capture baseline/current test summaries, inherited warnings/type failures, exact
platform/tool/package versions and cold/warm timings. No downloaded artifacts,
server starts, live auth checks or model calls during documentation checks.

## Spec coverage checklist (author self-review)

- [x] #40 §§1–2: focused engine refactor, SL/SR/MT/MU and one native DMG — all three linked plans; MU/CLI explicitly excluded.
- [x] #40 §3: symmetric privacy, many-team grants, editing/offboarding and restricted audited admin projection — core 1/2/7, MT 5, native 2.
- [x] #40 §4: model/default lifetime, pinning, revision conflicts, no fallback — core 3/4, native 2.
- [x] #40 §§5–6: actual tenant/user identities, OS isolation, install/startup/egress and Linux gate — MT 1–4; native 4 separately qualifies SL.
- [x] #40 §7: credential owners/references, protected actions, approval/revocation — core 2/6, MT 3/4.
- [x] #40 §8: admission/fencing/rejected prompt/read revocation — core 4/7, native 3.
- [x] #40 §9: manual upgrade/state retention/contracts/uncertainty — core 8, MT 6, native 4–6.
- [x] #40 §§10–11: real negative/native acceptance, performance and source-backed limits — all final gates, no claims from mocks/research.
- [x] #41 §§1–3: reuse existing kernel/store, full independent dimensions and private resolved bindings — core 4–6.
- [x] #41 §§4–5: original request identity, fenced owner, intent/start before effect, durable publication and CoreMem limits — core 4/6/7.
- [x] #41 §6: source-bound immutable/conflict evidence and separate late assessment — core 5/6.
- [x] #41 §7: cancellation/physical termination, process-backed sync and foreground/background lifecycle — core 5/6, MT 2/4, native 3/4.
- [x] #41 §8: restart/reconciliation/replay bindings/compensation/restore and explicit durability policy — core 5/6/8, MT 6, native 4.
- [x] #41 §§9–11: receipt privacy, slow-client snapshots, entire injected-failure matrix, native and rollout gates — core 7/8, MT 6, native 3/6.

Checks mark **plan coverage**, not passing runtime tests or independent security review.
No new product-decision blocker was found in the second spec review. Backend and
packaging feasibility remains explicitly gated. Before runtime execution the user
reviews these written plans and chooses the execution method.
