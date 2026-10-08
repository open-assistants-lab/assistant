# Bounded macOS subprocess crash investigation

Status: **bounded probes complete; native crash not reproduced, cause inconclusive.
No product fix selected; latest full-suite verification remains blocked.**
Plan: [bounded investigation](../superpowers/plans/2026-10-07-macos-subprocess-crash-investigation.md).
User approved native/inline execution and keeping observability access offline.

## Task 1 — existing evidence

Clean linked worktree `refactor/execution-authority`, start `462d9108`.
Existing interpreter CPython3.13.10/macOS aarch64; installed OpenAI2.37.0,
httpx0.28.1, Langfuse4.14.1, pytest9.0.2. No installs, SDK/client invocation,
network access, customer stores, Docker calls or runtime changes in this indexing.
Public installed package versions were read from metadata, not live services.

Original input full log: the plan-referenced
`.superpowers/sdd/2026-10-07-linux-namespace-policy-attribution/task-3-full-final.log`.
New evidence index lives only in this plan's owned ignored workspace. Decisive
stack excerpts and result summaries are recorded here for durable review.

### First three distinct current-thread stacks

| Original log line | Observed stack | What it does NOT establish |
|---|---|---|
|12|stdlib subprocess1921 `_fork_exec` ← platform905 `from_subprocess` (`uname -p`) ← `platform.platform` ← OpenAI2102 `get_platform` ← thread-pool worker|Not proof OpenAI, background threading or SDK telemetry caused the native fault|
|107|stdlib subprocess1921 `_fork_exec` ← platform738 `_syscmd_file` (`file -b` path) ← `platform.architecture/platform` ← OpenAI2102 `get_platform` ← thread-pool worker|Two platform helper subprocesses are implicated; not proof every platform call fails|
|192|stdlib subprocess1921 `_fork_exec` ← `src/storage/paths.py:135 _ensure_git` ← SDK runner/run service ← synthetic conversation endpoint ← AnyIO portal thread|Fault is not confined to sandbox preexec or platform metadata calls|

Other threads appear in copied crash traces, including a PostHog worker. Thread
presence is not network/export evidence or a causal attribution. Trace text alone
also does not identify each crashing process PID or distinguish all copied child
stacks from parent activity. Controlled probes must record markers/return codes.

### Spawn selection from installed source

CPython `subprocess.py:1861–1875` permits posix_spawn only with a directory-bearing
executable, no preexec, allowed close-FD behavior, no pass_fds/cwd/new session or
explicit UID/GID/groups/umask changes (plus compatible pipe/process-group values).
`platform.py:905` uses bare `uname`; `_syscmd_file` uses bare `file`. Their lack of
an executable directory already excludes that posix_spawn fast path. Fallback
uses `_fork_exec` at1921; Python's `_USE_VFORK=True` is passed to the C function,
not proof that C uses vfork for every observed spawn. No selection is changed here.

SoftBackend (`src/sdk/sandbox.py:362,399–406`) instead supplies a preexec hook
for limits/session/UID drop. That also excludes posix_spawn but adds different
security semantics. Removing preexec is neither justified nor authorized by this
trace. Installed SDK async request code at1611–1613 calls `get_platform` through
`asyncify`; constructing a synthetic client without requests does not necessarily
exercise that call, so the direct get_platform probe is a separate control.

### Existing results — preserve all outcomes

- Earlier normal full: **3969 passed,32 skipped,69 warnings,263.38s,exit0**.
- Latest full: **95 failed,3875 passed,32 skipped,69 warnings,274.03s,exit1**.
- Isolated new/sandbox/custom-timeout/desktop triage:
  **126 passed,4 skipped,3 warnings,12.83s,exit0**.

The latest95 named failures equal the earlier retained95-name set exactly.
This does not prove an exact cause or absence of a regression. Between the two
recent full runs the owned diagnostic cleanup acquired an exact-ID mismatch
refusal and its mocked regression; the comparison test acquired an operator
permission check on its opt-in-only path. These are differences to disclose,
not a causal verdict. No full rerun or suppression in this investigation.

## Hypotheses and discriminating controls

| Hypothesis, not finding | Planned control |
|---|---|
|Threaded stdlib spawning contributes|Fresh serial/threaded platform queries, same interpreter/guard/environment|
|SDK platform initialization or cache state contributes|Direct installed get_platform versus client construction; cold/warm only within fixed probe budget|
|Combined test collection/order/global state contributes|Fresh isolated probes and desktop/sandbox ordered slices in both orders, reduce only an observed failing sequence|
|Sandbox preexec-specific effects contribute to later failures|Unchanged SoftBackend echo control versus ordinary platform helper subprocesses; retain all security limits|

