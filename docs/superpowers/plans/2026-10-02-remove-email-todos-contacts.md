# Remove Email, Contacts, and Todos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the `email_*`, `contacts_*`, and `todos_*` tool families and their supporting code, replacing them with the existing `app_*` structured-data family plus seeded template apps, without weakening any governance assertion.

**Architecture:** A single deletion branch. The families are removed from the tool registry, their storage and HTTP routers are deleted, configuration and path accessors are removed, and the now-childless `_desktop_excluded` filter is deleted. A test-only external-executor fixture tool replaces the Gmail-send vertical slice as the end-to-end evidence that durable governed operations work, and that fixture must land before the suite is declared green.

**Tech Stack:** Python 3.11+, `AgentLoop` / `ToolRegistry` / `ToolAnnotations`, `governance_operations.py` external executors, pytest + pytest-asyncio, existing seed-refresh pattern with `.seed-hash` sidecars.

**Spec:** [`docs/superpowers/specs/2026-10-02-remove-email-todos-contacts-design.md`](../specs/2026-10-02-remove-email-todos-contacts-design.md)

## Execution record — 2026-10-06

**Status:** Tasks 1–9 implemented and verified in isolated branch
`refactor/remove-legacy-families`; not merged, pushed, released or deployed.
Baseline: `de478f49`. Final runtime/test/package tree: `497172a9`.
The original task instructions below are retained for reproduction; this record
and the worktree's ignored progress ledger record actual execution.

- Full suite: **3,810 passed, 27 skipped, 70 warnings in 534.95s**, process **exit 0**.
- Opt-in Phase 0: **4 passed**, exit 0. Governance final slice: **38 passed**, exit 0.
- Source Ruff clean. Scoped mypy clean across five changed non-router modules
  with `--no-incremental --follow-imports=silent`; a subsequent cached subset
  check echoed inherited dependency diagnostics, while the fresh check was clean.
  Whole-source mypy is **not clean**: 26 inherited errors versus 34 at baseline,
  with **zero new errors**. Three inherited non-router diagnostics remain in
  `sandbox.py` and `mcp_config.py`; the original router-only exception assumption
  was inaccurate. No unrelated typing changes were made.
- Collection: **3,897 baseline → 3,837 current**. At the original reviewed tree,
  114 node IDs were removed/renamed and 46 added/renamed; eight more review
  regressions were then added. Retired fixed-domain CRUD/parsing assertions leave
  with their subjects; generic platform/governance invariants are re-homed, not
  silently claimed as preserved legacy feature behavior.
- Independent read-only review found three Important template issues (derived
  state symlinks, mkdir-before-ancestor-check, missing wheel assets) and an
  already-current validation gap. Eight RED cases reproduced them; all fixed and
  closed by scoped follow-up review. Extracted-wheel smoke builds offline with
  already-cached dependencies and loads/materialises all three templates; no
  installation/download occurs.
- First full attempt hit an outer 900s timeout before summary and is **not** a
  pass. A diagnostic run at `efc11d0a` completed with one elapsed-time assertion
  failure in unchanged `test_custom_tool_timeout.py`; baseline/current isolated
  runs passed (30 each). The exact final-tree rerun above passed. Existing
  collection/deprecation/aiosqlite closed-loop warnings remain unsuppressed; no
  unrelated timing/shutdown fix is claimed.
- Fresh/retained-store lifespan tests, actual app insert/query, user-state
  preservation, global API/module removal and fixture governance are verified.
  Template seeding is **explicit operator use on an initial/stopped store**;
  no automatic legacy import, startup seeding, fixture deployment mode or
  first-party mail replacement was added. Generic ConnectKit, file-sync,
  coding/browser and CoreMem remain.
- Docker/native/live customer/vendor tests remain outside this slice. Main's
  concurrent changes and dirty files were not integrated or overwritten.

## Global Constraints

