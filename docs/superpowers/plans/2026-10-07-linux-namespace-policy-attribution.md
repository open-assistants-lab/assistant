# Linux Namespace Policy Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a reviewable no-op-only seccomp delta and, only after explicit security authorization, compare installed/default/baseline/candidate syscall results.

**Architecture:** Throwaway qualification tooling alongside existing Linux probes, not production policy tooling. Preserve the one-engine/bubblewrap candidate and unchanged runtime; freeze a provenance-reviewed baseline, generate one structural addition, then run a separately gated three-way diagnostic. Missing provenance, unexpected namespace success or any qualification mismatch stops progression.

**Tech Stack:** Existing Python 3.13/pytest, hashlib/json, cached Linux aarch64 public image and Docker client. No new installed packages or product dependencies.

**Spec:** [approved attribution design](../specs/2026-10-07-linux-namespace-policy-attribution-design.md).

## Global Constraints

- Native/inline execution remains selected; no new delegation/execution-method choice is required.
- Applying a changed profile requires user review of complete JSON/diff/hashes and separately explicit execution authorization.
- The only candidate addition is `unshare`, `SCMP_ACT_ALLOW`, argument index 0, `SCMP_CMP_EQ`, value 0. No namespace permission is added.
- Preserve all baseline rules, default action/errno, architectures and conditional rules. Reject conflicts instead of removing denies.
- No privileged mode, SYS_ADMIN, root execution, seccomp-unconfined, sysctl change or disabled no-new-privileges.
- All actual runs use UID/GID 20001, all capabilities dropped, read-only root, network none, owned temporary storage, immutable cached image and pull never.
- No customer/source/.env/socket mounts; Docker policy is loaded by the client, not mounted into execution. No existing-container or daemon-wide policy modifications.
- Native clone diagnostic is aarch64-only; refuse unsupported architecture, never guess ABI/syscall numbers.
- No runtime workers, new quota scheme, MT readiness, helper/backend fallback, merge/push/release or automatic next-stage namespace policy.

## Review Focus

1. Duplicate JSON keys or hash mismatch must not hide a baseline rule change — Task 1.
2. A moving upstream source or unverified engine mapping must not be described as the installed default — Task 1.
3. Conflicting unshare denial or a pre-existing diagnostic addition must not be silently replaced/broadened — Task 2.
4. Changed files between review and execution must fail before Docker launch — Task 3.
5. Diagnostic process exit 0, mixed-image comparisons or extra namespace success must not count as attribution/containment — Task 3.

---

## File map and scope

- Create `tests/refactor/linux/seccomp_review.py`: pure, fail-closed baseline/delta validation; no Docker calls, source fetching or generic policy compiler.
- Create `tests/refactor/test_seccomp_review.py`: ordinary offline tests of those pure functions.
- Create `docs/audits/2026-10-07-namespace-policy-artifact-review.md`: provenance, complete artifact locations/hashes, structural diff and review status.
- Create `tests/refactor/linux/seccomp_comparison.py`: diagnostic parsing and matrix assertions, not business/security receipts.
- Create `tests/refactor/test_seccomp_comparison.py`: ordinary offline parser/refusal tests.
- Create `tests/refactor/linux/test_namespace_policy_comparison.py`: separately authorized actual D/B/P comparison.
- Modify `tests/refactor/linux/probe_helpers.py`, `conftest.py`, `namespace_diagnostic.py` only for fixed reviewed profile input, opt-in comparison and structured evidence.
- Frozen approved JSON files belong in `tests/refactor/linux/fixtures/seccomp/` only after provenance review; transient unapproved acquisitions stay in the ignored execution workspace. Never ship these as production defaults.

### Task 1: Freeze and verify baseline provenance

**Interfaces:** `load_baseline(raw: bytes, expected_sha256: str) -> dict[str, object]` in `seccomp_review.py`. No source fetch or approval booleans inside this function.

- [ ] Read engine identity/source mapping with existing read-only Docker metadata. Locate an operator-supplied or commit-pinned upstream baseline; record source revision, raw SHA-256, engine version/build commit, aarch64 support and how mapping was established in the artifact review. Do not use raw scraped text as executable JSON. If exact mapping is unavailable, label it a substitute and STOP for user approval before synthesis/application; no invented mapping.
- [ ] Write failing offline tests: `test_hash_mismatch_rejected`, `test_duplicate_keys_rejected`, `test_permissive_default_rejected`, `test_malformed_syscall_rules_rejected`, `test_valid_baseline_bytes_unchanged`. Assert ValueError on refusals; accepted input bytes and all decoded fields remain unchanged.
- [ ] Run `python3 scripts/verify_refactor.py -- tests/refactor/test_seccomp_review.py -q`; observe named missing-implementation failures.
- [ ] Implement hash verification before decoding, duplicate-key rejection at every JSON-object level, top-level object/syscalls-list/rule-object validation and `defaultAction == SCMP_ACT_ERRNO`; preserve unrecognized baseline fields rather than rewriting them. Existing Docker baseline provenance is an operator review gate, not inferred by this parser.
- [ ] Run tests GREEN, scoped Ruff/mypy and diff checks. Present baseline provenance for review; do not synthesize P until accepted.
- [ ] Commit named helper/tests/review evidence: `test: freeze reviewed seccomp baseline provenance`. Blocked acquisition/review is recorded as BLOCKED, not task complete.

### Task 2: Generate and review the exact no-op delta

**Consumes:** reviewed B bytes/hash, Task 1 `load_baseline`.
**Produces:** `make_noop_unshare_profile(baseline: dict[str, object]) -> dict[str, object]`; `assert_noop_only_delta(baseline: dict[str, object], candidate: dict[str, object]) -> None`, both in `seccomp_review.py`.

