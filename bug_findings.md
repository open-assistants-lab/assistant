# Bug Findings — Assistant Codebase

**Review date:** 2026-06-14  
**Scope:** Uncommitted changes in working tree (`git status`) plus a deeper pass over affected modules  
**Test result:** 1,193 passed, 16 skipped (full suite); 47 passed (integration/smoke); 28 passed (new untracked SDK tests)

---

## 1. Critical / Runtime Bugs

### 1.1 `src/http/routers/email.py` — `asyncio` not imported at module scope
- **Location:** lines 88–93
- **Issue:** `_sync_gmail` calls `asyncio.create_subprocess_exec` and `asyncio.subprocess.PIPE`, but `asyncio` is only imported locally inside `handle_sync` (line 60). The function-scoped import is not visible to `_sync_gmail`, so the background sync task will raise `NameError`.
- **Fix:** Add `import asyncio` at the top of the file.

### 1.2 `src/storage/messages.py` — `Any` is not imported
- **Location:** lines 307, 380
- **Issue:** The code uses `tuple[Any, ...]` but `Any` is not imported. This is flagged by `ruff` (F821) and mypy. Because Python resolves type annotations lazily, calling the functions does not immediately raise `NameError` at runtime; the annotations are only evaluated if introspected. However, the file does not pass lint/type checking and is fragile.
- **Fix:** Add `from typing import Any`.

### 1.3 `src/storage/messages.py` — `add_summary_message` return type mismatch
- **Location:** line 331
- **Issue:** Declared `-> int`, but it returns the result of `self.add_message(...)`, which returns a string id. Runtime returns `str`.
- **Fix:** Change return type to `str`.

### 1.4 `src/http/main.py` — semicolon import
- **Location:** line 193
- **Issue:** `import traceback; traceback.print_exc()` on one line violates E702 (semicolon). Not a runtime crash, but blocks lint.
- **Fix:** Split into two lines.

### 1.5 `src/skills_seed/skill-creation/eval-viewer/__init__.py` — invalid file content
- **Location:** `src/skills_seed/skill-creation/eval-viewer/__init__.py`
- **Issue:** File contains literal text `404: Not Found`, which is invalid Python syntax. The module name `eval-viewer` is also invalid. Any import under this package will fail.
- **Fix:** Delete the file or replace it with valid package content.

---

## 2. Logic / Design Bugs

### 2.1 `src/sdk/tools_core/email.py` — unused `folder_list`
- **Location:** line 58
- **Issue:** After login, the list of folders is computed and discarded. There is no verification that the account can actually access the INBOX. The result is also unused, which suggests incomplete validation after the SQLAlchemy→AgentsDB refactor.
- **Fix:** Either verify that `"INBOX"` is in `folder_list`, or remove the variable.

### 2.2 `src/sdk/tools_core/email_db.py` / `email_sync.py` — UID reused as `message_id`
- **Location:** `email_db.py` line 133; `email_sync.py` lines 170–184
- **Issue:** `email_to_dict` stores `message_id = str(msg.uid)`. IMAP UIDs are folder-scoped and can be reused after messages are expunged. Combined with the new AgentsDB storage, lookups by `message_id` in `email_get` and REST endpoints can return the wrong message after a sync.
- **Fix:** Use a stable identifier such as `Message-Id` header or a hash of account + folder + UID.

### 2.3 `src/sdk/tools_core/email_sync.py` — "new" sync mode ignores `last_timestamp`
- **Location:** lines 211–212
- **Issue:** The code reads `last_timestamp` but then fetches `mailbox.fetch(limit=limit, reverse=True)` with no date or UID criteria. It relies on skipping already-synced IDs, which means it always fetches the newest N messages. Inefficient and unnecessarily slow for large inboxes.
- **Fix:** Add a UID-range or date criterion to the fetch.

### 2.4 `src/sdk/tools_core/todos_storage.py:todos_count` — O(N) counting in Python
- **Location:** lines 113–120
- **Issue:** Loads up to 10,000 todos and counts statuses in Python. Counts are wrong above the cap.
- **Fix:** Use a SQL `COUNT(*)` query via `raw_query`.

