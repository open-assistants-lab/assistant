# Approved first-node reduction — no native failure reproduced

User “yes plz” approved two fresh120-second verifier launches after `6b8e3f27`.
Exactly two were run, sequentially, with the same offline guard and temporary
PID-capture plugin as the [instrumented full run](2026-10-08-macos-instrumented-full-crash.md).
Only capture module/log names changed. No product/library/test-source changes,
cache-wheel/full-suite run, API credentials, customer stores, Docker change,
installation, telemetry suppression, spawn-mode change or retry-to-green.

Target: `tests/api/test_agent_loop.py::TestMessageEndpoint::test_message_returns_response`.
This is the first **native-fault-associated node**, not a previously failed test
assertion. Helper statuses are evaluated separately from pytest pass/fail.

[Complete sanitized evidence](2026-10-08-macos-first-node-reduction-evidence.json)
retains launch selections, parent/helper PIDs, phases, flags and results. Original
full/probe/slice data remain unchanged in the owned ignored workspace.

|Run|Selection|Pytest outcome|Observed helper exits|
|---|---|---|---|
|1|Full tests collection; `-k 'TestMessageEndpoint and test_message_returns_response'`|1 passed,4001 deselected,3 warnings,16.87s,exit0|git54849,uname54851,file54852: all exit0; parent54848|
|2|Exact nodeid only|1 passed,1 warning,3.37s,exit0|git54860 exit0; parent54859; no uname/file captured|

Both parents reached session finish and restored capture hooks. No fatal Python
stderr, SIGSEGV helper exit or deadline termination. Owned blank .env shadow absent
between runs and afterward. No native fault occurred in these reductions, so no new
native-report scan was undertaken. The temporary wrappers delegate unchanged
Popen behavior; their I/O/timing effects and parameter-redaction limits still apply.

## What this does and does not establish

- Run1 reproduces collection scope and the first observed helper-category sequence,
  but not the native failure. Mere full collection plus execution of that single
  node did not reproduce in this process; this is not a proof the node is always safe.
- Run2 is **not an equivalent successful platform-detection control**: no uname/file
  helper launch was observed. Selection/import/cache/runtime differences may matter;
  the reason that path differs is not established by these records.
- The full run still has95 failed test identities and1,544 SIGSEGV helper exits.
  Neither passing reduction is a fix or green general-verification result.
- No minimal reproducer or exact native C cause has been identified. No production
  change, global cache-warming, telemetry disablement or sandbox weakening is justified.

## Stop and next investigation direction

The approved two-launch budget is exhausted. No third launch or full retry.
Instead of further passing slice guesses, compare recorded initialization/import/
fixture/selection context before the successful first git helper and SDK platform
metadata in the full versus reduced runs. Any further execution needs a new bounded
scope with an explicit hypothesis and added evidence it can collect. If a native
stack is needed, design a safe owned-PID capture method; do not attach privileged
or attribute unrelated reports. No new execution recipe is authorized here.

Authority foundation, hardened MT, receipts/recovery, native readiness and fresh-
context whole-branch review remain pending. Work is not ready for integration.
