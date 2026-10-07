# Execution authority foundation — partial implementation evidence

Branch: `refactor/execution-authority`; baseline `00b4f12c`.
Execution method: user-selected inline/native.
Plan: [core contracts](../superpowers/plans/2026-10-07-engine-contracts-and-receipts.md), Task 1.

## Implemented and tested foundation

- Frozen opaque-instance/actor/policy models; explicit instance configuration and
  authenticated issuer/subject membership mapping in fresh per-instance SQLite.
- Many-team membership and revision invalidation; caller role assertions ignored.
- Distinct equal-name users' instance paths; malformed IDs and symlinked private
  roots refused; retained/incompatible databases rejected without mutation.
- Actual generated keys carry stored instance/subject/credential bindings. Unbound
  deployment/legacy keys do not resolve to individual new-contract authority.
- OIDC session records can retain verified issuer/subject/instance provenance;
  remote sign-in/qualified callback wiring is not yet delivered.
- Outer HTTP/WebSocket ingress gate rejects unqualified legacy routes, aliases,
  exemptions/callbacks and loopback bypass. Fresh-contract startup is intentionally
  refused before legacy migrations/dispatchers. No hardened MT claim.
- Owned isolated verifier scrubs credentials, blocks external/default-live network
  destinations, forces offline dependency tools and bounds the pytest process group.

## Verification

Tests were written and observed failing before corresponding implementation.
Targeted authority/auth/OIDC slice: **76 passed**, 5 inherited warnings, exit 0.
Corrected authority/telemetry/lifecycle/skills slice: **88 passed**, 1 inherited
warning, exit 0.

Whole Python suite **excluding exactly `tests/config/test_packaging.py`**:
**3,860 passed, 27 skipped, 69 warnings, 272.86 seconds, exit 0**.
This is not a full-suite pass. The offline wheel check has **1 failed, 1 passed**:
Hatchling is absent from the fresh build cache/project venv. It must not silently
fetch dependencies. Cache reuse or separately authorized dependency acquisition
is still required to close that gate.

The first full run produced many intermittent macOS child-process SIGSEGVs; no
product workaround was introduced. A later run exposed verifier/test-fixture
issues: a global OTel-disable flag suppressed recording exporters, incomplete
lifespan test doubles omitted app state, and long temporary paths changed catalog
budget input. Those were corrected without weakening production safeguards.
The final offline run above exited cleanly. Full failure names/logs and rulings
remain in this plan's ignored execution ledger/workspace.

Earlier inherited wheel-test commands ran before explicit UV/PIP offline flags;
zero dependency-network activity cannot be attested for those commands. No
explicit dependency install/sync, version bump or lockfile change was performed.

## Remaining gates

Task 1 and the wider refactor are **not complete**. Qualified runtime/context
provisioning integration, policies/resources, conversation model semantics,
admission/fencing, receipt fidelity/recovery, native flows and actual boundaries
remain planned. Linux Docker, dependency acquisition, signing/notarization and
publication each require their stated authorization. No customer access, Docker
startup, merge, push or release. Fresh whole-branch review remains pending.
