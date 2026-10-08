# Native-runtime evidence review — author review, not independent

User “could u do it yourself” explicitly selected inline author review after
reviewer-launch capability remained unavailable. Reviewed at `b82b2f4f`.
Read-only source/log/status work only: no pytest/SDK/native/debugger launch, code
fix, dump, permission change or inference. This does **not** satisfy the pending
independent native review or final fresh-context whole-branch review.

## Findings and disposition

### 1. Blocker: no demonstrated native cause or qualified capture method

The PID-linked full run is strong evidence of **helper** failure: first git exit0,
then1544 distinct helper exits-11 while parent finishes. First fault stack anchors
subprocess.py:1921 `_fork_exec`, not the helper program's main. Copied parent thread
states locate context but are not a C instruction/backtrace. Missing owned native
report/core leaves library/callback/allocation/actual-fork-path causality unresolved.

Benign LLDB trial reached child SIGSTOP but never produced selected-child status or
native stack. Neither accepted enum nor interpreter launch qualifies child-follow.
No safe supported C-stack method established; no production fix justified.

### 2. Important diagnostic gap: controls do not match loaded native/runtime state

`native-full.log:108` lists26 extension modules at the earliest crash, including
numpy native kernels, grpc._cython.cygrpc, asyncpg protocol, SQLAlchemy extensions,
_cffi_backend and aiohttp components. Simple OpenAI/httpx/platform controls do not
record a corresponding pre-spawn native-image/runtime-state inventory. We cannot
assert equivalence, nor blame any named library merely because it is present.

The initial successful git spawn also limits the hypothesis: **mere collection or
native-module presence was not already sufficient to fail every helper**. State
could change between git and the first SDK metadata spawn; timing/initialization
or a process-wide fork-path interaction fits the observed cascade but is unproven.
Full-collection/one-node successful reduction prevents calling collection alone a
minimal trigger. Native image/loading changes, thread lifecycle and actual C spawn
path were not measured. Valid diagnostic blocker; not a dependency-removal mandate.

### 3. Source-proven non-equivalent assertion coverage

`tests/api/test_agent_loop.py:13–18` checks200 and a response field only. Router
`src/http/routers/conversation.py:848–850` converts exceptions to
`MessageResponse(response="", error=str(e))`; that assertion can pass on error.
Thus passing first-node tests do not establish successful model/native operations.
This is concrete source proof, not speculation about an HTTP response payload.

Two SDK modules mutate AGENT_MODEL on import; API session fixture reloads settings.
Default model is empty and provider factory refuses an absent model. Together with
observed exact-node git-only versus full-collection git/uname/file, this supports
coverage drift. Actual effective-model/payload values were not captured, so do not
claim their specific runtime values. Valid separate test-isolation/coverage issue;
**removing model mutation would hide a path, not repair the native fault**.

### 4. Spawn-path distinction rules out a blanket security-weakening cure

Installed subprocess.py:1861–1876 requires an executable dirname and no preexec_fn
for its posix_spawn fast path. Bare uname/file names exclude that branch. Soft
backend `src/sdk/sandbox.py:362–406` intentionally uses preexec for rlimits, setsid
and fail-closed UID/GID dropping. Those are security/termination semantics, not
incidental code to delete to make tests pass. Actual `_USE_VFORK=True` passed at
subprocess.py:1930 does not establish which C path succeeded/fell back internally.

Forced posix_spawn/vfork toggles, absolute-name platform monkeypatching, dropping
preexec, skipping tests or soft/null fallback are not evidenced fixes. Source-proven
boundary constraint; no change recommended.

### 5. Proxy-overlap hypothesis remains plausible but weakly discriminated

First copied-parent snapshot includes native SystemConfiguration discovery through
idle governance AsyncClient construction. Proxy environment scrubbing does not
suppress Darwin native fallback. But both purpose-built defaults measured query/
uname-init interval overlap and all8 default/control helpers exited0. Intervals are
not C instruction concurrency proof; simple observed composition was insufficient.

Idle dispatcher optimization or trust_env=False cannot be claimed as a fix; SDK
clients and other consumers can also discover proxies. Proxy/CF state is one
candidate among native-runtime interactions, not a proven isolated seam. Speculative
cause; retain, do not ship global proxy/TLS/telemetry policy changes.

### 6. Cleanup is a real blocker for more debugger execution

Exact read-only status still showed58406 with original start time, ppid1, stateZ.
Other recorded preflight targets were absent in earlier follow-up. Complete absence
is not confirmed. No further signal was sent; a zombie is not reaped by SIGKILL.

Preflight driver kills descendants/debugserver/target/debugger in reverse insertion
order, then repeats exact owned kill on remaining entries. It only waits/reaps its
immediate LLDB child. That teardown **does not ensure every tracee/fork parent gets
a chance to reap its child before its tracer is killed**. It is consistent with,
not proven cause of, retained zombie. Future method must define graceful checkpoint
release/detach/reap ordering and deadline escalation; do not restart PID1, kill
unrelated services or retroactively claim cleanup success. No teardown code changed.

## Ranked hypotheses (not diagnoses)

1. Native process-state/initialization or thread/fork interaction before exec:
   best fit to copied Python caller, varied helper categories and persistent cascade.
   Needs native instruction/image/callback evidence; C fork/vfork distinction unknown.
2. macOS SystemConfiguration interaction in that fuller state/timing: frame-grounded,
   not reproduced by simpler overlapping controls. Needs state/interleaving evidence.
3. Specific native extension fork/runtime defect: loaded-image evidence exists,
   identity/initialization/callback evidence missing. No specific library implicated.
4. Independent missing commands or namespace policy as general explanation: weak;
   helpers die by signal, git initially succeeds, failures precede sandbox commands.

## Recommended next method and go/no-go

**No further debugger/core-dump launch now.** First preserve/resolve owned cleanup
state without privilege/global cleanup, and qualify a supported capture method.
Do not infer debugger support or enable all-helper core dumping as a shortcut.

If operator later wants another execution-based investigation, the most defensible
fallback is **one full-context diagnostic run capped60seconds, stopped on first
observed SIGSEGV helper**, adding pre-spawn native-module/image identities, sanitized
spawn-flag summaries, model-present boolean and thread/initialization phase records.
Core remains0; no platform/proxy/spawn/preexec/telemetry semantics changed; no request
credentials/customer stores; exact PID ownership and graceful teardown reviewed
before launch. No retry if it does not reproduce. Stop data collection rather than
run the entire95-failure cascade. This is additional discriminating state evidence,
**not a C backtrace or general verification**, and needs separate explicit approval.

No implementation of that recipe in this review. Any actual native image acquisition
must first be source-qualified to avoid secret values, memory reads or code loading.
If cleanup/support/privacy bounds cannot be established, no-go: retain evidence and
seek an independent native specialist on a reviewer-capable session. No production
fix, integration/readiness/merge/customer authorization follows from this review.