## Boundaries and next gate

At most16 guarded single-process probes (20-second child deadline) and6 sequential
pytest slices (120-second verifier deadline). Guard sanity is performed inside
planned probe children before SDK/project imports. No parent secrets or service
credentials; no external Langfuse/ClickStack connection. All timeout cleanup must
track exact owned processes, including SoftBackend's separate sessions; uncertainty
stops further launches. Only investigation evidence is authorized. A lasting fix
requires an observed RED regression and separate bounded-design review.

## Task 2 — actual bounded results

[Complete structured outcomes](2026-10-07-macos-spawn-bounded-results.json) preserve
exact argv, PID/start-time ownership records, guard/start/finish markers, return
codes, duration and every slice failure. Full per-run stdout/stderr remains in
this plan's ignored workspace. This is not a test pass used to erase prior failures.

All **16/16** fresh processes completed with exit0, no native fatal signature,
no timeout and no tracked owned leftovers. Each checked external DNS denial before
SDK/project imports. Two fresh repetitions of each serial/threaded case:
stdlib platform.platform; installed SDK get_platform; synthetic client constructor/
close with no request; unchanged SoftSandboxBackend `/bin/echo spawn-sentinel`.
No crash occurred to justify a cold/warm reduction; no extra probe was launched.
No parent credentials, deployments or external observability service accessed.

|Slice: explicit execution selection (all use120-second verifier deadline)|Observed outcome|
|---|---|
|1: desktop-server file → sandbox file|59 passed,4 skipped,3 warnings,19.78s,exit0|
|2: sandbox file → desktop-server file|59 passed,4 skipped,3 warnings,16.36s,exit0|
|3: whole tests collection, `-k 'test_second_launch_exits_when_lock_held or test_runs_argv'`|2 passed,4000 deselected,3 warnings,2.89s,exit0|
|4: API agent-loop file → sandbox file|41 passed,4 skipped,1 warning,19.10s,exit0|
|5: API family → NullBackend ordinary-argv sentinel|**2 failed**,637 passed,6 skipped,16 warnings,41.05s,exit1|
|6: whole tests collection, `-k 'api or test_sandbox'`|715 passed,10 skipped,3277 deselected,18 warnings,102.97s,exit0|

**No native fatal/SIGSEGV signature appeared in any of the six slices.** These
are keyword/path-selected slices, not a full-suite rerun. Whole collection alone
also did not reproduce the issue in the two-sentinel run.

### New ordinary failures — retained, not repaired

Slice5 produced404 where200 was expected in:

- `tests/api/test_subagents.py::TestSubagentV1Invocations::test_create_start_list_detail_and_instruct_subagent_job`
- `tests/api/test_subagents.py::TestSubagentV1Invocations::test_subagent_jobs_are_user_level_across_workspace_ids`

They are not native crashes and not among the historical95. The broader selection
in slice6 passed; different collection/selected execution contexts prevent treating
that pass as a fix or attributing the difference to any specific fixture. No
unrelated API repair or extra retry performed.

### Investigation limits and harness correction

A diagnostic-only setup import initially resolved `scripts` to an unrelated
installed editable package at HybridDB rather than this repo's namespace folder.
It failed before any probe launched or `.env` shadow was created. The throwaway
driver now loads the exact local verifier file via importlib; the verifier/product
code was not changed. A summary parser also initially expected plural warnings;
corrected to retain the singular-warning slice4 result, without rerunning tests.

The budget is exhausted: **16/16 probes and6/6 slices**. No seventh slice, full
rerun, dependency change, telemetry disablement, sandbox limit/UID weakening,
Docker change or speculative cache-warming fix. All earlier95 full failures remain
current unresolved evidence. Task3 must recommend the next discriminating step,
not infer a root cause from successful controls.

## Task 3 — recommendation and explicit next gate

**No exact production fix is justified yet.** Standalone threaded spawning,
standalone SDK metadata queries, client construction without requests, API prefix
and collection alone are insufficient triggers in the cases observed. This is a
negative result for these controls, not proof that any component is safe in every
combined context. The ordinary404 failures are a separate recorded concern.

The next cheapest discriminating question is **composition**: does constructing
an HTTP client and then asking the SDK for platform metadata in the same process
trigger the native fault? Current probes exercised those components independently;
SDK async request source performs metadata detection after client initialization.
No API request is needed to examine this boundary.

### Follow-up proposed at the prior checkpoint (subsequently approved/executed below)