- [ ] Write failing tests `test_adds_exact_noop_rule_without_mutation`, `test_explicit_unshare_denial_rejected`, `test_existing_diagnostic_rule_rejected`, `test_extra_or_altered_rules_rejected`, `test_nonzero_or_bool_argument_rejected`.
  Assert the sole appended rule equals `{"names":["unshare"],"action":"SCMP_ACT_ALLOW","args":[{"index":0,"value":0,"op":"SCMP_CMP_EQ"}]}`; B unchanged; modified default/architectures/conditions/clone rules and broadened flags raise ValueError.
- [ ] Run the offline tests; observe expected missing functions/assertion failures.
- [ ] Implement deep-copy + one append only. Conservatively reject any existing unshare rule with action other than ALLOW and an already-identical unconditional diagnostic rule; do not resolve precedence or remove rules. `assert_noop_only_delta` independently reconstructs expected structure from B and requires exact P equality, including strict argument integer types (bool is not accepted as int).
- [ ] Run GREEN and static/diff checks. Save byte-frozen B, canonical P, both hashes and exact structural/JSON diff into the artifact review; include D/B/P launch argv, inherited-policy limitations and expected outcomes. Approval record must identify these exact bytes, not a moving filename.
- [ ] Commit reviewed artifacts/helper/tests: `test: prepare no-op-only seccomp attribution delta`.
- [ ] STOP: request user review of complete JSON/diff and explicit permission to run B/P in owned containers. The earlier Docker/build approvals do not satisfy this gate. Do not mark attribution complete.

### Task 3: Gated actual comparison and evidence

**Consumes:** exact reviewed B/P hashes, explicit user execution authorization, existing `run_probe`, existing aarch64 syscall diagnostic.
**Produces:** `parse_diagnostic(stdout: str) -> list[dict[str, object]]`; `assert_attribution_matrix(default: list[dict[str, object]], baseline: list[dict[str, object]], candidate: list[dict[str, object]]) -> None`; `prepare_reviewed_profiles(baseline_path: Path, candidate_path: Path, baseline_sha256: str, candidate_sha256: str, owned_dir: Path) -> tuple[Path, Path]` in `seccomp_comparison.py`.

- [ ] Write RED offline tests: `test_matrix_requires_all_fourteen_cases`, `test_process_success_not_namespace_success`, `test_baseline_mismatch_rejected`, `test_namespace_success_rejected`, `test_missing_or_duplicate_rows_rejected`, `test_malformed_or_contradictory_errno_rejected`, `test_changed_profile_refused_before_docker_launch`, `test_cleanup_does_not_remove_unlabelled_container`. D/B: ordinary clone succeeds, all other rows EPERM. P: ordinary clone and no-op unshare succeed, all namespace rows EPERM. Require exact operation/flag set, boolean success type and consistent errno.
- [ ] Run RED through verifier. Implement strict parsing of the existing labelled JSON rows (no eval or log-text inference) and exact matrix assertions. Keep errno/process exit separate.
- [ ] Extend `run_probe(..., seccomp_profile: Path | None = None)` with a single fixed additional `--security-opt seccomp=<verified absolute path>` only for reviewed B/P. Add independent pytest `--namespace-policy-comparison` opt-in and baseline/candidate file/hash arguments. Default tests and ordinary `--linux-probe` must not apply a custom profile; test this with mocked subprocess argv, explicitly not qualification evidence.
- [ ] Before each launch verify reviewed hashes and structural delta, same immutable image/architecture, declared capability/UID/security flags and ownership labels. `prepare_reviewed_profiles` reads and hashes both files, uses Task 1/2 validation on those exact bytes, then exclusively creates owned 0400 temporary files (no overwrite) and returns their paths. Copy exact reviewed bytes to owned read-only temporary files to avoid unreviewed source replacement between checking and invocation. No arbitrary extra Docker flags or untrusted script input.
- [ ] Run pure tests GREEN and the normal offline suite with authorized cached build dependencies; actual comparison stays skipped without the separate execution permission. No skip counts as proof.
- [ ] Only after recorded permission, run `python3 scripts/verify_refactor.py -- tests/refactor/linux/test_namespace_policy_comparison.py --linux-probe --namespace-policy-comparison --docker-host <reviewed-local-unix-endpoint> --baseline-seccomp <reviewed-B> --baseline-sha256 <reviewed-hash> --noop-seccomp <reviewed-P> --noop-sha256 <reviewed-hash> -q` using the same cached image for D/B/P. Require all syscall assertions plus UID/GID/capEff/noNewPrivs/seccomp checks. Record exact command/engine/kernel/image/profiles/outputs/exit/cleanup in the audit.
- [ ] Stop on B/D mismatch, P no-op denial, unexpected namespace success, changed hashes, missing policy metadata or cleanup uncertainty; never widen profile permissions as a recovery action. Any broader bwrap policy needs a subsequent evidence-backed design review.
- [ ] Commit named tests/evidence: `test: record reviewed namespace policy attribution comparison`. This is not the original eight-case Linux qualification or MT readiness.

## Self-review and handoff

Coverage: spec intent/scope and unchanged diagnosis -> globals/Task 1; provenance -> Task 1;
sole structural change/conflicts -> Task 2; D/B/P comparisons/state/cleanup -> Task 3;
execution authorization, no namespace permissions, downstream full qualification -> Task 2/3 stop gates.
Types/signatures and five review-focus refusals are pinned above. No production profile,
helper, worker or new backend is selected. The approved native execution method is preserved.
User must review this plan before inline implementation; concrete security-policy execution
still requires its later separate authorization.