- Deletion is not a pass. A test removed because its subject was removed is a coverage loss, not a green run. Every removed test's *assertion* must be re-homed onto a surviving tool.
- `connectkit==0.1.4` **stays**. `src/http/routers/connectors.py` and `src/sdk/tools_core/file_sync.py` remain its live consumers. Only `src/storage/gmail_client.py` and `src/storage/gmail_cache.py` are deleted.
- Existing user data under `~/Assistant/{Email,Contacts,Todos}/` is **retained, never deleted**. A fresh data root must not create those directories.
- No new `ToolAnnotations` fields. No tool metadata work in this plan.
- The repository must not be left with a thinner governance proof than it had before the deletion. Task 4 lands before Task 6 declares green.
- Commands run from the repo root. Use `uv run pytest`, `uv run ruff check src/`.
- Standard suite excludes the `phase0` marker by default. Run opt-in gates as `uv run pytest tests/integration/phase0_gate.py -m phase0 -q`.
- Scoped mypy must be clean for changed non-router modules. Existing `src/http/routers/*` errors are a separate cleanup.

## Review Focus

Most likely to bite a person using this software, most first:

1. **A governance test silently loses its subject.** `tests/sdk/test_governance.py` and `tests/api/test_governance_api.py` may use an email or todo tool as the vehicle for a permission assertion. Deleting the tool without re-homing the assertion converts a real check into a passing absence.
2. **`src/sdk/native_tools.py` still imports a deleted module.** The import block at lines ~36-39 pulls in `connector_gmail_send` and `email_draft`. A stale import surfaces as a collection error, not a clean failure.
3. **A new directory gets created on startup.** `DataPaths` accessors are the only thing creating `Email/`; removing them must not be defeated by a caller that builds the path inline.
4. **`imap-tools` still in `pyproject.toml`.** An unused dependency is a lint failure at best and an unnecessary attack surface at worst.
5. **An existing install's data directory is deleted by a migration or startup path.** The plan removes *writers*, never data. Any code that unlinks or moves `~/Assistant/Email/` is out of scope and must be rejected in review.

---

## File Structure

**Deleted (14 files):**

| Path | Reason |
|---|---|
| `src/sdk/tools_core/email_draft.py` | `email_draft` tool |
| `src/sdk/tools_core/email_db.py` | account + IMAP + parse |
| `src/sdk/tools_core/email_sync.py` | sync + background job |
| `src/sdk/tools_core/connector_gmail.py` | `connector_gmail_send` |
| `src/sdk/tools_core/todos.py` | 4 todo tools |
| `src/sdk/tools_core/todos_storage.py` | todo store |
| `src/sdk/tools_core/contacts.py` | 5 contact tools |
| `src/sdk/tools_core/contacts_storage.py` | contact store |
| `src/http/routers/email.py` | email API |
| `src/http/routers/todos.py` | todo API |
| `src/http/routers/contacts.py` | contact API |
| `src/storage/email_db.py` | email store |
| `src/storage/gmail_client.py` | Gmail REST client (ConnectKit-backed, email-only consumer) |
| `src/storage/gmail_cache.py` | GmailCache |

**Modified:**

| Path | Change |
|---|---|
| `src/sdk/native_tools.py` | delete 2 imports, delete 2 registrations, delete `_desktop_filtering_active` / `_desktop_excluded` / `DESKTOP_EXCLUDED_FAMILIES`, unwrap ~45 `if not _desktop_excluded(...)` guards |
| `src/http/main.py` | delete 3 router imports, delete the `if not desktop_mode_active():` block at ~411-414 mounting them |
| `src/http/routers/__init__.py` | delete 3 router exports |
| `src/config/settings.py` | delete `EmailConfig`, `EmailSyncConfig`, and their `email` / `email_sync` fields |
| `src/storage/paths.py` | delete `email_dir`, `contacts_dir`, `todos_dir`, `email_db`, `contacts_db`, `todos_db`, `team_contacts_dir`, `team_todos_dir` |
| `src/sdk/capabilities.py` | delete `"email_draft"` from the desktop capability set |
| `pyproject.toml`, `uv.lock` | drop `imap-tools` |
| `.env.example`, `docker/.env.example`, `DEPLOYMENT.md` | delete `EMAIL_GWS_CLIENT_ID`, `EMAIL_GWS_CLIENT_SECRET`, `EMAIL_M365_CLIENT_ID` |

