# Deferred Follow-ups — Audit Bug-Fix Plan

Tracking doc for items deliberately deferred from the 2026-08-23 audit bug-fix plan
(36 tasks merged to main at `c362f2d`). Full per-task history lives in
`.superpowers/sdd/2026-08-23-audit-fixes/progress.md`.

---

## 1. GmailCache `upsert_batch` — batched journaling ⏳ DEFERRED (trigger-based)

**Source:** Task 24 review finding (P2), investigated 2026-08-24.

**Current state:** `GmailCache` (`src/storage/gmail_cache.py`) has **no production
callers** — only its module-level singleton (`get_gmail_cache()`) and
`tests/unit/test_gmail_cache.py`. `upsert_batch()` is unused in `src/`, so the
per-row journaling cost is entirely theoretical today.

**Impact when a consumer lands:** N emails = N SQLite transactions + N
`_process_journal()` passes (each driving Chroma + DuckDB sync). A ~500-email
import would take seconds-to-minutes vs. one batched transaction + a single sync
pass.

**Trigger to re-open:** the moment an email-sync feature starts bulk-importing
into `GmailCache` (e.g. `email_sync` gaining a Gmail path).

**Priority then:** **P1** — do not ship a bulk consumer against per-row journaling.

**Fix sketch (when re-opened):** single `INSERT ... ON CONFLICT(message_id) DO
UPDATE` for the whole batch inside one transaction; write journal rows for all
LONGTEXT columns once; call `_process_journal()` exactly once; keep the existing
unique-index rebuild migration (`_migrate_unique_message_id`) as precondition.

---

## 2. Known pre-existing hang 🐛 (pre-dates the audit branch)

`test_ws_approval_rejects_mismatched_call_id` hangs in **both** copies
(`tests/api/test_ws_protocol.py::TestWebSocketPersistence` and
`tests/api/test_ws_approval.py`). Verified hanging on pre-branch HEAD.
Excluded from gates via `--deselect`; root-causing is open.

## 3. Minor residuals (documented in task reports)

- Approve retry / exception paths don't thread `event.run_id` into fallback
  persists (turn fragmentation only; Task 36a report).
- `_migrate_generated_columns` swallows exceptions (safe: json_extract fallback;
  Task 24 report).
- Generator-finally lock release untested for pre-start WS disconnect (Task 31).
- Task 28 of the plan (optional) was never started.

*Add new deferrals here with: source, current state, impact, trigger to
re-open, priority-at-trigger, fix sketch.*
