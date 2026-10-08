# Authorized no-op seccomp attribution — actual result and verification block

## Permission and scope

User replied **“plz proceed”** to the exact B/P implementation/execution request
following `416410ad`. This authorizes this bounded owned disposable comparison,
not namespace-policy widening or daemon/customer changes. The operator-recorded
permission is evidence, not authentication by an approval string or CLI flag.
Exact B/P files/hashes/diff remain in the [artifact review](2026-10-07-namespace-noop-policy-review.md).

## Actual Linux result

Final run after cleanup self-review fix: **1 passed in4.48s, exit0**. Previous actual
run also passed in4.81s; its observations/cleanup evidence are retained in the owned
execution ledger. No additional policy or privilege allowance was introduced.

- Docker29.8.0, Linux aarch64, kernel7.0.12-linuxkit; local endpoint
  `unix:///Users/eddy/.docker/run/docker.sock`.
- Requested and resolved immutable image IDs both
  `sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6`.
- B SHA256 `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`.
- P SHA256 `6a744f09389794f4de85126a9c853d90a73226823270ac34cb1186a3ba3c5a42`.
- All three: UID/GID20001, effective capabilities0, no-new-privileges1,
  seccomp mode2, diagnostic process exit0, confirmed exact-ID/label cleanup.
- Ordinary native clone without namespace flags (SIGCHLD17) succeeds for D/B/P.
- D/B no-op `unshare(0)` returns EPERM. P no-op `unshare(0)` succeeds.
- Every namespace-bearing clone/unshare case returns EPERM for all three;
  fourteen observations each. D/B validated before P creation/start.
- Before/after successful full inventories are byte-equal: **nine unrelated
  containers remain stopped**; no owned container remains. No global pruning,
  image acquisition, extra mounts, Docker socket inside execution or .env startup.

[Complete structured evidence](2026-10-07-namespace-comparison-evidence.json)
contains each owned full CID, creation argv including the fixed diagnostic, requested/
resolved image, all42 observations, policy facts, exits, cleanup confirmations,
permission record and inventory count/hash. Customer/unrelated names are not copied
into published evidence. Source snapshots were exclusive owned0400 files, read by
Docker client and never mounted; owned verifier temporary roots were removed.

## Interpretation — intentionally narrow

This supports **a seccomp contribution to the observed no-op unshare denial**:
under matching runtime conditions, the sole rule change changed that outcome.
It does not authenticate the live daemon filter, establish full installed-filter
identity, or distinguish causes for namespace-bearing denials. It is not a
namespace-capable bubblewrap recipe or hardened MT qualification. The original
bwrap launch failure and eight-case filesystem/process/egress/restart/termination
suite remain unresolved/unrun. No fallback to privileged/unconfined execution.
A broader namespace recipe requires a subsequent design and exact-policy approval.

## Test-first implementation and self-review

- Initial32 pure tests RED for missing functions →32 GREEN; four missing/duplicate/
  invalid facts/stderr cases RED →36 GREEN. Actual diagnostics are separately
  opt-in and pin both reviewed hashes/image plus recorded operator permission.
- Self-review found inventory assumed Docker's exact-ID filter would return the
  selected ID. A labelled different full-ID response could pass cleanup checks.
  New mocked regression reproduced **1 failed/6 passed** before the explicit
  `ids == [cid]` refusal; now **37 pure tests passed, exit0**. This fault injection
  is not evidence of an actual Docker inventory mismatch or removed foreign resource.
- Scoped Ruff/mypy clean; existing `run_probe` still returns CompletedProcess.
  No production runtime/backend changes. Pure tests/mocked transport are not
  Linux boundary qualification.

## Latest general verification is NOT green

Earlier normal full run: **3,969 passed,32 skipped,69 warnings,263.38s,exit0**.
After final cleanup strengthening, latest full run: **95 failed,3,875 passed,
32 skipped,69 warnings,274.03s,exit1**, with repeated macOS subprocess SIGSEGV.
All95 named failures match the previously retained namespace-attribution full
failure set exactly. This is historical overlap, not proof of the exact cause or
absence of a regression. No failures were suppressed/erased and no pass rerun
was used to hide them. [Current named failures](2026-10-07-namespace-comparison-full-failures.txt);
full trace retained in `.superpowers/sdd/2026-10-07-linux-namespace-policy-attribution/task-3-full-final.log`.

Isolated triage of new tests plus sandbox/custom-timeout/desktop-server files:
**126 passed,4 skipped,3 warnings,12.83s,exit0**. It does not substitute for the full
suite. Task3 comparison evidence is recorded as a **checkpoint**, not unqualified
whole-task/whole-branch completion: latest full-suite verification remains blocked.
Final fresh-context whole-branch review, core foundation completion and native/MT
qualification remain pending. No merge/push/release/customer operation authorized.