**Created:**

| Path | Responsibility |
|---|---|
| `src/sdk/tools_core/governance_fixture.py` | test-only external-executor tool proving the durable governed-operations path |
| `tests/sdk/test_governance_fixture.py` | its approval → dispatch → callback → receipt proof |
| `tests/sdk/test_no_removed_families.py` | regression: the three families are gone in every mode |
| `seeds/apps/tasks.json` | seeded template: tasks schema |
| `seeds/apps/contacts.json` | seeded template: contacts schema |
| `seeds/apps/reading-list.json` | seeded template: demonstrates user-chosen shape |
| `src/storage/app_templates.py` | loads `seeds/apps/*.json` with `.seed-hash` refresh |
| `tests/storage/test_app_templates.py` | template load + hash-refresh behaviour |

**Deleted tests (whole file):** `tests/api/test_email.py`, `tests/api/test_contacts.py`, `tests/unit/test_contacts_tools.py`, `tests/evaluation/test_email_tools.py`, `tests/storage/test_gmail_client.py`, `tests/sdk/test_email_draft.py`

**Modified tests (re-home assertions, do not delete):** `tests/sdk/test_tool_contracts.py`, `tests/sdk/test_governance.py`, `tests/api/test_governance_api.py`, `tests/sdk/test_permission_policy.py`, `tests/unit/test_complex_apps.py`

---

### Task 1: Pin the removal with a failing regression test

**Files:**
- Create: `tests/sdk/test_no_removed_families.py`

**Interfaces:**
- Consumes: `src.sdk.native_tools.get_native_tools() -> list[ToolDefinition]`, `src.storage.paths.DataPaths`
- Produces: the test module every later task must keep green

- [ ] **Step 1: Write the failing test**

```python
# tests/sdk/test_no_removed_families.py
"""Regression: the email/contacts/todos families are absent, not disabled.

This is a deletion, so the assertion is on the tool registry itself rather
than on any capability profile. It must hold in every deployment mode.
"""

REMOVED_PREFIXES = ("email_", "contacts_", "todos_")


def test_removed_families_absent_from_native_tools() -> None:
    from src.sdk.native_tools import get_native_tools

    names = {t.name for t in get_native_tools()}
    leaked = sorted(n for n in names if n.startswith(REMOVED_PREFIXES))
    assert leaked == [], f"removed tools still registered: {leaked}"


def test_removed_modules_are_not_importable() -> None:
    import importlib

    for module in (
        "src.sdk.tools_core.email_draft",
        "src.sdk.tools_core.email_db",
        "src.sdk.tools_core.email_sync",
        "src.sdk.tools_core.connector_gmail",
        "src.sdk.tools_core.todos",
        "src.sdk.tools_core.todos_storage",
        "src.sdk.tools_core.contacts",
        "src.sdk.tools_core.contacts_storage",
        "src.storage.email_db",
        "src.storage.gmail_client",
        "src.storage.gmail_cache",
    ):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{module} is still importable")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/sdk/test_no_removed_families.py -v`
Expected: FAIL — `test_removed_families_absent_from_native_tools` reports the current tool names, and `test_removed_modules_are_not_importable` raises on `src.sdk.tools_core.todos`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/sdk/test_no_removed_families.py
git commit -m "test: pin removal of email, contacts, and todos families"
```

A failing test committed alone is intentional: it is the RED state the rest of the plan turns green.

---

### Task 2: Delete the tool and storage modules

**Files:**
- Delete: the 14 files listed in File Structure
- Modify: `src/sdk/native_tools.py` (imports at ~36-39, registrations at ~195-198)
- Modify: `src/http/routers/__init__.py`

**Interfaces:**
- Consumes: `Task 1`'s `tests/sdk/test_no_removed_families.py`
- Produces: a registry with no removed-family tools; `get_native_tools()` unchanged in signature

- [ ] **Step 1: Delete the 14 files**

```bash
git rm src/sdk/tools_core/email_draft.py src/sdk/tools_core/email_db.py \
       src/sdk/tools_core/email_sync.py src/sdk/tools_core/connector_gmail.py \
       src/sdk/tools_core/todos.py src/sdk/tools_core/todos_storage.py \
       src/sdk/tools_core/contacts.py src/sdk/tools_core/contacts_storage.py \
       src/http/routers/email.py src/http/routers/todos.py \
       src/http/routers/contacts.py src/storage/email_db.py \
       src/storage/gmail_client.py src/storage/gmail_cache.py
