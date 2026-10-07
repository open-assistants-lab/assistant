# Linux boundary feasibility — candidate launch gate failed

Plan: [MT Task 1](../superpowers/plans/2026-10-07-mt-execution-boundaries.md).
Authorizations: isolated Docker probes, Desktop startup with restoration risk
acknowledged, then disposable image build/signed-distribution bubblewrap acquisition.
No authority to relax security checks or choose a different runtime backend.

## Exact target and resource safety

Docker 29.8.0; actual Linux aarch64; Linuxkit kernel 7.0.12. Desktop startup reported
already running. All nine unrelated containers remained stopped. No customer
container/store/port/socket mount or application entrypoint was used.

Cached public assistant 0.6.37 base ID:
`sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6`.
BuildKit resolved this cached base without pulling another base. Build context
contained only `tests/refactor/linux/Dockerfile.probe`, not repository/config/.env.
Debian trixie's signed repositories supplied bubblewrap **0.12.0-1~deb13u1**;
no signature-check bypass, other package installation or upgrade was requested.
A minimal curated rootfs contained only the shell and required dynamic libraries,
not /app, host root, tenant/control stores, inherited credentials or configuration.
Build exit 0; generated throwaway manifest image ID:
`sha256:f64b9f9f01b9a99a967bf928148b9c1818ff6bb6337d68f1a21c15b858378e8c`.

Each probe used a random name/recorded ID and `assistant.authority.probe` label,
`--pull=never`, explicit local Docker Unix socket, no host mounts/ports,
network none, read-only image, UID/GID 20001, all capabilities dropped,
no-new-privileges, disposable writable /tmp, overridden entrypoint and `env -i`.
Cleanup targeted only recorded labelled containers and the owned image tag/ID.
Final owned-container inventory and running-container inventory were empty;
owned image removed. No global prune or unrelated image/cache removal occurred.
BuildKit may retain build cache; it was not globally pruned.

## Observed results

1. Cached image preflight: **2 passed, 1 failed**, exit 1; bwrap absent, exit 127.
2. Authorized prepared image: **3 passed, 1 failed**, exit 1.
   Non-root Linux/sanitized environment, synthetic uncontained sentinel positive
   control, and bwrap dependency version passed. Actual curated namespace launch
   failed, child exit **1**, before the authorized private-write assertion:

   > bwrap: No permissions to create a new namespace, likely because the kernel
   > does not allow non-privileged user namespaces.

The launch used current backend mount/user/PID/network/IPC namespace mechanics,
`--die-with-parent` and `--new-session`, without changing root refusal or Docker
security policy. This is a **failed mandatory launch gate**, not a mocked/skipped
boundary pass or proof of cross-tenant authorization.

Diagnostic kernel globals: `user/max_user_namespaces=31879`,
`kernel/unprivileged_userns_clone=1`. Same-recipe process status: UID/GID 20001,
CapEff=0, NoNewPrivs=1, Seccomp=2, one filter. Global user namespaces are enabled;
the generic bwrap error alone does not establish which syscall/LSM/filter denied
creation. One additional diagnostic read of `/proc/self/attr/current` returned
EINVAL; it is recorded, not claimed as an AppArmor finding. No strace download,
privileged comparison or seccomp-disabled comparison was authorized/performed.

## STOP GATE and unproved coverage

This Docker launch recipe **cannot qualify hardened MT**. Existing bubblewrap
candidate cannot launch under these observed restrictions. No soft/null fallback,
privileged mode, SYS_ADMIN grant, seccomp disabling, sysctl change, permanent
worker layout or MT startup enablement was introduced.

Eight-case negative/isolation/egress/restart/termination qualification remains
unrun: the uncontained sentinel positive control is not that full negative suite.
Default unit collection skips the opt-in Docker tests; skips are not evidence.
Retained real tests/fixture: `tests/refactor/linux/`. Execution logs in plan ledger:
`linux-probe-image-build.log`, `linux-bubblewrap-launch.log`,
`linux-bubblewrap-diagnostics.log`, `linux-docker-policy-status.log`.

Next step requires **backend/deployment design review**, not more security-toggle
experiments. Review a narrowly scoped namespace-launch/seccomp/helper recipe or
another approved boundary, with explicit non-root provisioning and enforced egress
requirements, before further implementation assumptions. Other Linux host/recipe
configurations are neither qualified nor disproved by this Docker Desktop result.
