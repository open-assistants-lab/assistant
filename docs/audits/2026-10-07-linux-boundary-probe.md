# Linux boundary feasibility probe — blocked preflight

Plan: [MT execution boundaries, Task 1](../superpowers/plans/2026-10-07-mt-execution-boundaries.md).
Authorization: user approved an isolated Linux Docker probe and cached build dependency reuse.

## Observed preflight

- Docker client exists at `/usr/local/bin/docker`.
- Selected context: `desktop-linux`.
- Docker API connection failed: configured Unix socket is absent; daemon is not running.
- Docker Desktop is installed. No available Colima/Lima/OrbStack CLI was found.
- Container/image inventory, Linux kernel/backend discovery and real boundary tests
  could not run without a usable engine. No test was counted as passing/skipped proof.

## Status: BLOCKED, not qualified

Starting a shared Docker Desktop daemon may restart existing containers according
to their restart policies. Those containers could not be inventoried while the
daemon was unavailable. The isolated-probe authorization does not override the
requirement to leave unrelated services/customer operations untouched.

No daemon/application/container/image/network/volume was started, modified or
removed. No image/dependency download, OS provisioning or sandbox fallback occurred.
Bubblewrap/root refusal/egress requirements were not weakened. The Linux worker
layout is not qualified, and permanent hardened-MT implementation remains gated.

Next prerequisite: a user-started intended Docker engine/dedicated Linux target,
or explicit approval to start Docker Desktop with awareness of automatic container
restoration. Then inventory resources, create uniquely labelled fixtures and run
the planned negative controls and actual isolation/egress/termination probes.