```

- [ ] **Step 2: Remove the imports in `src/sdk/native_tools.py`**

Delete these two import lines:

```python
from src.sdk.tools_core.connector_gmail import connector_gmail_send
from src.sdk.tools_core.email_draft import email_draft
```

Delete the two registration blocks that reference them:

```python
    if not _desktop_excluded("email_draft"):
        registry.register(email_draft)
    if not _desktop_excluded("connector_gmail_send"):
        registry.register(connector_gmail_send)
```

Leave `_desktop_excluded` itself in place for now — Task 3 removes it. Leaving it keeps this commit revertable on its own.

- [ ] **Step 3: Remove the router exports**

In `src/http/routers/__init__.py`, delete `contacts_router`, `email_router`, and `todos_router` from the import list and from `__all__`.

- [ ] **Step 4: Run the regression test**

Run: `uv run pytest tests/sdk/test_no_removed_families.py -v`
Expected: `test_removed_families_absent_from_native_tools` PASSES. `test_removed_modules_are_not_importable` PASSES.

- [ ] **Step 5: Run the SDK suite to find the real breakage**

Run: `uv run pytest tests/sdk/ -q`
Expected: failures confined to test modules importing deleted symbols. Record every failing module name in the commit message body — Task 5 needs this list.

- [ ] **Step 6: Commit**

```bash
git add -A src/sdk/native_tools.py src/http/routers/__init__.py
git commit -m "refactor: delete email, contacts, and todos tool modules

Removes 14 modules (~2,780 lines). Test modules now failing on deleted
imports are re-homed in the follow-up task; see commit body for the list."
```

---

### Task 3: Delete the `_desktop_excluded` filter and unmount the routers

**Files:**
- Modify: `src/sdk/native_tools.py` (`_register_all`, ~108-300)
- Modify: `src/http/main.py` (imports ~19-38, mount block ~411-414)

**Interfaces:**
- Consumes: `Task 2`'s deletion
- Produces: `_register_all()` with no desktop branching; `app` with no email/todos/contacts routes

- [ ] **Step 1: Write the failing test**

Add to `tests/sdk/test_no_removed_families.py`:

```python
def test_no_removed_routes_are_mounted() -> None:
    from src.http.main import app

    mounted = {getattr(r, "path", "") for r in app.routes}
    leaked = sorted(
        p for p in mounted
        if p.startswith(("/email", "/todos", "/contacts"))
    )
    assert leaked == [], f"removed routers still mounted: {leaked}"


def test_native_tools_module_has_no_desktop_filter() -> None:
    import src.sdk.native_tools as nt

    assert not hasattr(nt, "_desktop_excluded")
    assert not hasattr(nt, "DESKTOP_EXCLUDED_FAMILIES")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/sdk/test_no_removed_families.py -v`
Expected: FAIL — routes still mounted, `_desktop_excluded` still present.

- [ ] **Step 3: Unwrap the registration guards**

In `src/sdk/native_tools.py`, delete `DESKTOP_EXCLUDED_FAMILIES`, `_desktop_filtering_active`, and `_desktop_excluded`, then rewrite every remaining guard of this shape:

```python
    if not _desktop_excluded("files_read"):
        registry.register(files_read)
```

into:

```python
    registry.register(files_read)
```

Preserve registration order exactly. Order is load-bearing for schema budget and for any test asserting a tool's index position.

- [ ] **Step 4: Unmount the routers**

In `src/http/main.py`, delete `contacts_router`, `email_router`, and `todos_router` from the `src.http.routers` import block, then delete:

```python
if not desktop_mode_active():
    app.include_router(email_router)
    app.include_router(contacts_router)
    app.include_router(todos_router)
