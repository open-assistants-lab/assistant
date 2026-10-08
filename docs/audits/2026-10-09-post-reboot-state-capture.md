# Post-reboot single state capture — no SIGSEGV within60seconds

Operator restarted Mac and requested another attempt, resuming the previously
approved one60-second state-capture recipe. Starting ref `155e9c9b`, clean worktree.
Old debugger identity58406 absent; kernel boot metadata confirms restart
(epoch1791474027, local FriOct9 02:40:27 2026). No PID1/system cleanup by assistant.

Exactly **one** full-collection/full-selected-context diagnostic launch; no keyword
filter, retry or debugger. Core soft0 in driver/pytest. Offline guard, scrubbed env,
owned blank .env/synthetic stores and approved cached public build reuse retained.
No credential/customer-store access, dependency acquisition, proxy/spawn/preexec/
UID/telemetry policy change or product/installed/test-source fix.

[Full sanitized evidence](2026-10-09-post-reboot-state-capture-evidence.json) retains
budget/boot, argv, ownership/cleanup, event counts, snapshots and helper outcomes.
Raw capture remains owned/ignored; all previous failed/negative evidence unchanged.

## Outcome — bounded incomplete run, not verification success

- Verifier **exit124** at configured60-second deadline; wrapper60.671s, no outer
  overrun. No first-helper-SIGSEGV marker, no Fatal Python stderr in retained log.
-16 observed helper exits: **15 exit0,1 exit3**, none-11. Exit3 belongs to the desktop
  lock-held test's expected sentinel, not a native failure.
-283 capture events,234 test-start events;219 complete PASSED progress lines matched
  the anchored parser and zero complete FAILED lines. No final pytest summary;
  these visible-line counts are **not a complete-suite or authoritative partial
  test tally**. No session-finish/capture-restoration event after external timeout.
- All recorded owned PID/start identities absent after teardown; no outer-driver
  extra signals needed. Verifier owns timeout group termination and shadow cleanup;
  .env absent. No retained debugger/zombie identity or core dump created by capture.

The old failing desktop lock sentinel now reached expected exit3; early git/uname/
file helpers also exited0. That is useful negative evidence within this context,
not proof every historical95 failure is gone or that restarting fixes the cause.
Host reboot, elapsed host state, timing and instrumentation are confounded.

## New state evidence actually collected

Plugin loads before collection: model environment absent, settings cache absent,
thread_count1. Before the first git helper at the first API node:
model-environment-present=True, cached-settings-model-present=True, threads2,
**34 Python-visible extension module names**. Before uname: same model-presence
booleans, threads4, **41 names**. Newly observed names in that interval:

- `_duckdb`, `_duckdb._func`, `_duckdb._sqltypes`
- `chromadb_rust_bindings.chromadb_rust_bindings`
- `jiter.jiter`
- `onnxruntime.capi.onnxruntime_pybind11_state`
- `tokenizers.tokenizers`

This demonstrates initialization/loading state changes between git and metadata
helpers in the successful post-reboot run. It does **not implicate these libraries**
in the previous fault. Model presence is measured as a boolean, not the effective
provider/model value or request result. Thread counts are not C interleaving proof.

Inventory is from sys.modules extension filenames (names only), not a complete
dyld/native image or registered-fork-callback inventory. Several names may alias one
binary. It is **not directly count-comparable to faulthandler's earlier26-module
enumeration**; do not infer missing/added native binaries from that cross-method
comparison. No native paths/addresses, argv/environment/model/proxy values, memory,
locals or test-parameter values were recorded in state events.

## Capture/teardown semantics

Temporary Popen wrappers delegate unchanged arguments/results/exceptions and record
PID/status/category/flags; first12 pre-spawn snapshots add import-state/counts.
Instrumentation allocates/copies sys.modules and adds I/O/timing; it can affect races.
Python `_USE_VFORK` flag is recorded, not proof of selected internal C path.

First SIGSEGV would have been recorded after original child wait/status, then owned
pytest exited86 to stop the cascade. That path was **not exercised** here. At60s,
verifier terminated its owned pytest group. Outer driver tracks exact descendant
PID/start identities including separated sessions, with graceful escalation only
for still-live matches, never PID1 or zombies. This run required no extra escalation
and confirmed tracked absence. Hook-restoration-on-normal-unconfigure was not seen
because the target was terminated; the target process itself is absent.

## Stop and next gate

The one-run budget is exhausted. No second capture or full verification was run.
The original native cause remains unresolved; no production fix selected. General
verification, qualified hardened MT, authority foundation/native readiness and
fresh-context whole-branch review remain pending.

If operator wants the next gate, obtain separate permission for **one normal offline
full verification pass** with installed dependencies/approved cached builds and a
fixed deadline, retaining any failures. A green result would show the branch passes
in the post-reboot context, **not a demonstrated native fix or qualification of
bubblewrap/native packaging**. No run-until-green, test skipping or security weakening.
