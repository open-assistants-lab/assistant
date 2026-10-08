# Four approved proxy/fork controls — native fault not reproduced

User “proceed” after `8ea6eaa3` approved exactly four fresh guarded20-second parent
processes. No pytest/full-suite run, requests, customer stores/credentials, installed
or product source changes, Docker changes, telemetry disabling or spawn-mode changes.

[Sanitized full outcomes](2026-10-08-macos-proxy-fork-evidence.json) retain budget,
argv, phase timestamps, helper PIDs/exits, worker identities, owned inventory and
closure/restoration facts. All previous full/reduction/probe data remain intact.

Each process creates one disposable httpx.AsyncClient in an owned worker and calls
installed SDK get_platform once in a second owned worker. A temporary client
subclass aligns proxy-map entry with platform-worker readiness before delegating
unchanged. Default native proxy queries are observed with start/finish hooks; no
proxy or environment values are logged. Control changes only the disposable
client's trust_env=False, not product/global policy. No client request method is used.
The offline DNS guard is verified before SDK imports; proxy environment is empty.

|Order|Condition/repetition|Parent PID|uname/file helper PIDs|Outcome|
|---|---|---|---|---|
|1|Default1|56911|56917/56918|parent0,both helpers0,MacOS|
|2|Control1|56921|56925/56926|parent0,both helpers0,MacOS|
|3|Default2|56929|56933/56934|parent0,both helpers0,MacOS|
|4|Control2|56937|56941/56942|parent0,both helpers0,MacOS|

Durations:0.846/0.574/0.575/0.579seconds. No deadline, fatal stderr or tracked owned
leftovers. Clients closed, workers joined, proxy/Popen hooks restored, owned blank
.env removed with inode identity checks. No native failure occurred; no native-report
scan undertaken. Exclusive budget record prohibits restarting this four-run group.

## Actual phase overlap, not inferred worker labels

Both defaults have allow_env_proxies=True and a real observed getproxies call.
Observed proxy-query intervals last4.707ms and4.845ms. Each intersects token1's
`helper-start`→`helper-ready` interval (uname); neither intersects token2's file
initialization interval. Both controls have allow_env_proxies=False and **no proxy
query markers**, but do exercise both SDK platform helpers.

These intervals encompass Python wrapper/delegate activity and scheduling; they
are **not native-instruction-level proof of concurrent SystemConfiguration and
fork execution**. Markers/barriers/I/O alter timing. They are stronger evidence than
assuming overlap because two worker names exist, without establishing the exact
C interleaving that occurred in the original fault snapshot.

## Conclusion and stop

No SIGSEGV reproduced. The simple measured background proxy-query/platform-helper
composition was not sufficient to trigger the original fault in these four fresh
processes. Do not claim native proxy discovery is unrelated, or ship trust_env=False,
NO_PROXY, platform cache warming, telemetry suppression or idle-dispatch skipping as
a demonstrated fix. Full-suite95 failures and1,544 SIGSEGV helpers remain unresolved.
The prior collection-time AGENT_MODEL side effect and non-equivalent exact-node
coverage remain separate findings, not native-cause proof.

**Budget exhausted; no additional run.** Further constructor/slice combinations
without added native evidence are unlikely to discriminate the original C failure.
The next useful step is a reviewed safe method for obtaining an actual native stack
from an owned crashing helper, or an independent native-runtime evidence review.
It must preserve spawn/security semantics, avoid privileged bypass and respect
PID/time/secret boundaries; no debugger/security changes or wider execution scope
are authorized here. No production fix, full green verification, branch readiness,
merge/release/customer authorization or independent-review claim.