```

Do not delete the `if not desktop_mode_active():` guards around other routers — those still gate real behaviour.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/sdk/test_no_removed_families.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: only the known-failing test modules from Task 2 Step 5 fail. Any *new* failure is a bug in this task.

- [ ] **Step 7: Commit**

```bash
git add src/sdk/native_tools.py src/http/main.py tests/sdk/test_no_removed_families.py
git commit -m "refactor: remove the childless desktop family filter

DESKTOP_EXCLUDED_FAMILIES excluded only the three families removed in the
previous commit, so it no longer has a consumer. Unwrapping it also removes
~45 conditional guards from _register_all."
```

---

### Task 4: Replace the Gmail governance proof with an external-executor fixture

**Files:**
- Create: `src/sdk/tools_core/governance_fixture.py`
- Create: `tests/sdk/test_governance_fixture.py`

**Interfaces:**
- Consumes: `src.sdk.governance_operations` (durable operation ledger), `src.sdk.governance_dispatcher` (dispatch), `ToolAnnotations.executor: ExternalHTTPExecutor`
- Produces: `GOVERNANCE_FIXTURE: ToolDefinition` and an end-to-end test that the D3 gate's Gmail slice previously provided

**Do this task before declaring the suite green.** It is the reason the deletion is safe.

- [ ] **Step 1: Write the failing test**

```python
# tests/sdk/test_governance_fixture.py
"""The external-executor path proves durable governed operations.

This replaces the permission-gated Gmail send as end-to-end evidence: same
ledger, same dispatcher, same HMAC callback, no vendor integration.
"""

import pytest


@pytest.mark.asyncio
async def test_external_executor_operation_reaches_receipt(tmp_path, monkeypatch):
    from src.sdk.governance import GovernanceService
    from src.sdk.tools_core.governance_fixture import GOVERNANCE_FIXTURE

    svc = GovernanceService(tmp_path / "governance.db")
    pending = svc.create_pending(
        user_id="u1", tool="governance_fixture", args={"payload": "hello"}
    )

    op = svc.approve(pending.proposal_id, user_id="u1")
    assert op.operation_id

    # Dispatch is refused without a reachable executor; the refusal must be
    # a durable `uncertain` status, never an automatic replay.
    status = svc.get_operation(op.operation_id)
    assert status.status in {"queued", "uncertain", "running"}
    assert status.status != "succeeded"


def test_fixture_is_governed_not_read_only() -> None:
    from src.sdk.tools_core.governance_fixture import GOVERNANCE_FIXTURE

    ann = GOVERNANCE_FIXTURE.annotations
    assert ann is not None
    assert ann.read_only is False
    assert ann.execution_mode == "async"
    assert ann.executor is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/sdk/test_governance_fixture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.sdk.tools_core.governance_fixture'`

- [ ] **Step 3: Implement the fixture tool**

Create `src/sdk/tools_core/governance_fixture.py` defining `GOVERNANCE_FIXTURE`, a `ToolDefinition` named `governance_fixture`, with a `ToolAnnotations` carrying:

- `title="Governance Fixture"`
- `read_only=False`, `destructive=True`
- `execution_mode="async"`
- `requires_approval=True`
- `executor=ExternalHTTPExecutor(...)` pointing at a non-routable loopback port so dispatch cannot accidentally succeed

