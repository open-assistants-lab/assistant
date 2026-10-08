# Namespace policy artifact review — accepted baseline; candidate awaiting execution review

Plan: [namespace attribution](../superpowers/plans/2026-10-07-linux-namespace-policy-attribution.md).
Status: **baseline accepted after author self-review; Task 1 complete. Candidate P
prepared and checksum-verified; custom Docker policy execution remains gated.**

## Installed target and pinned public source

- Docker Desktop 4.92.0; engine 29.8.0; engine GitCommit `3ce5872`.
- Engine platform reports Linux/arm64; native diagnostic ABI is aarch64.
- Kernel `7.0.12-linuxkit`; engine reports `seccomp,profile=builtin`.
- Public Moby `docker-v29.8.0` resolves to annotated tag object
  `dc4db3d292c317ca216dae9301dffc935c8d7680`, peeled source commit
  `3ce5872b7950c63ba2ffbc5123101019ff3e6682`, matching engine commit prefix.
- Vendored profile module: `github.com/moby/profiles/seccomp v0.2.3`.
- No tag signature verification or running-binary attestation is claimed. Git
  acquisition used an isolated HOME, disabled credential helpers/prompts/hooks,
  pinned commit, no checkout and no source/build execution.

## Proposed byte-frozen B

Source: [vendored default JSON at the pinned engine commit](https://github.com/moby/moby/blob/3ce5872b7950c63ba2ffbc5123101019ff3e6682/vendor/github.com/moby/profiles/seccomp/default.json).

- Git blob: `ea5a494afb8d64898fa0f4f47ae0c4f5ba9cbbc9`.
- Raw length: **13,470 bytes**; **33 syscall rules**.
- Raw SHA-256:
  `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`.
- Following user acceptance of the documented limitation, exact B bytes are frozen
  at `tests/refactor/linux/fixtures/seccomp/docker-29.8.0-baseline.json` with
  `baseline-provenance.json`, commit `37b07acf`. Original acquisition remains in
  the plan execution workspace; no runtime policy application occurred.
- Default action is `SCMP_ACT_ERRNO`; arch map includes `SCMP_ARCH_AARCH64`/ARM.
- Baseline namespace-related ALLOW group (including unshare/mount) is conditional
  on bounding `CAP_SYS_ADMIN`. The probe drops all capabilities.
- Ordinary aarch64 clone ALLOW uses argument0 mask `2114060288` with namespace
  bits excluded; s390 has a separate conditional argument1 rule.
- Non-admin clone3 returns ENOSYS (38). Existing conditions/denies must be preserved.

## Mapping limitation requiring explicit review

[Daemon source](https://github.com/moby/moby/blob/3ce5872b7950c63ba2ffbc5123101019ff3e6682/daemon/seccomp_linux.go)
selects `seccomp.GetDefaultProfile` for the builtin profile; the matching vendored
loader calls compiled `DefaultProfile()` for D and decodes JSON for a custom B.
The reviewed JSON is therefore a **version-linked upstream snapshot**, not an
export of the installed kernel filter. A matching release commit and fourteen-call
behavioral comparison do not independently prove complete runtime/binary/filter
equivalence. Do not label it a byte-exact export or an authenticated approval.

**Accepted decision:** user proceeded after the baseline self-review, accepting
this pinned version-linked JSON for comparison with that limitation. Recorded as
`substitute-reviewed`, not an exact live-filter export. The sole no-op candidate
and complete B/P hashes/diff are now in the [execution review](2026-10-07-namespace-noop-policy-review.md);
no custom policy has been applied.

## Implemented pure checks and verification

`tests/refactor/linux/seccomp_review.py` validates raw checksum, deny default,
JSON/rule/condition container types, duplicate keys, non-finite values and pinned
provenance record syntax. Caller strings cannot authenticate source mapping or
operator approval. All nonmodified baseline fields/bytes are preserved.

Observed initial RED: **41 missing-implementation failures**. Initial GREEN:
**41 passed**. Numeric exponent overflow (`1e999`) then reproduced an additional
refusal gap: **1 failed, 41 passed**, followed by finite-float rejection. Final new
validation tests: **42 passed** within the final targeted selection.

Initial whole-suite commands were not green (retained evidence):

- First full run: **95 failed, 3,811 passed, 31 skipped, 69 warnings**, exit1;
  repeated native subprocess SIGSEGV stacks. First stack includes PostHog consumer,
  OpenAI platform-header thread and `platform.processor` subprocess creation.
  Every failure name is retained in the [companion failure inventory](2026-10-07-namespace-attribution-full-failures.txt).
- Isolated new-validator/sandbox/write-budget/tool-contract selection:
  **113 passed, 4 skipped**, exit0.
- Same full verifier excluding only the newly added validation tests:
  **1 failed, 3,863 passed, 31 skipped, 69 warnings**, exit1.
  Failure: `tests/sdk/test_custom_tool_timeout.py::test_timeout_raises_distinct_failure_with_elapsed[reconstructed]`;
  monkeypatched elapsed42.5 instead observed0.0. No elapsed assertion was weakened.
- Final full current run: **1 failed, 3,905 passed, 31 skipped, 70 warnings**, exit1.
  Failure: `tests/sdk/test_tenant_isolation_guard.py::test_the_escape_hatch_is_explicit_and_defaults_safe`;
  legacy operator opt-in expected `allow`, observed safer `ask`. No private-resource
  protection or runtime guard was weakened to make this test green.
- Final new-validator/tenant-guard/custom-timeout selection:
  **83 passed**, exit0. This is not a full-suite pass or definitive failure attribution.

Raw logs and all named failure inventories remain in this plan's execution
workspace. Similar historical native crashes/order sensitivity are recorded in the
foundation audit; their exact root cause is unresolved. No unrelated product fix,
process cleanup or test suppression was applied.

## Subsequent author self-review

The [checkpoint review](2026-10-07-namespace-baseline-code-review.md) found/fixed a
provenance gap: source URLs were not bound to their claimed pinned revision/repository.
Eight refusal cases were observed RED before canonical pinned-source enforcement.
Targeted tests now **52 passed**, exit0. Fresh full offline rerun:
**3,916 passed, 31 skipped, 69 warnings, 270.72s, exit0**. Earlier failures above are
not erased or claimed fixed; their exact causes remain unresolved.
No baseline acceptance, candidate synthesis or policy application was inferred.

## Outstanding gates

Review exact prepared B/P bytes/diff and authorize the no-op comparison if desired.
Task 3 comparison implementation/actual execution and final whole-branch review
remain pending. Latest full suite after Task 2: **3,933 passed, 31 skipped, 69 warnings,
266.78s, exit0**; prior intermittent failures remain unresolved. No namespace-policy
qualification, hardened MT/native readiness or release claim. No Docker policy changed.
