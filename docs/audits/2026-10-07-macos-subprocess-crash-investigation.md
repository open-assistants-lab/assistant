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