Its body raises `NotImplementedError("fixture is dispatched, never executed in-process")`. It is registered in `native_tools.py` only under `DEPLOYMENT_MODE=governance-fixture`, so it never reaches a normal agent.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/sdk/test_governance_fixture.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sdk/tools_core/governance_fixture.py tests/sdk/test_governance_fixture.py src/sdk/native_tools.py
git commit -m "test: replace the Gmail governance proof with an external-executor fixture"
```

---

### Task 5: Re-home governance assertions onto surviving tools

**Files:**
- Modify: `tests/sdk/test_tool_contracts.py` (~206-260)
- Modify: `tests/sdk/test_governance.py`, `tests/api/test_governance_api.py`, `tests/sdk/test_permission_policy.py`
- Modify: `tests/unit/test_complex_apps.py`

**Interfaces:**
- Consumes: the failing-module list from Task 2 Step 5
- Produces: a suite where every governance assertion runs against a tool that still exists

**This task deletes no assertion. It re-points them.**

- [ ] **Step 1: Enumerate the assertions that will be lost**

Run:

```bash
uv run pytest tests/ -q 2>&1 | grep -E "^(ERROR|FAILED)" | sort -u
```

Write the list into the commit body. Every entry is either a test to re-home or a test whose only subject was removed.

- [ ] **Step 2: Re-home `test_tool_contracts.py`**

The todos and contacts contract tests at ~206-260 exercise the `@tool` invocation contract: `invoke()` returns a `ToolResult`, state persists across calls, and identifiers are stable. Re-point them at `app_create` / `app_insert` / `app_list`, which have the same contract shape:

```python
def test_app_create_and_list(self):
    from src.sdk.tools_core.apps import app_create, app_list

    created = app_create.invoke(
        {"name": "contract_tasks", "tables": {"items": {"title": "TEXT"}}, "user_id": "test_contract"},
    )
    assert getattr(created, "is_error", False) is False

    listed = app_list.invoke({"user_id": "test_contract"})
    assert "contract_tasks" in str(listed.content if hasattr(listed, "content") else listed)
```

Keep one test per contract property that existed before. Do not collapse five tests into one.

- [ ] **Step 3: Re-home governance and permission-policy assertions**

Any assertion whose vehicle was an email/todo/contact tool switches to a surviving destructive tool with the same annotation shape — `files_delete` (`destructive=True`, `read_only=False`) or `app_delete_row` — so the permission branch under test is unchanged.

- [ ] **Step 4: Verify no assertion was dropped**

```bash
git diff --stat tests/
uv run pytest tests/ -q
```

Expected: the only deleted test *names* are those whose sole subject was a removed module. Any test that asserted governance behaviour must appear as **modified**, not deleted.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: re-home governance and contract assertions onto surviving tools

No assertion is deleted. Test names that disappear did so because their
only subject was a removed module; every governance assertion is now
exercised against files_delete or app_delete_row."
```

---

### Task 6: Remove configuration, paths, and the dependency

**Files:**
- Modify: `src/config/settings.py` (`EmailConfig` ~499, `EmailSyncConfig` ~604, fields at ~771/~779)
- Modify: `src/storage/paths.py` (~168-183, ~301-308, ~466-474)
- Modify: `src/sdk/capabilities.py` (desktop set, `"email_draft"`)
- Modify: `pyproject.toml`, `uv.lock`
- Create: `tests/sdk/test_no_email_config.py`

**Interfaces:**
- Consumes: Tasks 2-3 deletions
- Produces: a config schema with no email surface; a data root with no `Email/`/`Contacts/`/`Todos/`

- [ ] **Step 1: Write the failing test**

```python
# tests/sdk/test_no_email_config.py
def test_settings_expose_no_email_config() -> None:
    from src.config.settings import AppConfig

    cfg = AppConfig()
    assert not hasattr(cfg, "email")
    assert not hasattr(cfg, "email_sync")


def test_paths_expose_no_removed_dirs(tmp_path) -> None:
    from src.storage.paths import DataPaths

    dp = DataPaths(data_root=tmp_path, data_path=tmp_path)
    for attr in ("email_dir", "contacts_dir", "todos_dir",
                 "email_db", "contacts_db", "todos_db",
                 "team_contacts_dir", "team_todos_dir"):
        assert not hasattr(dp, attr), f"{attr} still exists"


def test_fresh_data_root_creates_no_removed_dirs(tmp_path) -> None:
    from src.storage.paths import DataPaths

    dp = DataPaths(data_root=tmp_path / "root", data_path=tmp_path / "root")
    dp.workspace_files_dir()
    dp.workspace_memory_dir()
    for name in ("Email", "Contacts", "Todos"):
        assert not (tmp_path / "root" / name).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/sdk/test_no_email_config.py -v`
Expected: FAIL — `cfg.email` and `dp.email_dir` still exist.

- [ ] **Step 3: Delete the config classes and fields**

In `src/config/settings.py`, delete `class EmailConfig`, `class EmailSyncConfig`, and the `email_sync: EmailSyncConfig` and `email: EmailConfig` fields on the app config.

