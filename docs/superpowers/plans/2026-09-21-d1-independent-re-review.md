# D1 Independent Re-Review — PASS for the delta (2026-09-21)

**Scope:** read-only, independent re-review of the merged D1 remediation
(`61777b6b..830ee0d4`; commits `e4216a9e`, `4b16ac4a`, `1a594d47`, `830ee0d4`)
against the prior D0/D1 gate review's five findings and the D1 exit gate.
Reviewed at main HEAD `721280c0`.

**Source currency (verified):** none of the eight files changed by the four
commits were modified after `830ee0d4` — the intersection of
`git diff --name-only 61777b6b..830ee0d4` with `git diff --name-only 830ee0d4..HEAD`
is empty. (The reviewer's parenthetical "only `tests/api/test_governance_*`" was
inexact — 74 files changed after the tip — but the claim that matters holds.)

**Verdict: PASS for this delta against the D1 exit gate.** All five prior
findings are fixed with source + regression-test evidence; no new defect or
regression was found in the four commits.

## Prior findings

| Severity | Finding | Verdict | Key evidence |
|---|---|---|---|
| P0 | Lock/ownership ordering | FIXED | One lifetime lock acquired before migration and rendezvous cleanup (`src/http/desktop.py:289-316`); serving borrows it and releases only self-acquired locks (`:154-165,241-242`); `tests/api/test_desktop_startup_ownership.py:33-52` drives the full `desktop_main()` under a competing lock and proves migration/rendezvous untouched |
| P1 | Desktop WS identity/workspace | FIXED | Upgrade authenticates desktop before `accept()` (`src/http/routers/ws.py:424-441`); `resolved_user_id` set in desktop mode (`:528-529,554`); per-message payload cannot redirect identity/workspace (`:773-788`); `tests/api/test_desktop_ws_identity.py:40-83` asserts both boundary calls are `("default_user", "personal")` |
| P1 | Duplicate promotion destinations | FIXED | Normalized destination map rejects legacy `Conversation/` + `Messages/` in preflight, before any `shutil.move` (`src/storage/desktop_migration.py:83-97,136-176`); `tests/storage/test_desktop_migration_conflicts.py:8-21` proves both sources survive |
| P2 | Desktop-mounted app leak | FIXED | Effective-settings test no longer reloads `src.http.main` (`tests/api/test_desktop_server.py:696-712`); remaining desktop-mode reloads restore solo mode in `finally` |
| P2 | Desktop contract 401s | FIXED | Fixture publishes `DESKTOP_LAUNCH_TOKEN`; requests carry `Authorization: Bearer …` (`tests/api/test_desktop_contracts.py:171,189,219-221`) — refusal now 409, listings 200 |

## Exit-gate rows

- **MET:** dynamic loopback binding; required bearer token; nonce ownership
  (D1 scope; launcher-side nonce binding is D0); second-launch safety;
  server-owned identity (HTTP + WS).
- **PARTIAL (evidence quality only, no product defect):**
  1. `tests/api/test_desktop_contracts.py:225-227` asserts tool names not
     registered in any mode, so it cannot catch a desktop registration-filter
     regression. The one registered excluded-family tool is `email_draft`
     (`src/sdk/native_tools.py:194`) and it is not asserted.
  2. `src/storage/desktop_migration.py:154-173` (`unexpected_legacy_siblings`)
     has no test, so "every migration branch" is not literally demonstrated.
- The registry filter is order-sensitive (built at import); in a process that
  first built the registry non-desktop, desktop `/v1/tools` can still list
  `email_draft`. Production starts in desktop mode, so this is a test-evidence
  gap, not a runtime exposure.

## Validation (parent-run, on the same surface)

- `test_desktop_contracts` + `test_desktop_ws_identity` +
  `test_desktop_startup_ownership`: 22 passed
- `test_desktop_server`: 27 passed
- `test_identity_resolver` + `tests/storage/`: 194 passed
- **243 passed / 0 failed** (prior review: 224 passed / 12 failed)
- Scoped mypy clean; one pre-existing scoped ruff `F401`
  (`tests/api/test_identity_resolver.py:327`, byte-identical at `61777b6b`)

## Remaining decisions (D1 not yet recorded as closed)

1. **Quality-gate policy** — the plan's explicit precondition. Measured
   baseline at `721280c0`: `ruff check src/` 19 `E402` in
   `src/http/routers/conversation.py`; `ruff check tests/` 56 (33
   auto-fixable); `mypy src/` 62 errors / 17 files.
2. **D0 sign-offs** — bootstrap nonce-binding payload shape; six-component
   release tuple (`src/http/desktop.py:94-115` still reports `unpinned`);
   backend/security review memo completion.
3. **Gate owner's call** — whether the two PARTIAL evidence rows must be closed
   before D1 is recorded as passing (both are small test additions).

## Evidence

- `/tmp/desktop-d1-rereview-evidence/` — delta diff, per-suite logs,
  `validation-summary.txt`, archived reviewer transcript, `mypy-full.txt`,
  quality-gate baseline.
