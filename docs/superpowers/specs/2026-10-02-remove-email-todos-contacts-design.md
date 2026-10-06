# Remove Email, Contacts, and Todos

**Date:** 2026-10-02
**Status:** Design record
**Type:** deletion with a replacement path
**Supersedes:** the runtime-exclusion approach in the earlier capability-scope draft

---

## 1. Decision

Remove the `email_*`, `contacts_*`, and `todos_*` tool families from the
codebase, along with their storage, HTTP routers, configuration, and
dependencies. This is a **deletion**, not a capability exclusion.

### Rationale

A dedicated `todos_add` / `todos_update` / `todos_delete` set pins the agent to
*our* column names and *our* semantics. The `apps_*` family already provides
structured data with FTS (`app_create`, `app_schema`, `app_insert`, `app_query`,
`app_search_fts`, `app_import_csv`), and a user who wants a different shape
should be able to build it. A tool that cannot be reshaped is a constraint, not
a feature.

The same reasoning does **not** extend to email (see §4), but email is being
removed on the same decision for the same class of reason: a fixed
read/send/reply API is one opinion about what mail is, and ConnectKit already
provides the general integration seam.

---

## 2. Correction to a prior claim

An earlier draft of this work asserted that the desktop exclusion of these
families was **unimplemented**. That was wrong. It is implemented:

- `src/sdk/native_tools.py:108` — `DESKTOP_EXCLUDED_FAMILIES = ("email_", "contacts_", "todos_")`
- `src/sdk/native_tools.py:117` — `_desktop_excluded()`, applied at every one of
  ~45 registration sites
- `src/http/main.py:411-414` — the three routers are mounted only when not in
  desktop mode

The earlier draft reached that conclusion by inspecting `deployment_tools.py`
and `settings.py` and not `native_tools.py`. Recorded so the error is not
repeated, and so the removal spec is not read as "finish an unfinished job."

**Consequence:** §6 also deletes `_desktop_excluded` and
`DESKTOP_EXCLUDED_FAMILIES`, because after this removal the mechanism has no
remaining consumer. A prefix-based family filter that excludes nothing is
worse than no filter.

---

## 3. What is removed

### 3.1 Source files

| File | Lines | Contents |
|---|---:|---|
| `src/sdk/tools_core/email_draft.py` | 161 | `email_draft` tool |
| `src/sdk/tools_core/email_db.py` | 277 | accounts, IMAP, message parsing |
| `src/sdk/tools_core/email_sync.py` | 620 | sync, interval sync, background job |
| `src/sdk/tools_core/connector_gmail.py` | 85 | `connector_gmail_send` |
| `src/storage/email_db.py` | 197 | email store |
| `src/storage/gmail_client.py` | — | Gmail REST client over ConnectKit |
| `src/storage/gmail_cache.py` | — | GmailCache, batched upsert |
| `src/http/routers/email.py` | 116 | email API |
| `src/sdk/tools_core/todos.py` | 292 | 4 todo tools |
| `src/sdk/tools_core/todos_storage.py` | 216 | todo store |
| `src/http/routers/todos.py` | 71 | todo API |
| `src/sdk/tools_core/contacts.py` | 202 | 5 contact tools |
| `src/sdk/tools_core/contacts_storage.py` | 461 | contact store, email parsing |
| `src/http/routers/contacts.py` | 84 | contact API |

**~2,780 lines** of the ~64,000-line Python source, across 14 files.

### 3.2 Configuration and paths

| Target | Action |
|---|---|
| `EmailConfig`, `EmailSyncConfig` in `src/config/settings.py` | Delete, plus the `email_sync` / `email` fields on the app config |
| `DataPaths.email_dir/contacts_dir/todos_dir` and the three `*_db()` accessors | Delete |
| `DataPaths.team_contacts_dir` / `team_todos_dir` | Delete (team layer is already deferred and unused) |
| `capabilities.py` desktop list — `email_draft` | Delete the entry |
| `native_tools.py` — `DESKTOP_EXCLUDED_FAMILIES`, `_desktop_excluded`, `_desktop_filtering_active` | Delete, and unwrap all ~45 `if not _desktop_excluded(...)` guards to direct `registry.register(...)` |
| `pyproject.toml` — `imap-tools` | Drop the dependency |
| `.env.example`, `docker/.env.example`, `DEPLOYMENT.md` — `EMAIL_GWS_CLIENT_ID`, `EMAIL_GWS_CLIENT_SECRET`, `EMAIL_M365_CLIENT_ID` | Delete |

### 3.3 Tests

Removable outright:

```
tests/api/test_email.py            tests/sdk/test_email_draft.py
tests/api/test_contacts.py         tests/unit/test_contacts_tools.py
tests/evaluation/test_email_tools.py   tests/storage/test_gmail_client.py
tests/unit/test_complex_apps.py    (email/todo/contact cases only)
```

Roughly 15 test files are removable. A further ~25 reference the words in
fixtures, personas, or unrelated assertions and need editing rather than
deletion.

**Do not delete by grep.** `test_permission_policy.py`, `test_tool_contracts.py`,
`test_governance.py`, and `test_audit.py` all mention these tools as *subjects
of governance assertions*. Deleting the subjects does not delete the assertions
that governance behaves correctly — those tests must be re-pointed at a
surviving governed tool, not removed.

---

## 4. What stays, and why

| Stays | Reason |
|---|---|
| `connectkit` dependency | Still has real consumers (below) |
| `src/http/routers/connectors.py` | Generic OAuth over 400+ ConnectKit specs |
| `src/sdk/tools_core/file_sync.py` | Dropbox / Drive / OneDrive adapters, all ConnectKit-backed |
| `CONNECTKIT_VAULT_KEY` | Used by the ConnectKit vault, which file-sync still needs |

