# Native-capture feasibility — no debugger target or dump enabled

User “plz proceed” after `be0200fb` authorized progressing safe native evidence
capture/review. This pass checks installed tool metadata and exact known helper
core-path metadata only. No test/probe/application launched, no debugger attached,
no target created, no credentials/stores accessed or native/core body read.
No OS/security/diagnostic limit changes. Worktree was clean at entry.

## Confirmed local facts

- Installed `/usr/bin/lldb`:2103.0.34.103, Apple Swift6.4.
- Targetless LLDB used `--no-lldbinit --batch`: no operator init scripts.
- `target create --core <filename>` is an available offline inspection command.
- `target.process.follow-fork-mode` exists and defaults to **parent**. Presence of
  this setting does **not** qualify child-follow behavior on this Darwin backend.
- Read-only sysctl: `kern.corefile=/cores/core.%P`, `kern.coredump=1`.
- Current shell core limits: soft0, hardunlimited. Historical verifier limits were
  not recorded; do not pretend this proves those past processes' resource limits.
- Stat-only checks of `/cores/core.<PID>` for all1,544 exactly recorded SIGSEGV
  helper PIDs found **zero existing files**. No unrelated core filenames or memory
  were inspected; no dump deleted. Previously matched native reports remain zero.

[Metadata evidence](2026-10-08-macos-native-capture-feasibility.json) preserves these
checks and unresolved child-follow support.

## Unsafe shortcuts rejected

Do **not** enable core dumping for an entire ordinary full-suite pass and let it
continue through1,544 helper faults. That could create many large sensitive memory
files under /cores, exhausting disk and exposing synthetic/process memory beyond
ordinary text diagnostics. An inherited process-local core limit would propagate
to helpers; a per-file cap does not establish a safe aggregate bound or a usable
untruncated Mach-O core. Current readiness is not permission to change limits.

Do not attach by process name or select an unrelated report. A live child may die
before PID capture/attach; parent-only debugger tracking would not necessarily
capture the child's native fault. Do not add child preexec hooks, force alternate
spawn/vfork modes, acquire entitlements, disable SIP/NNP, change system sysctls or
use root to obtain a superficially successful capture. Those would alter semantics
or exceed scope; no such operation was performed.

## Preflight proposed at this checkpoint — subsequently approved

Before another SDK/full-suite launch, verify whether this installed debugger can
actually follow an **owned benign fork child** on Darwin:

- **One**30-second maximum debugger launch; no pytest, app imports, SDK, requests,
  stores or inference. Guarded/scrubbed environment with synthetic HOME/TMP and
  `--no-lldbinit`; retain current core limit0.
- Tiny owned Python script creates one child with os.fork, emits parent/child PID
  markers, waits/reaps, exits. No arbitrary command execution inside child.
- Set child-follow only in that disposable LLDB session. Record process/thread
  identity and a symbolic native stack at an owned child checkpoint; do not assume
  support because the setting accepts a value. No memory/locals/environment dump.
- Exact PID/start-time ownership and cleanup checks for debugger/interpreter/helper;
  any denial, unsupported backend, unexpected prompt, deadline or uncertainty stops
  the preflight. No privileges, GUI permission changes, retries or fallback modes.

This is debugger-method qualification, not reproduction or security qualification.
A debugger can change scheduling and fork behavior; even a positive preflight will
not establish that original `_fork_exec` helper faults are catchable unchanged.
A subsequent real capture requires a separately reviewed single-fault-stop recipe,
strict PID association, privacy/retention and aggregate disk/process bounds.
If unsupported, stop and seek independent native-runtime review rather than enable
unbounded memory dumps or keep guessing passing component combinations.

No new debugger/test execution budget was implied by the metadata checks themselves.
User subsequently approved one30-second preflight. It reached the benign child
checkpoint but did not yield a child stop/backtrace before timeout; one recorded
parent remained visible as a zombie after exact-owned cleanup. See the
[negative method result and cleanup limitation](2026-10-09-macos-debugger-preflight-result.md).
No retry or supported native capture method is established. No production fix,
full verification pass, integration/release/customer readiness or independent-
review claim. Original95 failures remain unresolved.