New proposed budget: **8 processes maximum,20-second deadline each**, sequential;
no additional pytest slices or full-suite run in this follow-up. Four fixed cases,
each repeated twice, using the same installed environment and exact owned cleanup:

1. Construct synthetic OpenAI client, call installed get_platform serially, then
   close the owned client in finally.
2. Construct synthetic client, call get_platform in one joined worker, then close.
3. Call stdlib platform.platform before constructing that client; then repeat
   case1 as an explicit warm-cache control.
4. Same warm-cache control followed by case2.

Keep the client open during metadata detection to reflect the SDK request-stage
ordering. Keep the same dummy key/local closed-port base URL, guard installed
before imports, blank owned .env, synthetic roots and no requests/export endpoints.
Cache warming is only a discriminating experiment, never a proposed global cure.
Record startup/thread/finish markers, process roles/return codes and copied fatal
stacks. If an owned process crashes and macOS creates a native report, read only
its confirmed PID/time-matched report; do not sweep unrelated/customer diagnostics.
Stop for cleanup uncertainty. Preserve every failed control; no retry-until-green.

If this reproduces, use the remaining slots only to confirm/reduce that exact
sequence and propose a lasting regression before a minimal bounded fix. If all
cases pass, report inconclusive again and review wider runtime-context reduction
separately. Do not guess that SDK, proxies, caches, preexec or telemetry is the cause.
No dependency update, spawn-mode override, skipped test or security reduction is
included. A lasting fix still needs its exact regression to be observed RED and
its semantics reviewed before implementation; later general acceptance requires
sandbox/timeout/UID/telemetry/desktop checks, offline wheel/full-suite verification
and fresh-context whole-branch review.

### Evidence self-review and status

Author-only review checked every budget/result/failure name against the structured
record and logs; guard ordering and owned shadow absence were verified. No native
fault was observed in this pass, so native-cause/fix claims would be unjustified.
No runtime/test/library source changed; durable changes are investigation evidence.
The latest general full run remains95 failures; neither Linux namespace Task3 nor
the original authority foundation is cleared. No merge/push/release/customer use.
The proposed eight-process follow-up required separate approval, subsequently
received and executed as recorded below. No additional launch is authorized.

## Approved eight-process composition follow-up

User **“approve”** accepted the exact proposed experiment after `8ad82891`.
[Complete composition outcomes](2026-10-07-macos-spawn-composition-results.json)
preserve the separate8-process budget, argv, exact owned PID/start-time records,
guard/client/detection/closure markers, return codes and cleanup observations.
Original16-probe/6-slice evidence was not overwritten or rerun.

|Client open during SDK get_platform|Two fresh cold processes|Two fresh prewarmed processes|
|---|---|---|
|Serial detection|2 completed,exit0,MacOS|2 completed,exit0,MacOS|
|One joined detection worker|2 completed,exit0,MacOS|2 completed,exit0,MacOS|

All8 denied external DNS before SDK imports, kept the fake-key/local-closed-port
client open until detection finished, closed it in finally, and completed within
the20-second deadline. Actual worker names and warm-before-construction ordering
were checked from phase records, not inferred from case labels. No native fatal/
SIGSEGV stderr, timeout or tracked owned leftovers; owned .env shadow absent.
No API requests, Docker change, spawn-mode override, dependency installation,
telemetry suppression, product/library/test source change or cache-warming cure.
No owned crash occurred, so no macOS native reports were sought or unrelated
reports scanned. Author evidence self-review only; no branch-readiness claim.

**Conclusion remains inconclusive.** These cases did not support client construction
plus cold/threaded platform detection as a sufficient trigger in the tested fresh
processes. They do not rule out other runtime state, timing, framework initialization,
combined test sequence or native-runtime defects. Historical95 full failures and
two ordinary API404 failures remain unresolved.

### Recommended next step — separate scope, not run

Stop guessing with more isolated component combinations. If further debugging is
wanted, capture **one instrumented full-suite reproduction**, retaining exact
parent/helper PID associations, test-node boundaries and PID/time-matched macOS
native crash reports. Proposed limit: one full verifier launch, at most1800seconds,
using already approved offline build-cache reuse; no rerun-to-green. Instrumentation
must preserve actual spawn/security semantics and redact environment/argument
secrets; disclose its timing effects. A missing crash is still inconclusive.

This broader diagnostic run and its instrumentation need a reviewed bounded scope
before execution. No native crash or demonstrated regression currently justifies
an exact production fix. General verification and hardened MT remain blocked;
final fresh-context whole-branch review and authority foundation completion remain.
