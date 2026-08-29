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

---

## 2. Phase 1 review residuals (2026-08-29) ⏳ DEFERRED (tracked P2s)

P1-T1..T9 all merged and reviewed. Reviewer-passed commits carried the
following severity-tagged residuals, deliberately deferred:

- **P1 (fixed in-phase):** interview tools unregistered in native_tools.py +
  file-sync name-keyed manifest — fixed in `f7504f1`.
- **P1 (fixed in-phase):** review-queue draft-name traversal — fixed in
  `7413a10` BEFORE the review API landed.
- **P2 — file-sync provider path join:** `files_dir / rf.path` is
  unsanitized; clamp with resolve() containment check before writing
  (attacker-controlled remote paths must not escape Files/). *Sync connectors
  batch.*
- **P2 — live adapter pagination:** Dropbox continuation re-lists root
  (loop risk); Drive folders >100 files silently incomplete. *Sync connectors
  batch.*
- **P2 — interview transcript path:** deviates from plan spec
  (`data/private/interviews/{user_id}/` JSONL) — now at `data_root Interviews/`
  JSON. Align or amend the spec. *Interview iteration.*
- **P2 — interview_ask after completion:** raises IndexError; return a
  typed error shape. *Interview iteration.*
- **P2 — CorpusStore error handling:** `index` swallows delete failures
  (stale rows / overcount on shrunken re-index); `search` masks
  OperationalError as empty miss. *Corpus iteration.*
- **P2 — design extractor:** `approve/reject_skill_draft` now validated
  (`7413a10`); remaining: `.draft-meta.json` pruning on bulk operations.
  *Review queue batch.*
- **Ops cluster (GitHub issues #2/#3/#5/#7):** MCP transparent reconnect,
  `.mcp.json` hash-based index invalidation, hot tool-registry refresh for
  live sessions, `GET /v1/mcp/health`. *Next batch post-Phase-1.*
- **Durable approvals (#6):** per-tool confirmation policies + approval
  receipts; prerequisite for Phase 2 spend-bearing actions. *Phase 2.*
