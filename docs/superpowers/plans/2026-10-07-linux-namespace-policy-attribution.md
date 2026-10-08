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
- Create `tests/refactor/linux/seccomp_comparison.py`: diagnostic parsing, typed run facts, ordered comparison and profile snapshots, not business/security receipts.
- Create `tests/refactor/test_seccomp_comparison.py`: ordinary offline parser/refusal tests.
- Create `tests/refactor/linux/test_namespace_policy_comparison.py`: separately authorized actual D/B/P comparison.
- Modify `tests/refactor/linux/probe_helpers.py`, `conftest.py`, `namespace_diagnostic.py` only for fixed reviewed profile input, opt-in comparison and structured evidence.
- Frozen approved JSON files belong in `tests/refactor/linux/fixtures/seccomp/` only after provenance review; transient unapproved acquisitions stay in the ignored execution workspace. Never ship these as production defaults.

### Task 1: Freeze and verify baseline provenance

**Files:** create `tests/refactor/linux/seccomp_review.py`, `tests/refactor/test_seccomp_review.py`, `docs/audits/2026-10-07-namespace-policy-artifact-review.md`; add approved baseline under `tests/refactor/linux/fixtures/seccomp/` only after its provenance gate.

**Interfaces:** `load_baseline(raw: bytes, expected_sha256: str) -> dict[str, object]`; `validate_provenance_record(record: dict[str, str], engine_version: str, engine_commit: str, architecture: str, profile_sha256: str) -> None` in `seccomp_review.py`. No source fetch or approval booleans inside these functions. Provenance record fields: `source_url`, `source_revision` (full 40/64-hex commit), `profile_sha256` (64-hex), `engine_version`, `engine_commit`, `architecture`, `mapping_kind` (`exact` or `substitute-reviewed`), `mapping_evidence`, `review_reference`. String/schema validation does not authenticate source mapping or user approval; those remain explicit recorded operator review.

- [ ] Read engine identity/source mapping with existing read-only Docker metadata. Locate an operator-supplied or commit-pinned upstream baseline; record source revision, raw SHA-256, engine version/build commit, aarch64 support and how mapping was established in the artifact review. Do not use raw scraped text as executable JSON. If exact mapping is unavailable, label it a substitute and STOP for user approval before synthesis/application; no invented mapping.
- [ ] Write failing offline tests: `test_hash_mismatch_rejected`, `test_duplicate_keys_rejected`, `test_permissive_default_rejected`, `test_malformed_syscall_rules_rejected`, `test_nonfinite_json_rejected`, `test_valid_baseline_bytes_unchanged`, `test_unpinned_source_revision_rejected`, `test_engine_mapping_mismatch_rejected`, `test_unreviewed_substitute_rejected`. Assert ValueError on refusals; accepted input bytes and all decoded fields remain unchanged.
- [ ] Run `python3 scripts/verify_refactor.py -- tests/refactor/test_seccomp_review.py -q`; observe named missing-implementation failures.
- [ ] Implement a strict 64-hex expected hash and hash verification before decoding; reject duplicate keys at every JSON-object level and non-finite JSON constants. Require a top-level object, nonempty syscall-rule list, rule objects with nonempty string names/action, list/object types for any arguments/conditions, and `defaultAction == SCMP_ACT_ERRNO`; preserve unrecognized baseline fields rather than rewriting them. Provenance validation rejects moving refs, inconsistent supplied engine/hash/architecture fields, unknown mapping kinds and missing evidence/review references. Existing Docker baseline provenance is an operator review gate, not inferred by this parser or a caller string.
- [ ] Run tests GREEN, scoped Ruff/mypy and diff checks. Present baseline provenance for review; do not synthesize P until accepted.
- [ ] Commit named helper/tests/review evidence: `test: freeze reviewed seccomp baseline provenance`. Blocked acquisition/review is recorded as BLOCKED, not task complete.

### Task 2: Generate and review the exact no-op delta

**Files:** modify Task 1 helper/tests/artifact review; add candidate JSON under `tests/refactor/linux/fixtures/seccomp/` only after baseline review. No runtime source changes.

**Consumes:** reviewed B bytes/hash, Task 1 `load_baseline`.
**Produces:** `make_noop_unshare_profile(baseline: dict[str, object]) -> dict[str, object]`; `assert_noop_only_delta(baseline: dict[str, object], candidate: dict[str, object]) -> None`, both in `seccomp_review.py`.

