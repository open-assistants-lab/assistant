# Option A — Linux namespace-policy attribution design

Status: **user approved for planning**; concrete JSON/diff and changed-policy
execution remain separately gated. This spec is the first bounded
security-review stage of Option A, not a production seccomp profile or MT approval.

Context: [deployment review](../../audits/2026-10-07-linux-boundary-design-review.md),
[failed Linux gate](../../audits/2026-10-07-linux-boundary-probe.md),
[original MT plan](../plans/2026-10-07-mt-execution-boundaries.md).

## 1. Intent and scope

The user selected Option A: preserve bubblewrap and the existing one-engine,
one-shared-container MT direction, diagnosing and reviewing necessary Docker
policy changes rather than adding a privileged launcher or replacing the backend.
Private-resource isolation, non-root execution, enforced egress, persistent identity,
root refusal and fail-closed startup remain unchanged.

This stage answers a smaller question before considering real namespace permissions:
**does the installed Docker syscall policy explain denial of a no-op `unshare(0)`?**
It does not enable namespace creation, mounts, network access or worker execution.
The purpose is to avoid designing a larger allowlist from an ambiguous EPERM.

## 2. New evidence under unchanged enforcement

The retained `tests/refactor/linux/namespace_diagnostic.py` used the cached public
base, the same non-root Docker recipe and Python's existing libc interface—no new
package, capability, sysctl, helper, host mount or policy change.
Every syscall case ran in its own child, avoiding prior namespace state changes.
On the actual aarch64 target:

| Operation | Flags | Result |
| --- | --- | --- |
| native clone | SIGCHLD only | succeeded; owned child reaped cleanly |
| native clone | each NEWUSER/NEWNS/NEWPID/NEWNET/NEWIPC + SIGCHLD | EPERM |
| native clone | combined required namespaces + SIGCHLD | EPERM |
| unshare | zero (no namespace creation) | EPERM |
| unshare | each required namespace or combined flags | EPERM |

Diagnostic outer process exit: **0**. This does not mean namespaces succeeded.
Kernel-wide user namespaces are enabled; same-recipe seccomp mode is 2, capabilities
zero and no-new-privileges enabled. The no-op denial and flag-dependent clone denial
are consistent with syscall filtering, but do not independently prove which filter
or LSM caused every namespace denial. No kernel-policy change was made.

The diagnostic native clone ABI is intentionally aarch64-only (syscall 220).
It must refuse other architectures rather than reuse a guessed syscall number.

## 3. Comparison artifacts and provenance gate

Prepare three explicitly distinguishable fixtures:

- **D: installed daemon default**, unchanged from the observed recipe.
- **B: explicit reviewed baseline JSON**, byte-preserved and SHA-256 pinned.
- **P: baseline plus a single diagnostic rule**, generated from B.

B must be sourced from a pinned upstream Moby/Docker profile revision with recorded
engine/source mapping, architecture, profile checksum and source provenance. A
moving branch, scraped approximation, reconstructed allowlist or another engine's
profile is not acceptable as an undocumented replacement. No raw web text becomes
executable policy. If exact installed-default mapping cannot be established,
explicitly report that limitation and obtain user approval for the substitute
baseline before applying it. Profile synthesis waits for the provenance gate;
profile application additionally waits for concrete JSON/diff review and explicit
execution authorization.

The review artifacts include the complete B/P JSON, hashes, exact structural diff,
launch argv and expected assertions. Generating/checking these files is not authority
to apply them to Docker.

## 4. The only proposed policy difference in this stage

P adds an OCI seccomp **SCMP_ACT_ALLOW** rule for syscall **unshare**, argument index
**0**, comparison **SCMP_CMP_EQ**, value **0**. This is the no-op call only.
Its mask/unknown flags are not broadened. Existing baseline default action, errno,
architecture map, conditional rules, all other syscalls and argument conditions
remain byte-equivalent/structurally identical after canonicalization.

A conflicting existing explicit unshare deny rule makes the automatic edit invalid:
report it and review the rule precedence/design instead of dropping denies.
No clone, clone3, setns, mount, umount, pivot_root or namespace-creating unshare
permission is added. No default-action change, privileged mode, SYS_ADMIN,
seccomp-unconfined, no-new-privileges removal or root execution.

The profile applies to the entire throwaway outer container—not merely a selected
worker. Even this small change requires explicit review. It is not the final MT
profile, and must not be installed daemon-wide or in existing/customer containers.

## 5. Actual comparison and assertions

Run the exact diagnostic under D, B and P in separate uniquely labelled disposable
containers with the same immutable cached image, UID/GID 20001, no-new-privileges,
capabilities dropped, read-only root, network none and owned disposable /tmp.
All image pulls remain disabled. No source/.env/customer store/socket mounts.

Require:

1. D and B show ordinary clone success and no-op unshare denial. If B differs,
   stop attribution; it is not a demonstrated equivalent baseline for this question.
2. P allows `unshare(0)` while retaining ordinary clone success.
3. P still denies namespace-bearing clone and unshare attempts, including each
   flag and combined flags. No namespace creation is authorized by this stage.
4. UID/capability/no-new-privileges/seccomp state remains as declared.
5. No unrelated syscall rule changed in the JSON/diff; cleanup leaves no owned
   containers or running siblings affected. Only owned resources may be removed.

If no-op unshare remains denied under P, the filtering explanation is incomplete;
stop and investigate evidence instead of widening permissions. If namespace-bearing
calls unexpectedly succeed, fail the stage and remove the owned fixtures.

D/B/P results support only this particular syscall/filter comparison. They do not
establish mount/egress safety or prove attribution of every bwrap failure.

## 6. Evidence, execution authorization and subsequent stages

Retain hashes, image/engine/kernel/architecture identity, exact argv, safe syscall
results/errno, process-policy metadata, comparison exit codes and owned cleanup IDs.
Do not retain private environment/content or claim a diagnostic exit 0 as containment.
There is no availability/noisy-neighbor guarantee or new quota scheme.

Written-spec approval permits an implementation plan and concrete artifact
preparation/review. Applying even P requires separately explicit permission after
its JSON/diff is reviewed. Existing approvals for signed-package acquisition and
Docker startup are not generic permission to relax policy.

If this stage supports filter attribution, the next written proposal must enumerate
actual bwrap syscall needs with evidence, review the expanded outer-container attack
surface and define exact minimal namespace permissions. No guess-based final
allowlist, permanent worker code or MT enablement follows automatically.
Full eight-case negative/isolation/egress/restart/termination qualification from
the original plan is still mandatory, including an allowed network fixture alongside
forbidden direct/raw-IP/loopback/DNS/redirect attempts. Any failure keeps MT disabled.

This stage deliberately excludes privileged helpers, alternate backends, deployment
changes outside owned fixtures, customer operations, main integration and publication.
Core/native work independent of the Linux backend retains its separate approved plan.
