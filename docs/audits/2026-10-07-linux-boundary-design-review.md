# Linux MT launch failure — deployment/backend design review

Status: **proposal for review**, not an approved security policy or implementation.
Evidence: [actual launch gate](2026-10-07-linux-boundary-probe.md).
Existing contract: [execution authority](../superpowers/specs/2026-10-07-execution-authority-boundaries-design.md).

## Intent and unchanged requirements

Deliver the existing one-engine MT product for trusted companies sharing a Linux
Docker deployment, with private arbitrary-code execution separated from trusted
coordination. Preserve the loop/providers/CoreMem/kernel, persistent opaque
instance/user identity, private persistent installations, symmetric private-resource
protection, controlled egress, bounded termination and fail-closed startup.

No general sudo, user Docker socket, user/admin secrets in execution, implicit
fallback, new quotas, customer changes, engine rewrite or MU/fleet platform.
Infrastructure operators remain outside application confidentiality guarantees.

## What the failed probe establishes

Bubblewrap 0.12.0 cannot create its initial required namespace under the tested
Docker Desktop/Linuxkit recipe: non-root UID 20001, no capabilities,
no-new-privileges and Docker's installed seccomp filter. Kernel-wide user namespaces
are enabled. The child failed before executing its authorized workspace operation.

It does **not** establish that bubblewrap is impossible on Linux, identify the exact
denied syscall/LSM decision, qualify controlled egress, or prove that an arbitrary
custom seccomp profile is safe. Enabling namespaces does not itself enforce grants,
network policy, process termination or private-resource authorization.

## Approaches

### A. Reviewed namespace-capable Docker recipe — recommended first candidate

Retain bubblewrap and the existing one-container deployment direction. First obtain
syscall-level evidence without changing enforcement, then review the minimum
Docker policy differences needed for non-root namespace creation. A proposed
profile must be derived from a pinned Docker baseline, retain all unrelated denies,
identify each changed rule and explain which process can exercise it.

No `privileged`, `seccomp=unconfined`, blanket syscall allowlist, root execution,
new SYS_ADMIN grant or disabled no-new-privileges. User/PID/mount/network/IPC
namespaces, curated rootfs, private ownership and explicit mounts remain mandatory.
If an additional host privilege/helper is necessary, this option stops for an
explicit separate review rather than silently acquiring it.

Trade-off: least engine/interface disruption, but custom namespace policy expands
what processes in the outer container may attempt. It must be reviewed at the
container boundary, not marketed as a harmless worker-local setting. Its feasibility
and deployment portability are unproved. A passing launch is only an intermediate
result, never MT readiness.

### B. Explicit fixed-operation privileged namespace launcher

Use the existing spec's allowed narrow provisioning/launch-helper seam if A cannot
meet the requirements. The helper would create fixed approved identities/roots and
namespaces, then relinquish privilege before user execution. It must have no model
or integration credentials and expose only authenticated, bounded operations—not
arbitrary commands, environment, paths, mounts, UID selection or namespace joins.

Trade-off: can move namespace/egress setup into trusted infrastructure, but adds a
security-critical helper, privilege lifecycle and deployment requirement. The
existing bwrap non-root refusal still applies; this is not permission to invoke it
as a root service. Exact privileges, root selection, race-resistant filesystem
operations, descriptor closure, authentication and termination ownership require a
written reviewed design and new actual qualification. Not approved as fallback.

### C. Per-user OCI containers / separate host isolation

A possible future alternative if A/B fail, not the recommended next implementation.
It changes the agreed one-shared-container worker layout and adds trusted container
lifecycle responsibilities. User execution still receives no Docker socket.
It requires explicit product/deployment rescoping and the same full adversarial
qualification, not reuse of Docker's name as security proof. MicroVM/MU/fleet work
remains deferred.

## Recommended staged review for A

1. **Diagnosis under unchanged policy:** use existing tooling or separately approved
   tracing acquisition in owned synthetic fixtures. Identify clone/unshare/mount
   failures and any available filter/LSM evidence. Do not guess the allowed syscall
   set from the generic bwrap message or probe with enforcement disabled.
2. **Written policy/recipe proposal:** pin baseline/profile/backend revisions;
   enumerate syscall changes and namespace flags, process roles, UID/GID mappings,
   mount visibility and capability/descriptor handling. Explain outer-container
   exposure, limitations and refusal behavior. Review before any policy is applied.
3. **Explicit execution authorization:** applying a revised security policy is a
   different authorization from the earlier signed package build. Do not change
   daemon-wide policy, sysctls, existing containers or customer deployments.
4. **Full throwaway feasibility qualification:** prepare all eight required cases
   first. Observe intentional uncontained RED, then actual candidate GREEN with
   authorized private operations and independent sibling survival. No permanent
   worker code or MT readiness claim before this gate passes.
5. **Only then resume MT runtime tasks:** reconcile approved core interfaces and
   provision identities persistently; keep any unqualified startup fail-closed.

## Required full qualification (unchanged)

- Private sibling/control sentinels cannot be read; authorized own writes succeed.
- Equal user names in different instances map to distinct persistent identities.
- Other users' process environment and namespace handles are inaccessible.
- Symlink/rename swaps cannot expose protected or sibling resources.
- Private synthetic installation survives worker restart without global writes.
- Bare sockets ignoring proxies cannot reach forbidden infrastructure.
- Internal loopback, raw IP, redirects and private DNS aliases are denied while an
  explicitly allowed local fixture succeeds through controlled egress.
- Termination stops only the owned process tree; sibling remains alive.

An empty network namespace or Docker `network=none` is not sufficient egress
qualification: the allowed fixture must also work. Privileged network setup, if
required, belongs to the reviewed fixed-operation infrastructure interface, never
arbitrary code. No namespace descriptors or privileged alternate transport may be
left accessible to the worker. Persisted user identity/mount ownership must be
qualified alongside syscall policy; caller JSON and numeric hashes are not proof.

## Acceptance and stop conditions

Record exact kernel/engine/image/profile/backend identity and syscall failure
outputs without secrets. Any missing allowed operation or successful forbidden
operation fails qualification. Refuse MT startup after qualification mismatch,
missing backend or policy drift. No weakening to make a test pass.

This proposal selects **A as the first review direction**, not a concrete syscall
profile, helper, approved spec amendment or new implementation plan. User review
must select the direction before those artifacts are produced. Core/remote-native
work that does not assume this backend can remain separate; the full refactor is
not complete and no main integration/publication is authorized.