- [ ] Write failing tests `test_adds_exact_noop_rule_without_mutation`, `test_explicit_unshare_denial_rejected`, `test_existing_diagnostic_rule_rejected`, `test_unconditional_unshare_allow_rejected`, `test_conditional_baseline_allow_preserved`, `test_extra_or_altered_rules_rejected`, `test_nonzero_or_bool_argument_rejected`.
  Assert the sole appended rule equals `{"names":["unshare"],"action":"SCMP_ACT_ALLOW","args":[{"index":0,"value":0,"op":"SCMP_CMP_EQ"}]}`; B unchanged; modified default/architectures/conditions/clone rules and broadened flags raise ValueError.
- [ ] Run the offline tests; observe expected missing functions/assertion failures.
- [ ] Implement deep-copy + one append only. Conservatively reject any existing unshare rule with action other than ALLOW, an already-identical diagnostic rule, or any unconditional unshare ALLOW. Preserve provenance-reviewed conditional ALLOW rules unchanged; their effective capability/architecture conditions remain part of B/D comparison. Do not resolve precedence, compile conditions or remove rules. `assert_noop_only_delta` independently reconstructs expected structure from B and requires exact P equality, including strict argument integer types (bool is not accepted as int).
- [ ] Run GREEN and static/diff checks. Save byte-frozen B, canonical P, both hashes and exact structural/JSON diff into the artifact review; include D/B/P launch argv, inherited-policy limitations and expected outcomes. Approval record must identify these exact bytes, not a moving filename.
- [ ] Commit reviewed artifacts/helper/tests: `test: prepare no-op-only seccomp attribution delta`.
- [ ] STOP: request user review of complete JSON/diff and explicit permission to run B/P in owned containers. The earlier Docker/build approvals do not satisfy this gate. Do not mark attribution complete.

### Task 3: Gated actual comparison and evidence

**Files:** create `tests/refactor/linux/seccomp_comparison.py`, `tests/refactor/test_seccomp_comparison.py`, `tests/refactor/linux/test_namespace_policy_comparison.py`; modify `tests/refactor/linux/probe_helpers.py`, `conftest.py`, `namespace_diagnostic.py` and the artifact audit.

**Consumes:** exact reviewed B/P hashes, explicit user execution authorization, existing `run_probe`, existing aarch64 syscall diagnostic.
**Produces in `seccomp_comparison.py`:**
- `DiagnosticRun` dataclass with `observations: list[dict[str, object]]`, `image_id: str`, `architecture: str`, `uid: int`, `gid: int`, `cap_effective: int`, `no_new_privileges: int`, `seccomp_mode: int`, `process_exit: int`, `cleanup_confirmed: bool`. This is throwaway diagnostic evidence, not product authorization/receipts.
- `parse_diagnostic(stdout: str) -> list[dict[str, object]]`.
- `assert_baseline_pair(default: DiagnosticRun, baseline: DiagnosticRun) -> None`.
- `assert_attribution_matrix(default: DiagnosticRun, baseline: DiagnosticRun, candidate: DiagnosticRun) -> None`.
- `prepare_reviewed_profiles(baseline_path: Path, candidate_path: Path, baseline_sha256: str, candidate_sha256: str, owned_dir: Path) -> tuple[Path, Path]`.
**Produces in `probe_helpers.py`:** `run_recorded_probe(script: str, *, docker_host: str, image: str = IMAGE, seccomp_profile: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]`. Preserve the existing `run_probe(...) -> CompletedProcess[str]` wrapper/return type for preflight tests; it delegates with no custom profile and discards extra facts. The recorded helper always captures Docker identity/cleanup facts; comparison-specific policy facts are required by the comparison wrapper, not by legacy scripts that do not emit them. Comparison invokes the recorded helper, not a changed legacy return type.