**The honest end state:** ConnectKit survives as an extension point with **no
first-party connector**. `src/storage/gmail_client.py` was the only built-in
one, and it goes with email.

Two consequences to accept explicitly:

1. The native app's Connections panel has no first-party service to
   demonstrate against. The D1 decision already excludes the panel from desktop
   mode, so this does not regress the shipped surface — but it does mean the
   panel is unproven until a third-party ConnectKit spec is exercised.
2. `file_sync.py` remains the only live consumer of the ConnectKit OAuth path,
   so it becomes load-bearing for that integration rather than one of several.

---

## 5. Replacement path

### 5.1 Structured data: `apps_*` (already exists)

No new code. `app_create` → `app_schema` → `app_insert` → `app_query` /
`app_search_fts` covers todos and contacts with a user-definable shape.

### 5.2 Seeded template apps (new, small)

The one genuine regression: "extract todos from this conversation" is currently
one call and would become create-schema-then-insert.

Mitigation: ship template apps under `seeds/apps/` so the schema already exists
and the agent inserts rather than designs. `seeds/` currently holds `prompts/`
and `skills/` only, so this is new but small — a handful of JSON/SQLite
definitions plus a loader, following the existing seed-refresh pattern with
`.seed-hash` sidecars.

Templates to ship: `tasks`, `contacts`, and one `reading-list` to demonstrate
that the shape is the user's to choose.

### 5.3 Email: no first-party replacement

Deliberate. ConnectKit specs provide OAuth for third-party services; the
general read/send/reply tool surface is not being rebuilt. If it is wanted
later, it should arrive as a **seeded skill or custom `TOOL.md` set** built on
ConnectKit, not as re-added native tools — which is the same flexibility
argument that motivated this deletion.

---

## 6. The governance proof-point must be replaced

`docs/superpowers/plans/2026-09-24-d3-gate-decision.md` cites the
permission-gated Gmail send as the evidence that durable governed operations
work end to end. Removing email removes that evidence.

**Replacement:** the external-executor path already in the codebase.

```text
ToolAnnotations.executor: ExternalHTTPExecutor
  → src/sdk/governance_operations.py    durable ledger, transactional outbox
  → src/sdk/governance_dispatcher.py    lifecycle dispatch
  → HMAC callback capabilities, ordered idempotent events
  → uncertain states, never automatic replay
```

A test-only fixture tool with an external executor exercises the identical
machinery — durable async operation, approval, dispatch, callback, receipt — with
no vendor integration and no network. It is a *better* test than the Gmail
slice, because every part of it is under our control.

**Requirement:** the replacement fixture ships in the same change as the
deletion. The repository must not be left with a thinner governance proof than
it had.

---

## 7. User data

Existing installs will have:

```text
~/Assistant/Email/emails.db
~/Assistant/Contacts/contacts.db
~/Assistant/Todos/todos.db
```

**Decision: leave the data in place. Do not delete it.**

- New installs must not create these directories. A test asserts a fresh data
  root under a normal server start contains no `Email/`, `Contacts/`, or
  `Todos/`.
- Existing directories become inert — nothing reads or writes them.
- `DEPLOYMENT.md` records that the data is retained but unread, so an operator
  can restore a backup or export it deliberately.

Deleting user data as a side effect of a code change is not something to do
without asking. Retaining it costs disk and nothing else.

---

## 8. Test contract

| # | Test | Assertion |
|---|---|---|
| 1 | No such tools | `email_*`, `contacts_*`, `todos_*` absent from the registry in **all** deployment modes |
| 2 | No such routes | `/email*`, `/todos*`, `/contacts*` return 404 |
| 3 | Fresh data root | Server start creates no `Email/`, `Contacts/`, `Todos/` directory |
| 4 | Imports clean | No module imports a removed symbol; `ruff` and a repo-wide import check pass |
| 5 | Dependencies | `imap-tools` is gone from `pyproject.toml` and the lockfile |
| 6 | Governance fixtures | Every governance test that used these tools as subjects now exercises a surviving governed tool |
| 7 | **External executor replaces Gmail** | The fixture drives approval → dispatch → callback → receipt with idempotent replay |
| 8 | ConnectKit intact | File-sync adapters and the connectors router still resolve and pass their tests |
| 9 | Seeded templates | A `tasks` template app exists and the agent can insert into it without designing a schema |
| 10 | Full suite | All remaining tests pass. No assertion is deleted to make this true |

Test 6 and test 10 are the two that will catch a careless removal. If a test
disappears because its subject disappeared, that is a coverage loss, not a pass.

---

## 9. Documentation updates

| File | Change |
|---|---|
| `README.md` | Remove the Email row; replace Tasks & Contacts with "Structured data — build your own with the app builder" |
| `AGENTS.md` | Remove from the tool inventory and the phase tables; note the `apps_*` replacement |
| `DEPLOYMENT.md` | Remove `EMAIL_*` secrets; add the retained-data note (§7) |
| `.env.example`, `docker/.env.example` | Remove `EMAIL_*` |
| `docs/architecture/macos-dmg-v0.1.0-backend-impact-review.md` | §3.3 is satisfied by deletion rather than a profile; record that and drop the per-family exclusion requirement |
| `CHANGELOG.md` | Record the removal as a breaking change, the `apps_*` replacement, and the governance fixture |

---

## 10. Sequencing

Single branch, in this order, each step independently verifiable:

1. Delete tools + storage + routers; unwrap `_desktop_excluded`
2. Delete config, DataPaths, dependency, secrets
3. Delete removable tests; re-point governance fixtures
4. Add the external-executor governance fixture (**before** the suite goes green — it is the proof replacement)
5. Add seeded template apps
6. Documentation

Step 4 lands before the suite is declared green so the governance regression is
never actually committed.