- [ ] **Step 4: Delete the path accessors**

In `src/storage/paths.py`, delete `email_dir`, `contacts_dir`, `todos_dir`, `email_db`, `contacts_db`, `todos_db`, `team_contacts_dir`, `team_todos_dir`.

- [ ] **Step 5: Remove `"email_draft"` from the capability set**

In `src/sdk/capabilities.py`, delete the `"email_draft"` entry from the set containing `"interview_start"`, `"app_summarize"`, and `"code_execute"`.

- [ ] **Step 6: Drop the dependency**

```bash
uv remove imap-tools
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/sdk/test_no_email_config.py -v`
Expected: all 3 PASS.

- [ ] **Step 8: Run lint and the full suite**

Run: `uv run ruff check src/ && uv run pytest -q`
Expected: ruff clean; suite green.

- [ ] **Step 9: Commit**

```bash
git add src/config/settings.py src/storage/paths.py src/sdk/capabilities.py \
        pyproject.toml uv.lock tests/sdk/test_no_email_config.py
git commit -m "refactor: remove email configuration, paths, and imap-tools"
```

---

### Task 7: Seed the replacement template apps

**Files:**
- Create: `seeds/apps/tasks.json`, `seeds/apps/contacts.json`, `seeds/apps/reading-list.json`
- Create: `src/storage/app_templates.py`, `tests/storage/test_app_templates.py`

**Interfaces:**
- Consumes: `src.sdk.tools_core.apps` schema shape (`{"tables": {name: {col: type}}}`)
- Produces: `load_app_templates() -> list[AppTemplate]` and `seed_app_templates(user_id: str) -> list[str]`, matching the existing seed-refresh pattern with `.seed-hash` sidecars

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_app_templates.py
def test_templates_load_from_seed_dir() -> None:
    from src.storage.app_templates import load_app_templates

    names = {t.name for t in load_app_templates()}
    assert {"tasks", "contacts", "reading-list"} <= names


def test_seeding_is_idempotent(tmp_path) -> None:
    from src.storage.app_templates import seed_app_templates

    first = seed_app_templates("u1", data_root=tmp_path)
    second = seed_app_templates("u1", data_root=tmp_path)
    assert first == second


def test_user_modified_template_is_not_overwritten(tmp_path) -> None:
    from src.storage.app_templates import seed_app_templates

    seed_app_templates("u1", data_root=tmp_path)
    marker = tmp_path / "Tasks" / ".seed-hash"
    marker.write_text("user-edited", encoding="utf-8")
    seed_app_templates("u1", data_root=tmp_path)
    assert marker.read_text(encoding="utf-8") == "user-edited"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/storage/test_app_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: src.storage.app_templates`

- [ ] **Step 3: Write the three template files**

Each is a JSON object with `name`, `description`, and `tables`, matching the `app_create` schema. `tasks` uses `items(title TEXT, status TEXT, due TEXT)`; `contacts` uses `people(name TEXT, email TEXT, org TEXT)`; `reading-list` uses `entries(title TEXT, url TEXT, status TEXT)` to show the shape is the user's to choose.

- [ ] **Step 4: Implement the loader**

`src/storage/app_templates.py` exposes:

```python
@dataclass(frozen=True)
class AppTemplate:
    name: str
    description: str
    tables: dict[str, dict[str, str]]

def load_app_templates(seed_dir: Path | None = None) -> list[AppTemplate]: ...
def seed_app_templates(user_id: str, *, data_root: Path) -> list[str]: ...
```

`seed_app_templates` writes a `.seed-hash` sidecar per template. Re-seeding refreshes a template only when the seed file's hash changed **and** the sidecar still matches the previous seed hash. Follow the existing pattern in `src/sdk/runner.py::_ensure_prompt_seeded`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/storage/test_app_templates.py -v`
Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add seeds/apps src/storage/app_templates.py tests/storage/test_app_templates.py
git commit -m "feat: seed tasks, contacts, and reading-list template apps"
```

---

### Task 8: Update documentation and secrets

**Files:**
- Modify: `README.md`, `AGENTS.md`, `DEPLOYMENT.md`, `.env.example`, `docker/.env.example`
- Modify: `docs/architecture/macos-dmg-v0.1.0-backend-impact-review.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 2, 3, 6, 7
- Produces: documentation that matches the code

