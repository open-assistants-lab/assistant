# Linux boundary feasibility probe — blocked dependency prerequisite

Plan: [MT execution boundaries, Task 1](../superpowers/plans/2026-10-07-mt-execution-boundaries.md).
Authorization: user approved isolated Docker execution/cached build dependencies,
then approved Docker Desktop startup with automatic-container-restoration risk disclosed.
No package download/image build/pull authorization is inferred.

## Actual target and observations

Initial preflight found no daemon socket. After further authorization,
`docker desktop start --timeout 120` reported **already running**. Actual inventory:
Docker 29.8.0, Linux aarch64, Linuxkit kernel 7.0.12. All nine unrelated containers
were stopped before and after the probes; none was started/stopped/modified by us.

Cached public image: assistant 0.6.37, immutable local image ID
`sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6`.
Each probe used a random name/recorded ID and `assistant.authority.probe` label,
`--pull=never`, no host mounts/ports/socket, network none, read-only image,
UID/GID 20001, all capabilities dropped, no-new-privileges, a writable disposable
`/tmp`, overridden entrypoint and `env -i`. No image .env/application was loaded.
Cleanup was restricted to the recorded labelled ID. Final labelled-container
inventory was empty and no containers were running.

Actual opt-in preflight: **2 passed, 1 failed, exit 1**:

- Non-root Linux execution and scrubbed environment passed.
- Intentionally uncontained same-process read of a synthetic sentinel passed as
  a positive control. This is not the eight-case negative-control qualification.
- Candidate dependency test failed: `bwrap --version` exited **127** with
  `/bin/sh: 1: bwrap: not found`. No actual bubblewrap namespace launch occurred.

Retained tests: `tests/refactor/linux/`, invoked explicitly with `--linux-probe`
and an explicitly selected local Docker Unix socket. Default unit execution skips
these three tests and performs no Docker actions; those skips are not qualification.
Logs: execution ledger `linux-preflight-actual.log`.

## Status: BLOCKED, not qualified

The usable Linux target is available, but the cached image lacks bubblewrap.
This is a missing prerequisite, not proof that the candidate's security mechanisms
fail or succeed. No fallback, root-refusal bypass, privileged helper, seccomp
relaxation, dependency install, image build/pull or permanent worker layout was added.
The eight required isolation/egress/restart/termination cases remain unrun.

Next prerequisite: authorize a disposable image build and acquisition of bubblewrap
from the base distribution's signed package repositories, or supply a prepared
cached image with it installed. Then prepare the curated rootfs/synthetic fixtures,
observe intentional negative controls and run the real candidate boundary tests.
Failure of required mechanisms stops for reviewed backend/design selection.
