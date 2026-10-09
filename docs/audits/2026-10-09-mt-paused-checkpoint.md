# MT paused checkpoint — resume here, not from scratch

Operator selected **SL → SR → MT → MU** incremental delivery and requested this
checkpoint. MT engineering is paused, not completed or abandoned. One shared engine
and existing authority/receipt contracts remain; no weaker backend substituted.
Recorded from branch refactor/execution-authority at `78c2af85` before staging notes.

## What exists

- Shared foundation (`895cbfc3` plus verifier/auth repairs): Actor/InstancePolicy/
  InstanceState, opaque instance/user paths, explicit provisioning and issuer/subject
  credential bindings, many-team membership model, fail-closed fresh-contract ingress.
  This is **partial** core Task1, not a working qualified v2 runtime. Startup still
  deliberately refuses with `Version-2 runtime is not yet qualified for startup`.
- Linux probe fixtures and exact-owned Docker lifecycle/cleanup helpers retained.
- Reviewed frozen baseline/no-op seccomp artifacts and independent literal-delta
  checks (`37b07acf`, `416410ad`); comparison implementation/evidence `b1e3dc29`.
- Actual matched D(default)/B(baseline)/P(no-op candidate) comparison passed:
  fourteen cases each,42 observations, identical immutable image, UID/GID20001,
  CapEff0, NoNewPrivs1, Seccomp2, process exit0 and exact container absence.

## What the successful comparison proves — only this

D/B deny `unshare(0)` with EPERM; P allows it. All namespace-bearing clone/unshare
cases remain EPERM in all three. Ordinary clone succeeds in all three.
This supports seccomp contribution to **no-op** unshare denial only. Baseline was
accepted as substitute-reviewed, not an attested export of the live installed filter.
It does not qualify namespace creation, bubblewrap or MT isolation.

## Actual blocker

Non-root bubblewrap launch under the tested Docker restrictions failed:

`bwrap: No permissions to create a new namespace, likely because the kernel does not allow non-privileged user namespaces.`

Cause of namespace-bearing denials remains unresolved. The no-op policy change
**did not unblock bubblewrap**. Hardened filesystem/process/egress/restart/termination
qualification matrix remains unrun. No tenant/user worker runtime, protected worker
transport, MCP lifecycle/credential separation or native MT acceptance is qualified.

## Verification distinction

Pure parser/delta/lifecycle tests passed and actual no-op attribution passed; those
are not backend isolation proof. Attribution Task3 remains a checkpoint: latest
full verification reproduced95 failures with macOS helper SIGSEGV. After operator
Mac reboot, one60-second full-context capture had no SIGSEGV and confirmed owned
cleanup; it reached its deadline, not a full-suite pass. Native cause remains unknown.
These host/general-test findings are separate from Linux namespace feasibility.

## Resume sequence when MT becomes the active delivery stage

1. Read original MT/#40/#41 plans and retained evidence below; reconcile current
   shared-engine interfaces after SL/SR rather than redo the engine or diagnostics.
2. Design/review an exact namespace-capable Linux deployment/backend recipe with
   explicit privilege/credential/resource ownership. New Docker/resource execution
   permission is required; no previous no-op approval implies namespace permission.
3. Pass real namespace/bwrap feasibility and the complete negative-control matrix.
   Never use privileged/root, SYS_ADMIN, seccomp=unconfined, disabled NNP, sysctl
   changes or soft/null fallback to manufacture qualification.
4. Implement/review provisioned tenant/user identities, workers, authenticated
   dispatch, protected credentials/MCP and termination/restart evidence (MT Tasks2–5).
5. Qualify private/shared/company/team grants and matching server/native MT isolation
   (MT Task6 plus MT portion of native Task6). Admin membership never grants private
   content. No new spend/quota/fairness guarantees implied.

No MT work, Docker startup/build/pull, policy widening, daemon changes, merge/release
or unrelated/private-store access is authorized by this checkpoint. Preserve existing
fixtures/artifacts/commits; no global cleanup. MU remains a separate later stage.

## Exact resumption references

- [Authority foundation](2026-10-07-authority-foundation-progress.md)
- [Failed actual bubblewrap gate](2026-10-07-linux-boundary-probe.md)
- [No-op artifact review](2026-10-07-namespace-noop-policy-review.md)
- [Actual comparison + scope](2026-10-07-namespace-comparison-result.md)
- [42 observations and cleanup](2026-10-07-namespace-comparison-evidence.json)
- [Latest post-reboot limited observation](2026-10-09-post-reboot-state-capture.md)
- [MT implementation plan](../superpowers/plans/2026-10-07-mt-execution-boundaries.md)
- [Attribution implementation plan](../superpowers/plans/2026-10-07-linux-namespace-policy-attribution.md)
- Owned ledgers: `.superpowers/sdd/2026-10-07-engine-contracts-and-receipts/progress.md`
  and `.superpowers/sdd/2026-10-07-linux-namespace-policy-attribution/progress.md`.