- [ ] **Step 1: Remove the email secrets**

Delete `EMAIL_GWS_CLIENT_ID`, `EMAIL_GWS_CLIENT_SECRET`, and `EMAIL_M365_CLIENT_ID` from `.env.example`, `docker/.env.example`, and the DEPLOYMENT.md secrets table. Verify no reference survives:

```bash
rg -n "EMAIL_GWS|EMAIL_M365" . --glob '!uv.lock' --glob '!.git/*'
```
Expected: no output.

- [ ] **Step 2: Update the README features table**

Delete the Email row. Replace the Tasks & Contacts row with:

```markdown
| **Structured Data** | Build your own tables and queries — tasks, contacts, anything. The assistant creates the schema you need. |
```

- [ ] **Step 3: Update AGENTS.md**

Remove `email_*`, `contacts_*`, and `todos_*` from the tool inventory and phase tables. Add a line noting the `apps_*` family is the replacement. Remove the `EMAIL_*` environment variables from the configuration section.

- [ ] **Step 4: Record the retained-data policy in DEPLOYMENT.md**

Add a short section: existing installs retain `~/Assistant/{Email,Contacts,Todos}/` unread; new installs do not create them; operators may export before deleting.

- [ ] **Step 5: Update the desktop review memo**

In §3.3, record that the three families are satisfied by deletion rather than a capability profile, and that `DESKTOP_EXCLUDED_FAMILIES` is gone.

- [ ] **Step 6: Write the changelog entry**

Record it as a **breaking change**, the `apps_*` + seeded-template replacement, ConnectKit's continued existence with `file_sync` as its only live consumer, the external-executor governance fixture, and the retained-data policy.

- [ ] **Step 7: Verify docs match code**

```bash
rg -n "todos_|contacts_|email_draft|connector_gmail" README.md AGENTS.md DEPLOYMENT.md
```
Expected: no output, except the DEPLOYMENT.md retained-data note written in Step 4.

- [ ] **Step 8: Commit**

```bash
git add README.md AGENTS.md DEPLOYMENT.md .env.example docker/.env.example \
        docs/architecture/macos-dmg-v0.1.0-backend-impact-review.md CHANGELOG.md
git commit -m "docs: record the removal of email, contacts, and todos"
```

---

### Task 9: Final gate

**Files:** none — verification only

**Interfaces:**
- Consumes: every prior task
- Produces: the evidence that the branch is shippable

- [ ] **Step 1: Full suite**

```bash
uv run pytest -q
```
Expected: green.

- [ ] **Step 2: Opt-in Phase 0 gate**

```bash
uv run pytest tests/integration/phase0_gate.py -m phase0 -q
```
Expected: 4 passed.

- [ ] **Step 3: Lint and scoped mypy**

```bash
uv run ruff check src/
uv run mypy src/ | grep -v "src/http/routers/" || true
```
Expected: ruff clean; no mypy errors outside the pre-existing `src/http/routers/*` set.

- [ ] **Step 4: Confirm the governance proof exists**

```bash
uv run pytest tests/sdk/test_governance_fixture.py tests/sdk/test_governance.py \
                  tests/api/test_governance_api.py -q
```
Expected: green — the deletion did not leave the repository without end-to-end governed-operation evidence.

- [ ] **Step 5: Confirm nothing survives**

```bash
rg -l "email_draft|connector_gmail_send|todos_add|contacts_add|start_interval_sync" src/
```
Expected: no output.

- [ ] **Step 6: Confirm the test count fell, not collapsed**

Compare `uv run pytest --collect-only -q | tail -1` against the pre-branch count (3,553 on `v0.6.25`). A drop larger than the ~15 removable files plus the re-homed replacements means an assertion was lost — investigate before shipping.

- [ ] **Step 7: Commit any residual fixups, then report**

Do not open a follow-up branch for anything this plan should have caught.
