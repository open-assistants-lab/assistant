# Single benign LLDB fork preflight — capture method not qualified

User “proceed” after `ab236693` approved one benign30-second method preflight.
Exactly one debugger target launch occurred. No SDK/app/pytest/inference, requests,
customer stores, core dumps, memory/locals/environment dumps, downloads, root,
entitlement/SIP/sysctl changes, permission bypass or retry.

[Complete outcome and cleanup observations](2026-10-09-macos-debugger-preflight-evidence.json)
retain approval/budget, argv, PID/start-time ownership and raw negative result.
Core soft limit0 was set in the owned driver and target script. The target imports
stdlib and exact-file offline socket guard only. Scrubbed environment/synthetic
roots used; LLDB init scripts/external symbol lookup disabled. Child-follow was
changed only in this disposable debugger session, not global settings.

## Observed execution

- LLDB PID58389 accepted child-follow setting and launched target Python.
- Benign target parent marker: PID58406, ppid58407 (debugserver).
- Fork child marker: PID58452, ppid58406; reached deliberate SIGSTOP checkpoint.
- Batch `run` did **not** return with an observable selected-child stop. Subsequent
  `process status`, symbolic `thread backtrace` and `continue` commands never ran.
- At30-second deadline, exact recorded PID/start identities were killed; LLDB exit-9.
  Driver wall duration30.386s includes cleanup. No debugger/native stack captured.

The method therefore **does not qualify Darwin child tracking**. It is not proof
all LLDB/Darwin child debugging is impossible, nor a reproduced SDK SIGSEGV. Setting
acceptance and successful process launch are not proof of child-follow support.
No follow-up attach, fallback spawn/preexec mode, privilege escalation or retry.

## Cleanup limitation — retained, not hidden

Initial inventory after exact owned kills still showed target parent58406. A
read-only exact-PID follow-up confirmed it as **Z (zombie), reparented to PID1**,
with the original recorded start time. Three status observations retained; no
additional signal or broad cleanup. The other tracked PIDs (58389,58391,58407,
58452) were absent in the final observation.

Driver raised `Owned cleanup uncertain: STOP` rather than declare success. A zombie
cannot be removed by repeated SIGKILL; reaping belongs to its parent/system. Do not
kill/restart PID1 or unrelated services. **Complete PID absence is not confirmed**;
no further target/probe launch is authorized while this unresolved state is retained.
No core file at any recorded preflight PID path was found during stat-only checks.

## Next gate

Safe owned-helper native-stack capture remains unqualified. Do not enable dumping
through the entire95-failure/1,544-helper full run; aggregate disk/privacy risks
remain. Further debugger/capture execution requires a reviewed supported method
and explicit budget, not another guess with child-follow enum settings.

An independent native-runtime review of the retained C-call-site/PID/parent-snapshot
and method-failure evidence is now preferable to more constructor/pytest slices.
No such independent review occurred in this pass. Original95 full failures remain
unresolved; authority/MT/native readiness and whole-branch review remain pending.
