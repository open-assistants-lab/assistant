# D3 Gate Decision — Local-First Desktop Track

**Date:** 2026-09-24  
**Branch:** `feat/desktop-d3`  
**Scope:** committed backend execution, permissions, connector, and desktop contract work

## Decision

**D3: PASS with non-blocking follow-ups.**

The local-first desktop track has one unified execution mode, item-level `allow` / `ask` / `deny` permissions, durable approval state, shared execution-kernel receipts, and a working permission-gated Gmail send vertical slice. Build/Use profiles are cancelled and are not part of the shipped contract.

## Evidence

- Full Python suite: **3196 passed, 26 skipped**.
- Focused permission/governance/connector suites: passing.
- Ruff: clean for modified source modules.
- Mypy: clean for modified source modules.
- Native D3 evidence recorded in `.superpowers/sdd/desktop-v0.1-plan/progress.md`: native tests and ReleaseFast build passed.
- Permission-gated Gmail flow covers:
  1. durable `ask` proposal;
  2. approval transition;
  3. Gmail API send through the shared execution path;
  4. provider message-id receipt;
  5. reconnect-required OAuth scope expansion.

## Non-blocking follow-ups

1. Integrate `PostgresReceiptStore` into Jen's application lifecycle and migration tooling; the adapter now exists behind the existing `ReceiptStore` protocol.
2. Add provider readback/reconciliation where a connector supports it; Gmail send currently records the provider acknowledgement/message id.
3. Re-run native/frontend gates immediately before packaging because unrelated native UI changes remain uncommitted in the worktree.
4. Preserve the existing one-process-per-user and trusted-network deployment constraints.

## Explicit exclusions

- No Build/Use profile selector.
- No legacy governance tiers.
- No claim that SQLite is the production persistence adapter for Jen.
- No claim of per-user authentication beyond the existing deployment trust model.

## Worktree note

The following pre-existing unrelated changes remain intentionally uncommitted and are not included in this gate decision: native SDK files and the conversation history replay fix.
