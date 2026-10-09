# SL/SR staging — author self-review

Scope: staging commit `afa7a9ba` against `78c2af85`, original core/native/MT plans,
branch AGENTS.md, current ingress/startup refusal and retained Linux/post-reboot
results. Requested by operator. **Author review, not independent/fresh-context
whole-branch review.** No runtime or resource execution performed.

## Important findings and corrections

1. **Contradictory canonical guidance.** AGENTS.md still parked macOS product work
   in both its product-direction and native-app sections. Updated both to active
   SL/SR, paused MT and later MU; linked amendment/checkpoint. Preserved written
   review status, installed-only/offline constraints and no shipped-readiness claim.
2. **Hidden MT dependency.** Core Task6 supplies actual worker/credential adapters
   exclusively from the MT plan. Reordering stages alone could still block solo
   work or invite legacy direct-execution bypass. Core plan/amendment now explicitly
   require qualified solo adapters behind the same authority/dispatch/kernel
   contracts; multi-tenant layout alone is deferred. Exact adapter/backend task
   design is still pending, not selected or implemented by this correction.
3. **First slice and acceptance too loose.** Qualified initialization/identity must
   not enable tools, legacy routes or background dispatch before policy/admission/
   evidence/adapters/boundaries qualify. Made that restriction explicit. Require
   exact error-free scripted answer, actor/instance binding, tool fixture effect/
   receipt and recovered model/session, not HTTP200 or build success. Scripted tool
   proof still does not qualify arbitrary shell/MCP or OS containment.

## Additional clarifications

- Independent fresh-context whole-branch review is mandatory before integration;
  unavailable reviewer tooling blocks the gate, rather than making author review
  a substitute.
- MT resume permits a separately reviewed alternative backend meeting the original
  security properties; bubblewrap is a candidate, not an irrevocable requirement.
- Full offline baseline remains proposed/unapproved; no automatic new diagnostic
  campaign before task review. Focused RED/GREEN never clears the historical full
  suite failures or qualifies the affected native execution path.
- Local ignored execution ledger updated for resumption; not a published artifact.

## Verification performed

- [x] Read staging changes and core task/dependency contracts; checked canonical
  guidance and actual startup/ingress source rather than only the new notes.
- [x] Checked MT claims against actual42-observation comparison interpretation and
  post-reboot claims against the single incomplete60-second capture.
- [x] Four structural checks reproduce missing requirements in committed original
  documentation and pass against corrections: guidance, adapter dependency,
  identity-only execution refusal and error-bearing HTTP200 rejection.
- [x] Relative links in changed documents/guidance resolve; `git diff --check` clean.
- [x] Changed tracked paths are documentation only; production startup refusal
  unchanged, no .env created. No Python product/native/full-suite pass claimed.

## Disposition

Corrected staging amendment is suitable for user review, **not yet an executable
solo implementation plan or qualified runtime**. Next: approve written amendment,
re-scope existing tasks into concrete inline/native solo increments, then resume
partial Task1. Actual SL/macOS and SR/host security proof, native workflows/artifact
qualification, full-suite green evidence and independent whole-branch review remain
pending. MT stays paused/unqualified; no merge/release authorized.
