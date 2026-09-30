# Agent Execution Plane — Plan

**Date:** 2026-09-29
**Context:** [`docs/audits/2026-09-29-agent-posture-audit.md`](../audits/2026-09-29-agent-posture-audit.md)
**Status:** step 1 in progress; steps 3–4 not started.

## Premise

Five releases of work went into the governance plane. The execution plane is
where the agent actually fails: on an identical task and model, ours took
**228 s / 80 tool calls / 372k tokens and did not complete the job**, where a
harness with *no governance at all* took **10 s and got it right**.

Two of the three root causes are fixed (`8e928c6b`). Until the same benchmark
comes back clean, further work on the governance plane or the data layout is
premature.

## Step 1 — Prove the loop is fixed *(in progress)*

**Done when** `scripts/bench/agent_parity.sh` reports a passing suite and a
comparable wall clock for both harnesses.

```bash
scripts/bench/agent_parity.sh            # both harnesses, default task
scripts/bench/agent_parity.sh --task bugfix
```

The harness builds a throwaway fixture, runs each harness in the **same
directory with the same model**, and scores on:

- does the test suite pass (objective)
- wall clock, tool calls, input tokens

**Decision rule.** If we still fail or are >2× Pi's wall clock, **stop all
other work** and stay on the execution plane. If we are within 2×, the loop is
sound and the remaining plan proceeds.

The comparison is against `pi`, not an absolute number: the point is parity
with a reference, not a target we invented.

## Step 2 — Say what this does not protect you from *(not started)*

One page, from the audit's own findings:

- `files_*` is a **tool-level allowlist** (`filesystem.allowed_roots`)
- `shell_execute` is a **working directory, not a jail** — a command can read
  outside it; it is approval-gated instead
- the real containment boundary is the **OS / container / user account**
- what the sandbox does and does not bound (rlimits, write budget, no network
  isolation by default)

Every past external report was "your docs promise X". This page is the direct
fix, and step 3 is the only item that changes behaviour.

## Step 3 — Close `shell_execute` containment (#40) *(not started)*

The largest remaining gap between what we imply and what we enforce.

Options, in order of preference:

1. **Bind the command's reachable roots to the active profile's roots.** Reuses
   the new `allowed_roots` resolution; strongest.
2. **Fail closed when it cannot be enforced.** If `shell_execute` is `allow`
   while the backend is `soft`, refuse to start with a message naming the
   remedy. Cheapest, honest, and it turns a silent exposure into a loud one.
3. Document-only. **Not sufficient** — this is the one that already shipped a
   false claim.

Note `BwrapSandboxBackend` already passes `--unshare-net`: kernel network
isolation *exists* but is off by default and Linux-only. It is also incompatible
with the research agent's web access, so full isolation is not the answer
either.

## Step 4 — Self-verification *(not started)*

`verification.enabled: false` by default and the system prompt never asks the
model to check its own work. A fast unverified answer is the failure mode worth
fearing, and it is invisible in every metric collected so far.

Start by **measuring, not enabling**: add a re-read-after-answer signal to the
benchmark, so we know how often a completed answer survives scrutiny before
changing defaults.

## Not in this plan

- **Profiles / `Assistants/<id>/`** — the mode it serves does not exist yet
  (desktop binds loopback only; `api_base_url` is a sentinel). Blocked on that
  mode being specified. Durable finding recorded: the server already owns
  identity — *"the client never chooses `user_id`"*.
- **Renaming `workspace_*` accessors** — ~21 call sites, cosmetic.
- **`Users/<id>/` → `Assistants/<id>/` data move** — live data in `docker/data/`;
  needs the profile spec and a `desktop_migration.py`-shaped migration.
- **More governance surface** — deferred until step 1 is green.

## Open questions for the product owner

1. Is solo use a first-class case, or is every deployment multi-user? This
   decides whether the in-runtime governance plane is a feature or overhead we
   maintain for nobody.
2. Should the self-hoster's guarantee be *per-tenant* (runtime policy) or
   *per-deployment* (container/OS isolation)? They buy different things and we
   currently do the first while documenting like we do the second.
