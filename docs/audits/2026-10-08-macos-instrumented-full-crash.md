# Single instrumented full-suite run — native failure reproduced

Status: **reproduced with helper PID/test-boundary evidence; cause and minimal
trigger still unresolved. No product fix or verification clearance.**

User “plz proceed” approved one instrumented full-suite run after the eight-case
composition controls. Start `c78409ff`, clean isolated refactor/execution-authority
worktree. Full launch budget **one**, verifier deadline1800seconds; no rerun.
No external observability connection, customer credentials/stores, Docker changes,
dependency acquisition or installed/product/test-source edits.

## Actual result and retained failures

**95 failed,3,875 passed,32 skipped,69 warnings,284.18s,exit1.**
All95 final-summary test identities exactly match the previously retained95 set.
[Every failed identity](2026-10-07-macos-instrumented-full-failures.txt) is preserved.
This reproduces the native failure pattern, not an explanation for each assertion
or proof that all95 have one cause. The separate earlier API404 failures remain
recorded in the [bounded investigation](2026-10-07-macos-subprocess-crash-investigation.md).
No repeated run until green or failure suppression.

[Structured evidence](2026-10-07-macos-instrumented-full-evidence.json) retains
approval/argv, sanitized helper sequences, parent/result/failure-set checks and
report correlation limits. Complete log and12,650 PID events stay in the owned
ignored investigation workspace; original probe/slice evidence is untouched.

## First observed transition

Parent pytest PID **48731** survived to session-finish exit1. Instrumented Popen
exit records contain **one exit0 and1,544 distinct helper exits -11 (SIGSEGV)**.
This is helper-level evidence, not a claim the parent Python process crashed.

All first three spawns were attributed at initiation to:
`tests/api/test_agent_loop.py::TestMessageEndpoint::test_message_returns_response`.

|Order|Helper PID/category|Recorded outcome|
|---|---|---|
|1|48732 / git|exit0; started with cwd supplied, no preexec or new session|
|2|48739 / uname|exit-11; no cwd/preexec/new session, no explicit environment|
|3|48741 / file|exit-11; no cwd/preexec/new session, explicit environment|

The uname spawn began about12seconds after the successful git helper. Its source
Python fault stack is the previously observed SDK get_platform → platform helper
→ `_fork_exec` path in a worker. Later failures include git, Python, uv, echo,
sh and other executable categories. Of the observed SIGSEGV helper exits,
**1,432 were git**,1 uname,1 file,22 Python,1 uv,6 echo,4 sh,1 sleep and76 other.
The range of failing helper categories argues against treating this only as a
missing `echo` binary or a bubblewrap/namespace denial. It does not identify the
native C fault, registered fork callbacks or exact initialization step responsible.

## Instrumentation and privacy limitations

A temporary pytest plugin in the owned ignored workspace delegates the original
`Popen.__init__` and `_handle_exitstatus` with unchanged arguments/results/exceptions.
It records initiation token, parent/helper PID, epoch time, thread ID, test base,
allowlisted executable category, argument count and non-value spawn flags. No argv
contents, environment values, exception messages or test parameter values are
written into PID capture. Full pytest text may contain synthetic fixture arguments;
there were no inherited deployment credentials. Log append uses no Python mutex.

Hooks were restored at pytest unconfigure; owned .env shadow removed. Installed
pytest9 applies `-o pythonpath=<owned-workspace>` before loading `-p native_full_capture`.
Offline guard remains loaded first; public build-cache reuse remained offline.
These wrappers add I/O/timing and object bookkeeping; instrumentation can affect
races and is not a cure. Parameter values are omitted, limiting parameter-level
correlation; event order/helper IDs are preserved. Python copied-child fault stacks
are not a native C backtrace.

A summary parser initially mixed verbose/body lines with final failure identities;
validation now reads only the final short-test-summary section. No test rerun was
performed to repair this evidence-parser issue.

## Native report correlation

Metadata-only search covered standard per-user/system DiagnosticReports directories
and their Retired directories. Process-name/time filtering yielded **39 candidate
headers**, but **zero owned helper PID/time matches**, including one delayed rescan
for report-write latency. Unmatched report bodies were not read/copied. This is not
proof native reports were never generated; no authoritative native stack could be
attached to these captured helpers within the searched metadata/window.

Do not borrow another process's report to explain this failure. No debugger attach,
security toggle, global diagnostic cleanup or scan of unrelated customer contents.

## Next bounded reduction — proposed, not authorized/run here

The first crashing helper already occurs in the first agent-loop test, before the
later95 failed assertions. Reduce around that **observed first node**, rather than
repeat the entire suite or guess another component fix.

Proposed next budget: **two fresh verifier launches,120seconds each**, sequential:

1. Keep full tests collection, execute only that first agent-loop test via its
   keyword selection; preserve the transparent PID capture and offline guard.
2. Execute only its exact nodeid with the same capture/guard/environment.

Both runs must retain positive/negative outcomes and exact collection selection;
no retry-on-failure, no full-suite rerun, no changed spawn mode/security/telemetry.
If the full-collection single-node case reproduces, reduce collection dependencies
in a later separately bounded step. If neither reproduces, report uncertainty and
review the timing/runtime-context difference rather than prescribe a speculative
fix. Native-stack acquisition, if needed, requires a separately reviewed owned-PID
method, not privileged attachment or an unrelated report.

No current evidence justifies a production change. A lasting fix still requires
an observed RED regression and reviewed semantics; original authority foundation,
Linux qualification, receipts/recovery/native readiness and fresh-context whole-
branch review remain pending. No merge/push/release/customer use authorized.
