# macOS Subprocess Crash Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Preserve the already selected native/inline method. Steps use checkbox (`- [ ]`) syntax for tracking.

**Execution status:** User approved native/inline investigation. Bounded16-probe/6-slice pass executed; native cause remains inconclusive. [Evidence and proposed follow-up](../../audits/2026-10-07-macos-subprocess-crash-investigation.md). No product fix or extra probe budget authorized.

**Goal:** Establish a reproducible, causally useful explanation for the macOS subprocess crashes blocking general verification, without weakening execution or test protections.

**Architecture:** This is a bounded diagnostic investigation, not a sandbox rewrite. First classify existing evidence, then compare isolated synthetic probes and ordered test slices. A lasting fix is deliberately not selected before reproduction; finish with evidence and a proposed exact regression/fix for review.

**Tech Stack:** Existing CPython3.13.10/macOS aarch64 environment, pytest, stdlib subprocess/platform/threading, installed OpenAI SDK, existing offline verifier and network guard. No dependency acquisition.

**Spec:** User-approved next-step scope: investigate the crashes/minimal triggering sequence, do not suppress failures or rerun until green, leave Docker security unchanged. Failure context: [comparison result](../../audits/2026-10-07-namespace-comparison-result.md) and [all95 failures](../../audits/2026-10-07-namespace-comparison-full-failures.txt). This plan authorizes no unidentified product fix.

## Global Constraints

- Work only in `.worktrees/execution-authority-impl`, branch `refactor/execution-authority`; planning base `b1e3dc29`. Preserve dirty main, other worktrees/processes and `uv.lock`.
- Use `scripts/verify_refactor.py` for pytest. Preserve its allowlisted environment, exclusive blank `.env` shadow, short owned `/tmp/ar-*` roots, synthetic stores and offline network guard. Do not read/inherit parent deployment secrets.
- Existing installed dependencies only. Wheel verification may reuse the already approved cache via `--build-cache /Users/eddy/.cache/uv`; no `uv sync`, upgrades/downloads or writes to operator cache.
- No Docker execution/policy changes, root/privileged/SYS_ADMIN/unconfined runs, disabled no-new-privileges, sandbox fallback, customer operations or integration/publication.
- No global `OTEL_SDK_DISABLED`, wholesale telemetry/provider disablement, global platform monkeypatch, skipped failing tests or signal swallowing as a proposed cure. Enable `PYTHONFAULTHANDLER=1` only in owned probe environments for trace capture; this is instrumentation, not a claimed fix.
- Preserve resource/write limits, UID-drop fail-closed behavior, process-group timeout/descendant termination and recording-exporter tests. Changing these needs a separately reviewed fix scope.
- Temporary probe code/evidence stays in the owned ignored ledger `.superpowers/sdd/2026-10-07-macos-subprocess-crash-investigation/`; it is diagnostic, not production readiness proof.
- Budget: at most16 fresh single-process probe launches, each20-second child timeout; at most6 pytest slice launches, each120-second verifier timeout. Sequential only. Stop/report inconclusive at the budget, not unbounded repeats. Full-suite reruns are not part of discovery.

## Review Focus

1. ChildSIGSEGV versus parent crash/ordinary assertion failure: record exact return codes, process role and stderr signature; do not collapse them to a generic failure.
2. A probe child does not inherit Python-level socket patches: install `tests.refactor.offline_guard` before any SDK/project import, use scrubbed environment and synthetic roots.
3. Background threads outlive a test: use a fresh owned process for each probe, record thread names/case boundaries; never kill unrelated processes.
4. Warm caches or altered ordering hide the crash: compare the same cases cold/warm and serial/threaded; keep all outcomes, including failed controls.
5. A one-off pass or small slice cannot clear full verification: report non-reproduction honestly, require an observed regression RED before a lasting fix, and retain the latest95-failure result.

## File/responsibility map

- Read only: `scripts/verify_refactor.py`, `tests/refactor/offline_guard.py`, `src/sdk/sandbox.py`, `tests/api/test_desktop_server.py`, `tests/sdk/test_sandbox.py`, `tests/sdk/test_custom_tool_timeout.py`, root/test-family conftests and installed stdlib/OpenAI SDK spawn/platform code.
- Create durable report: `docs/audits/2026-10-07-macos-subprocess-crash-investigation.md` — observations, commands, outcomes, minimal sequence or explicit inconclusive result and next gate.
- Create owned diagnostic artifacts under the ignored ledger: evidence index, guarded subprocess probe script and complete per-run logs/results. No product or installed-library edits.
- A lasting regression file/product edit is intentionally not allocated: select exact files only after the cause and regression are known, then obtain review of that bounded fix design.

---

### Task 1: Classify existing traces and establish controlled cases

**Consumes:** `task-3-full-final.log`, earlier failing/passing verification logs, all95 named failures, current source at planning base.
**Produces:** evidence index and report separating observed facts from hypotheses; no tests/product changes.

- [ ] Record clean-worktree status and interpreter/installed SDK versions locally; do not dump environment variables or credentials.
- [ ] Extract the first three distinct current-thread crash stacks, including source log offsets and surrounding thread/process evidence. The earliest observed stack is `openai._base_client.get_platform` → `platform.platform` → platform processor subprocess → stdlib `_execute_child`; record it as a lead, not a root-cause verdict.
- [ ] Trace that stack through installed SDK/stdlib source and compare sandbox `preexec_fn` callers. Determine which inputs select fork/posix-spawn without changing those selections. Do not claim sandbox-only failure when the earliest stack is a standard platform query.
- [ ] Normalize and compare the current95 names with the previous95 names; assert exact set equality, preserve original inventories and distinguish historical overlap from proof of causation.
- [ ] Index existing evidence: earlier3969-pass full run, latest95-failure full run, isolated126-pass triage. Record which files/commands differ; no full rerun.
- [ ] Write the evidence index/report with hypotheses in separate columns: threaded stdlib spawn; SDK platform initialization/caching; test-order/global-state interference; sandbox preexec-specific effects. Each hypothesis needs a discriminating next case, not a fix.
- [ ] Commit only the durable audit checkpoint: `docs: index macOS subprocess crash evidence`.

