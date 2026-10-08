# Namespace attribution plan — self-review

Reviewed: [three-task plan](../superpowers/plans/2026-10-07-linux-namespace-policy-attribution.md)
against [approved spec](../superpowers/specs/2026-10-07-linux-namespace-policy-attribution-design.md)
and existing `probe_helpers.py`, `namespace_diagnostic.py`, `conftest.py`.
Method: inline self-review; no subagent/independent review claim.

## Six findings repaired in the plan

1. **Provenance requirement lacked concrete refusal tests/interface.** Added a
   pinned-record schema, validator signature and tests for moving refs, engine
   mismatches and unreviewed substitutions. The record does not authenticate its
   own claims; source/mapping and substitution still require operator review.
   Also pinned rejection of duplicate keys and non-finite JSON values.
2. **Pre-existing unconditional unshare ALLOW was not rejected.** Such a baseline
   could already permit the no-op or broader namespace operations, defeating the
   premise of a one-rule diagnostic addition. Added conservative rejection and
   tests; reviewed conditional baseline rules remain unchanged.
3. **Comparison interface could not enforce its promised runtime facts.** The
   original matrix accepted only syscall rows, so it could not distinguish image,
   architecture, UID/capability changes, failed process exits or uncertain cleanup.
   Added a small diagnostic run record and corresponding refusal tests. Preserved
   the legacy helper's CompletedProcess return type via a separate recorded helper
   rather than silently breaking the existing preflight tests. Requested manifest
   and resolved platform image identity are recorded separately.
4. **D/B mismatch did not explicitly prevent P launch.** Added an ordered baseline
   pair gate: D then B must satisfy assertions/cleanup before P is invoked. A
   fourteen-case match is not evidence of complete installed-filter equivalence.
5. **Cleanup certainty was underspecified and unsafe to inherit.** Existing helper
   skips removal for any failed inspect, which cannot distinguish absence from an
   unavailable Docker API. Added tests and an explicit successful-inventory/removal
   confirmation protocol, including missing CID files and mismatched labels.
   No global prune or unrelated resource deletion is permitted.
6. **Diagnostic parsing/security opt-in refusals were incomplete.** Added exact
   row/key/type/errno checks, duplicate/trailing-data rejection, mandatory policy
   facts, immutable/mixed-image refusal and a test that both actual-execution flags
   are required. A process exit0 or ordinary Linux-probe opt-in is not authority to
   apply a changed profile or evidence that a syscall succeeded.

## Spec coverage and consistency

- Intent/non-root/private-resource/fail-closed invariants: global constraints.
- Existing unchanged-policy syscall findings and aarch64-only ABI: Task 3 refuses
  unsupported architecture and reuses the retained diagnostic; no ABI guessing.
- Baseline provenance/conditional rules/checksums: Task 1 and its user review gate.
- Exact sole no-op addition, conflicts and byte-frozen review: Task 2.
- Ordered D/B/P calls, runtime state, hashes, snapshots and cleanup: Task 3.
- Explicit policy execution authorization and later full Linux qualification:
  Task 2/3 stop gates, not automatic approval or production MT readiness.

Plan remains three independently reviewable tasks. Helper signatures, typed run
facts, wrapper compatibility and test ownership agree across tasks. Pure tests are
outside the opt-in Linux collection so they cannot disappear behind Docker skips.
The five review-focus risks now have named refusal tests. No product scope or
permission has expanded; revised profile application remains gated.

## Remaining prerequisites, not concealed implementation findings

Exact baseline source/mapping is not acquired or proven. If unavailable, explicit
substitute review is required. Complete B/P JSON/hashes/diff do not yet exist and
must be reviewed before changed-policy execution. No full bwrap namespace/mount
allowlist is proposed by this no-op stage; no MT qualification is implied.

Verification here is documentation/link/interface/coverage review, not executed
implementation tests. No probes, package acquisition, source fetch, profile
synthesis, policy changes or production edits occurred during this self-review.
Plan is ready for user review; native execution remains selected. Final fresh
whole-branch review remains a separate pending gate.
