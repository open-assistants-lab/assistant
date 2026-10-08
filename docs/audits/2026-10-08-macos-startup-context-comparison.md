# Read-only startup-context comparison

User “proceed” after `7780cf7f` authorized comparison of recorded initialization/
fixture paths. **No new pytest, SDK/native probes, services, requests, dependencies,
product/test-source edits or security/config changes in this pass.** Original full
95 failures/1,544 SIGSEGV helpers remain unresolved. Author evidence analysis only.

## 1. Collection changes model configuration before the first API test

Module top-level statements in:
- `tests/sdk/test_subagent_scheduled_firing.py:20`
- `tests/sdk/test_subagent_v1.py:23`

call `os.environ.setdefault("AGENT_MODEL", "ollama:minimax-m2.5")` while importing
collected tests. `-k` deselection occurs after collection; skipping execution of
those tests does not undo their module-import side effect. The full4002-item
collection and4002-item first-node reduction import these modules; exact-node
collection does not require them. This is **import-time environment mutation**,
not evidence those SDK autouse test fixtures ran before the API node.

`tests/api/conftest.py:14–42` sets synthetic stores and reloads settings before its
session app/client fixtures. `src/config/settings.py:1054–1064` explicitly handles
flat AGENT_MODEL. `config.yaml:8` has empty agent.model by default. Provider factory
`_resolve_config_model_inputs` falls back to settings and refuses an absent model;
its ollama branch constructs an OpenAI-compatible provider without live credentials.
The first API test passes no model and only checks HTTP200 and a response field;
it does not assert inference success or the absence of an error.

This is a **source-supported explanation** for coverage divergence:
- Full collection: git/uname/file observed in first-node reduction.
- Exact node: only git observed, no recorded uname/file.

The earlier event capture did not record effective model/configuration or response
body, so this is not a new runtime measurement proving every resolution step.
Do not treat exact-node PASS as an equivalent successful SDK metadata control.
Import-time model mutation is a test-isolation issue worth separate review, **not
a demonstrated cause of the native SIGSEGV**. Removing it to avoid exercising the
SDK would mask this investigation, not fix the failing subprocess boundary.

## 2. Earliest crash snapshot includes native proxy discovery

Read-only extraction of first instrumented full fault block, raw log lines11–108:

|Recorded location|Frame|
|---|---|
|23|subprocess.py:1921 `_execute_child` (`_fork_exec` call site)|
|34|openai/_base_client.py:2102 `get_platform` in thread-pool worker|
|42|urllib/request.py:2676 `getproxies_macosx_sysconf`|
|43|urllib/request.py:2694 `getproxies`|
|44|httpx/_utils.py:37 `get_environment_proxies`|
|47|src/sdk/governance_dispatcher.py:91 `dispatch_once`|
|48|src/sdk/governance_dispatcher.py:57 `_run`|

These other-thread stacks are copied parent Python states emitted by a crashing
helper, not proof those Python threads were still executing inside that child.
They locate a native proxy-discovery call in the captured parent fork context;
there is no matching native C report and no established causal native instruction.
The first git helper succeeded before this point; subsequent helper failures span
many executable categories. The second earliest crash block does **not** contain
the same proxy-query frame; a one-time state transition or timing effect is possible,
not established. PostHog consumer is also present in the first snapshot; thread
presence is not evidence its telemetry caused the native fault. No telemetry fix.

### Source chain confirms a native path despite scrubbed proxy environment

- `src/http/main.py:98–105`: legacy lifespan starts the operation dispatcher.
- Dispatcher defaults to one-second cycles; `dispatch_once` builds
  `httpx.AsyncClient(timeout=5.0)` after candidate enumeration, even if empty.
- Installed httpx0.28.1 defaults trust_env=True; `_get_proxy_map` calls
  `get_environment_proxies` when no explicit transport/proxy is supplied.
- Installed CPython3.13.10 urllib on Darwin uses
  `getproxies_environment() or getproxies_macosx_sysconf()`; the latter calls
  `_scproxy._get_proxies()` / macOS SystemConfiguration.
- Verifier allowlists PATH/LANG/LC_ALL/SYSTEMROOT, then adds synthetic roots and
  offline flags. It does not inherit proxy environment variables. Thus clearing
  credential/proxy environment does not itself suppress native proxy discovery.
- Offline guard patches DNS/socket connects, not SystemConfiguration discovery.
  No proxy setting values were read or dumped during this source-only pass.

Earlier composition probes constructed a synchronous OpenAI client **on main
thread before** calling platform detection, serially or in one joined worker.
They did not intentionally align background AsyncClient/native proxy discovery
with the first platform-helper fork. They are valid negative controls for their
sequence, not a refutation of the newly identified concurrency/state hypothesis.

## 3. Comparison limitations retained

The full and full-collection reduced runs share interpreter/pytest/plugins/root
and first node, with instrumented delegates unchanged except module/log names.
They differ in selected items, runtime timing, process state and wall-clock host
conditions. Full launch used approved cached build reuse (UV_CACHE_DIR); reductions
did not. Event records do not provide effective-model, loaded-native-image,
registered-fork-callback or pre-fork SystemConfiguration-state inventories. No
measured statement that all startup state was identical is justified.

Native proxy-discovery overlap is **a trace-grounded hypothesis**, not the proven
root cause. It is more specific than blaming sandbox namespace policy, echo PATH,
OpenAI construction alone or globally disabling telemetry. No lasting fix selected.

## Experiment proposed at this checkpoint — subsequently approved

**Four fresh guarded processes,20-second outer deadlines, sequential, two repetitions
of each condition. No pytest/full-suite runs or API requests.**

Hypothesis: background native macOS proxy discovery around the initial platform
helper spawn contributes to the persistent helper-fault state absent from the
main-thread constructor controls.

Both conditions: one owned worker constructs/closes an httpx.AsyncClient; a second
owned worker calls installed SDK get_platform once. A disposable client subclass
records/aligns entry to `_get_proxy_map` before delegating unchanged. Retain exact
phase times and helper PID/exit records; native overlap is assessed, not assumed
from worker labels. No argv, environment, proxy values or credentials in capture.

- Baseline: native/default trust_env=True proxy-map path.
- Diagnostic control: only that disposable client has trust_env=False, so its
  proxy-map delegate does not call native environment/SystemConfiguration discovery.
  This changes proxy/TLS-environment behavior: **experimental control only, not a
  product fix or global override**. Guard and spawn/UID/session semantics unchanged.

Use scrubbed env, blank owned shadow, synthetic roots, offline guard before imports,
no requests/exporters/customer stores. Restore temporary capture and join/close
workers/client; stop on timeout or ownership uncertainty. Match only owned PID/time
native reports if a fault occurs. Do not extend/retry budget to seek a crash.
If native proxy phase fails to overlap or no crash occurs, retain that limitation;
no inference of cause/fix. Any lasting change still requires observed RED and review.

No experiments were authorized/run by the read-only comparison request itself.
User subsequently approved four processes; all completed without SIGSEGV, including
two measured proxy-query/uname-initialization interval overlaps. See the
[complete control outcomes and limitations](2026-10-08-macos-proxy-fork-results.md).
The four-run budget is exhausted; no lasting fix or additional launch authorized.
General verification, hardened MT, authority foundation and fresh-context branch
review remain blocked/pending.