### Task 2: Execute guarded discriminating probes and bounded order reduction

**Consumes:** Task1's indexed stacks/hypotheses, exact existing verifier/guard/environment interfaces.
**Produces:** complete controlled results and a minimal reproducer if found; no product fix.

- [ ] Prepare throwaway probe code in the owned ledger. Import `tests.refactor.offline_guard` before third-party/project imports; use `verification_environment(root: Path, scratch: Path, environ: Mapping[str, str]) -> dict[str, str]` with owned roots, a blank owned root `.env` shadow and the installed interpreter. Preserve exclusive shadow ownership/inode checks. Do not use normal parent environment for probe children.
- [ ] Check the probe guard with a denied `socket.getaddrinfo('example.com',443)` attempt: expect the existing guard's OSError before external lookup. This is a guard sanity check, not OS egress proof. Record child startup/finish markers and return code; a missing finish marker is not success.
- [ ] Run these eight cases twice each in fresh processes, no parallel runs: stdlib `platform.platform()` serial/threaded; installed SDK `get_platform()` serial/threaded; synthetic OpenAI client construction serial/threaded (`api_key='offline-probe-not-a-real-key'`, `base_url='http://127.0.0.1:9'`, close owned client, no API request); existing SoftBackend local argv launch serial/threaded (`['/bin/echo','spawn-sentinel']`) using owned workspace and unchanged default bounds. Threaded cases use one joined worker; the outer owned process has a20-second deadline. Record every outcome, including timeout; no retry-on-failure beyond the fixed pair.
- [ ] For each case record exact argv, public version tuple, case/thread markers, child return code or timeout, stdout/stderr and fatal signature. No synthetic request is sent; no API key/environment from parent is available. Record the owned process tree before timeout termination. SoftBackend uses its own session/process group: do not assume killing the probe/verifier group also kills that child. Terminate/reap only confirmed owned IDs/groups, confirm their absence, and STOP if ownership/removal is uncertain; never global process cleanup or another launch after uncertainty.
- [ ] If a case crashes, reduce only that case first: compare cold versus deliberately warmed platform cache within the remaining budget, replacing unneeded cases rather than increasing the budget. Cache warming is an experimental control, not a production fix. Report which variable changes the failure; no conclusion from a single success.
- [ ] If individual cases do not reproduce, use up to6 ordered pytest slices through the existing verifier. Start with `tests/api/test_desktop_server.py` then `tests/sdk/test_sandbox.py`; compare with that order reversed. Include the known desktop lock sentinel and the sandbox ordinary argv sentinel. If a slice reproduces, reduce its ordered node list by halves while preserving order; otherwise use collection/earlier-family context from Task1 to select the next discriminating slice, not the full suite.
- [ ] Commands use `python3 scripts/verify_refactor.py --timeout 120 -- <explicit ordered paths/nodeids> -vv`; record the explicit selections and all failure names. No `--linux-probe` or custom-policy flags.
- [ ] At the budget, write either a reproducible sequence with observed repeats or an explicit inconclusive result. Do not invent a cause or claim full verification restored. Commit the durable report: `docs: record bounded macOS spawn reproduction`.

### Task 3: Review evidence and hand off a test-first fix scope

**Consumes:** Task2 complete result matrix/reproducer or inconclusive report.
**Produces:** a reviewed next-step recommendation; this plan stops before product implementation.

- [ ] If reproducible, identify the earliest responsible boundary and distinguish cause from later PATH/tool-resolution symptoms. List any proposed semantic/security effect of a fix, including limits, UID drop, environment isolation, signal/timeout receipts and thread lifetime.
- [ ] Present an exact bounded fix design for user review: existing source file/function, new regression test/node, controlled RED command and expected original failure, minimal proposed change and GREEN command. Do not write a regression that silently skips the trigger or weaken invariants to obtain GREEN.
- [ ] Require the regression to reproduce the observed original fault before implementation. If nondeterministic, propose a bounded deterministic seam test plus the same real probe as separate evidence; state what the seam cannot establish.
- [ ] If inconclusive, propose the next discriminating investigation with a new explicit budget. Do not offer a speculative fix or an unbounded run-until-green loop.
- [ ] After a separately approved and verified fix, future acceptance must include existing sandbox/timeout/telemetry/desktop checks, offline wheel/full-suite verification and fresh-context branch review. That acceptance is a later scope, not achieved by this plan.
- [ ] Self-review/commit the report and stop for the human's decision. Do not mark namespace Task3 or the original authority foundation complete on diagnostic evidence alone.

## Plan self-review and execution gate

Coverage: original95 failures and contradictory full/slice results →Task1; cold/warm/threaded/order and child guard/ownership →Task2; observed RED and invariant preservation before product fixes →Task3. No unknown fix body is presented as implementation-ready. This plan's retained outputs are evidence and a recommendation, not new runtime infrastructure.

**Approved investigation executed within budget; follow-up awaits separate approval.** The native/inline method was preserved. Task1 indexed evidence; Task2 completed its16-probe/6-slice budget and retained negative404 outcomes; Task3 reported inconclusive native cause and proposed eight compositional probes. Existing latest full-suite verification remains blocked. The ledger and report carry step evidence; no hypothetical product fix is authorized by this plan.