### 2.5 `src/sdk/companion_scheduler.py:_count_urgent_emails` — same O(N) issue
- **Location:** lines 224–244
- **Issue:** Fetches up to 100,000 unread emails and counts with `len(emails)`. Wrong above the cap and unnecessarily slow.
- **Fix:** Use a `COUNT(*)` query through AgentsDB.

### 2.6 `src/sdk/coordinator.py` — fallback `SubagentContext` lacks task_id
- **Location:** line 487
- **Issue:** `_run_loop` uses `subagent_ctx=ctx or SubagentContext()`. If `ctx` is ever `None`, the new context has no `task_id` and no `on_progress` callback. Loop cancellation would then raise `SubagentCancelledError("")` with an empty id.
- **Fix:** Always require a real `SubagentContext`; assert or explicitly construct one with `task_id`.

---

## 3. Type / Annotation Issues

### 3.1 `src/sdk/tools_core/todos_storage.py` — `str` passed as `row_id: int`
- **Location:** line 92
- **Issue:** `db.update_todo(todo["id"], ...)` passes `todo["id"]` (a `str`, set during insert as `str(uuid.uuid4())[:8]`) where `update_todo` expects `int`. In practice HybridDB's `query_todos` returns the inserted string id, so the update succeeds at runtime, but the type annotations are inconsistent.

### 3.2 `src/sdk/tools_core/contacts_storage.py` — `str` passed as `row_id: int`
- **Location:** line 199
- **Issue:** `db.update_contact(contact["id"], ...)` passes `contact["id"]` (a `str`, inserted as `str(uuid.uuid4())`) where `update_contact` expects `int`. Like todos, HybridDB returns the inserted string id, so the update succeeds at runtime, but the annotations are inconsistent.

### 3.3 `src/http/routers/email.py` — missing return type annotations
- **Location:** lines 23, 42, 48, 58, 79, 131
- **Issue:** All handlers are untyped. Minor, but inconsistent with the rest of the codebase.

---

## 4. Test / Config Issues

### 4.1 `tests/scripts/test_ws_rounds.py` — skipped by default
- **Location:** line 26
- **Issue:** Added `pytestmark = pytest.mark.skip(...)`. Previously runnable as a pytest; now it skips unless a server is running. This is intentional but removes CI coverage.

### 4.2 `tests/hybriddb/benchmarks/conftest.py` — benchmark hook silently dropped
- **Location:** lines 67–83
- **Issue:** `pytest_benchmark_update_json` is now guarded by a try/except that silently drops the hook if `pytest-benchmark` is not installed. The directory is also excluded via `norecursedirs`, so the file is effectively dead code in normal runs.

---

## 5. Pre-existing Issues (Not Introduced by This Diff)

- `src/sdk/loop.py` — multiple bare `except Exception: pass` blocks that swallow errors silently (lines 379, 432, 502, 558, 618, 918).
- `src/subagent/config.py` — Pydantic class-based `config` deprecation warning shown in tests.
- Widespread mypy `Missing type parameters for generic type` warnings across the SDK and HTTP layers.

---

## 6. Verification Summary

| Check | Result |
|---|---|
| Full pytest suite | 1,193 passed, 16 skipped |
| Integration + smoke unit tests | 47 passed |
| New untracked SDK tests | 28 passed |
| `ruff check src/ tests/` | F821/F841/E702/I001 errors (see above) |
| `mypy src/` | Pre-existing generic-type noise + new `Any`/`add_summary_message` issues |

---

## 7. Recommended Immediate Fixes

| File | Fix |
|---|---|
| `src/http/routers/email.py` | Add `import asyncio` at module top |
| `src/storage/messages.py` | Import `Any`; change `add_summary_message` return type to `str` |
| `src/http/main.py` | Split semicolon import; sort imports |
| `src/skills_seed/skill-creation/eval-viewer/__init__.py` | Delete or replace invalid content |
| `src/sdk/tools_core/email.py` | Remove or use `folder_list` |
| `src/sdk/tools_core/todos_storage.py` | Use `COUNT(*)` for `todos_count` |
| `src/sdk/companion_scheduler.py` | Use `COUNT(*)` for unread emails |