- [ ] Write RED offline tests: `test_matrix_requires_all_fourteen_cases`, `test_process_success_not_namespace_success`, `test_baseline_mismatch_prevents_candidate_launch`, `test_namespace_success_rejected`, `test_missing_or_duplicate_rows_rejected`, `test_diagnostic_duplicate_keys_or_trailing_text_rejected`, `test_malformed_or_contradictory_errno_rejected`, `test_changed_profile_refused_before_docker_launch`, `test_mutable_or_mixed_image_rejected`, `test_missing_or_wrong_policy_facts_rejected`, `test_comparison_requires_both_execution_flags`, `test_cleanup_does_not_remove_unlabelled_container`, `test_cleanup_api_error_is_uncertain_not_absent`, `test_cleanup_requires_confirmed_removal`. Pure mocked tests are not qualification proof.
- [ ] Run RED through verifier. Implement strict parsing: exactly the fourteen expected label/operation/flag combinations, one JSON object per row, no duplicate keys/nonfinite constants/stderr/trailing or extra lines. Expected row keys are exactly `operation`, `flags`, `succeeded`, `errno`, `reason`; strict bool/int types and errno/reason agreement. D/B: ordinary clone succeeds, all other rows EPERM. P: ordinary clone and no-op unshare succeed, all namespace rows EPERM. Validate run facts separately with strict integer/bool types (bool is not an integer): process exit0, aarch64, UID/GID20001, cap_effective0, no_new_privileges1, seccomp_mode2 and cleanup_confirmed exactly True. Require identical full immutable image IDs for all runs; a matching observation list does not replace these facts.
- [ ] Implement the recorded helper using owned `docker create`/`start --attach` and explicit cleanup so image/container facts can be collected before removal; preserve the same security argv and the existing wrapper interface. Add only `--security-opt seccomp=<verified absolute path>` for reviewed B/P and metadata capture for `DiagnosticRun`. Require a full immutable sha256 image reference, not mutable tags. Add independent pytest `--namespace-policy-comparison` opt-in plus baseline/candidate file/hash arguments. Both `--linux-probe` and this separate flag are required; ordinary opt-in/default tests never apply custom profiles. CLI flags are not an approval record: explicitly check recorded user authorization before invoking the actual command.
- [ ] Strengthen owned cleanup: a successful exact-ID Docker inventory returning no container confirms absence. An inventory/inspect/API error is not absence. Remove only matching full CID + generated ownership label, then confirm absence by another successful query. Missing CID file requires a successful exact generated-name/ownership inventory; never assume creation did not happen. Label mismatch, API failure or uncertain removal raises/refuses a successful diagnostic even if the child returned0; never globally prune or remove a mismatched container. Capture one mandatory `policy-facts ` JSON line from the fixed trusted diagnostic for UID/GID/capability/no-new-privileges/seccomp fields, and resolved native-platform image identity via Docker metadata before start/removal. The comparison wrapper rejects missing/duplicate/malformed fact lines and removes exactly that one line before strict syscall-row parsing. Retain the full immutable requested image reference and its resolved image identity; ensure identical resolution across D/B/P, without assuming manifest and platform/config digests are identical. No arbitrary script/extra flags are accepted by the comparison wrapper.
- [ ] Before any D/B/P launch verify reviewed hashes/delta, image/architecture and exact reviewed security argv. `prepare_reviewed_profiles` reads/hashes both files, applies Task 1/2 validation to those bytes, then exclusively creates owned 0400 snapshots and returns their paths. Source replacement after snapshot does not change submitted policy; output-file conflicts fail before launch. No source/profile path is mounted into the container.
- [ ] Run pure tests GREEN and the normal offline suite with authorized cached build dependencies; actual comparison stays skipped without separate permission. Unsupported architecture refuses before any syscall diagnostic. No skip counts as proof.
- [ ] Only after recorded permission, run `python3 scripts/verify_refactor.py -- tests/refactor/linux/test_namespace_policy_comparison.py --linux-probe --namespace-policy-comparison --docker-host <reviewed-local-unix-endpoint> --baseline-seccomp <reviewed-B> --baseline-sha256 <reviewed-hash> --noop-seccomp <reviewed-P> --noop-sha256 <reviewed-hash> -q`. Run D, then B; call `assert_baseline_pair` and STOP before P if observations/facts differ or cleanup is unconfirmed. Only then run P and `assert_attribution_matrix`. Record argv, engine/kernel/requested-and-resolved-image/hashes/results/exit/cleanup in the audit. The fourteen-case match establishes only equivalence for these diagnostic calls, not complete installed-filter equivalence.
- [ ] Stop on B/D mismatch, P no-op denial, unexpected namespace success, changed hashes, missing metadata or cleanup uncertainty; never widen permissions as recovery. Any broader bwrap policy needs a subsequent evidence-backed design review.
- [ ] Commit named tests/evidence: `test: record reviewed namespace policy attribution comparison`. This is not the original eight-case Linux qualification or MT readiness.

## Self-review and handoff

Coverage: spec intent/scope and unchanged diagnosis -> globals/Task 1; provenance -> Task 1;
sole structural change/conflicts -> Task 2; D/B/P comparisons/state/cleanup -> Task 3;
execution authorization, no namespace permissions, downstream full qualification -> Task 2/3 stop gates.
Types/signatures and five review-focus refusals are pinned above. No production profile,
helper, worker or new backend is selected. The approved native execution method is preserved.
User must review this plan before inline implementation; concrete security-policy execution
still requires its later separate authorization.
