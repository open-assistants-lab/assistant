# Subagent Scheduler Unification Implementation Plan (#46)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing APScheduler subagent scheduler reachable through a governed API and tool surface, executing every fire through the current `SubagentCoordinator` frozen-manifest path with durable trigger restoration and user isolation.

**Architecture:** Add a dedicated `subagent_schedules` store (definitions, separate from execution results) beside APScheduler's jobstore. A schedule is created only after `SubagentCoordinator.preflight()` produces a ready frozen launch plan; that plan is persisted and re-verified at fire time. Swapping `BackgroundScheduler` → `AsyncIOScheduler` lets the trigger callback await `coordinator.start_with_plan()` instead of the legacy `SubagentManager.invoke()`.

**Tech Stack:** Python 3.11–3.13, FastAPI, APScheduler 3.11 (`AsyncIOScheduler`), aiosqlite, Pydantic v2, pytest, Ruff, mypy.

**Design spec:** `docs/superpowers/specs/2026-09-25-subagent-scheduler-design.md`
**Issue:** #46

---

## Key finding that shapes this plan

`build_launch_plan()` in `src/sdk/subagent_capabilities.py` already resolves
`permission_ask` / `permission_deny` for every requested tool and skill, and
sets `plan.ready = all(decision.accepted)`. **A ready plan therefore already
means "every effective capability resolves to `allow`".**

So the design spec's "allow-only unattended" policy is *not* a new governance
mode — it is exactly the existing launch preflight. The plan below therefore
requires `plan.ready` at schedule creation and re-verifies the frozen plan at
fire time. No attestation mode, no new permission tier, no pending-approval
stall. This removes the largest chunk of the original design's risk.

## Global constraints

- No scheduled run may call `SubagentManager.invoke()`; every fire goes through
  `SubagentCoordinator` and the existing work queue, receipts, and completion
  outbox.
- A schedule is only created from a **ready** frozen plan; rejected plans are
  never persisted.
- A fire-time manifest mismatch fails the run with a distinct reason; it never
  silently broadens or narrows the frozen manifest.
- APScheduler's `jobs.db` schema stays owned by APScheduler — schedule
  definitions live in a separate database.
- All schedule reads and writes are user-scoped; a schedule ID alone grants
  nothing.
- `/subagents/jobs` keeps its work-queue meaning; the new resource is
  `/subagents/schedules`.
- The companion `/scheduler/*` API stays separate and untouched.
- TDD and full-suite verification are required before merge.

## File map

**New**
- `src/subagent/schedules_store.py` — `SubagentScheduleStore` (aiosqlite CRUD)
- `src/http/routers/subagent_schedules.py` — `/subagents/schedules` routes
- `tests/sdk/test_subagent_schedules_store.py`
- `tests/sdk/test_subagent_scheduled_firing.py`
- `tests/api/test_subagent_schedules.py`
- `tests/sdk/test_subagent_schedule_tool.py`
- `tests/sdk/test_subagent_scheduler_restore.py`

**Modified**
- `src/storage/paths.py` — `subagent_schedules_db_path()`
- `src/subagent/scheduler.py` — `AsyncIOScheduler`, coordinator-backed firing,
  trigger-metadata restore
- `src/sdk/coordinator.py` — `start_with_plan()`
- `src/sdk/tools_core/subagent.py` — `subagent_schedule` tool
- `src/sdk/native_tools.py` — register the tool
- `src/http/routers/__init__.py`, `src/http/main.py` — route mount + shutdown
- `src/config/settings.py` — new top-level `SchedulingConfig` (`AppConfig` has
  no `subagent` field today, and `config.yaml`'s `subagent: null` is inert, so a
  new section is cleaner than nesting under it)
- `config.yaml` — `scheduling.subagent_enabled`

---

## Task 1: Durable schedule store

**Files:** `src/storage/paths.py`, `src/subagent/schedules_store.py`, `tests/sdk/test_subagent_schedules_store.py`

**Interfaces:**
- `DataPaths.subagent_schedules_db_path() -> Path` (`data/subagent_schedules.db`)
- `SubagentScheduleStore.create(...) / get(...) / list_for_user(...) / update_status(...) / cancel(...) / due_for_restore()`

- [ ] **Step 1: Write failing store tests.**

Cover: create+round-trip of a frozen manifest, list isolation between two users,
`get` for a foreign user returns `None`, status transitions, cancel idempotency,
and corrupt/missing DB recovering to an empty store.

- [ ] **Step 2: Run red.**

```bash
uv run pytest -q tests/sdk/test_subagent_schedules_store.py
```

- [ ] **Step 3: Implement the schema.**

Exactly the design spec's columns: `id, user_id, workspace_id, subagent_name,
task, trigger_kind, run_at, cron, timezone, status, manifest_json,
manifest_hash, created_at, updated_at, last_run_id, last_error`. Use
`aiosqlite` to match `src/sdk/work_queue.py`. Validate `trigger_kind` and the
`status` vocabulary in Python, not just in the schema.

- [ ] **Step 4: Implement CRUD with user scoping baked into every query.**

Never write a `get`/`list` that takes only an ID.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_subagent_schedules_store.py
uv run ruff check src/subagent/schedules_store.py src/storage/paths.py tests/sdk/test_subagent_schedules_store.py
uv run mypy src/subagent/schedules_store.py
git add src/subagent/schedules_store.py src/storage/paths.py tests/sdk/test_subagent_schedules_store.py
git commit -m "feat: add durable subagent schedule store"
```

---

## Task 2: Coordinator frozen-manifest launch entry point

**Files:** `src/sdk/coordinator.py`, `tests/sdk/test_subagent_scheduled_firing.py`

**Interfaces:**
- `SubagentCoordinator.start_with_plan(agent_name, task, plan, *, parent_id=None) -> str`
- `SubagentLaunchPlan.from_persisted(dict) -> SubagentLaunchPlan`

- [ ] **Step 1: Write failing tests.**

- A ready plan launches and returns a work-queue task ID.
- A plan that is not `ready` raises `SubagentLaunchRejected` **before** any
  queue insert.
- A frozen plan whose `effective_tools` differ from a fresh preflight is
  rejected with a `manifest_drift` reason and no queue insert.
- A frozen plan for a deleted subagent is rejected cleanly.
- The persisted task row carries the frozen canonical manifest.

- [ ] **Step 2: Run red.**

```bash
uv run pytest -q tests/sdk/test_subagent_scheduled_firing.py
```

- [ ] **Step 3: Implement `start_with_plan()`.**

Reuse the existing tail of `start()` (validate → `insert_task(launch_plan=…)` →
`_run_job`). Do not duplicate `_run_job`. Revalidation is: profile still
exists, fresh `preflight()` is `ready`, and `effective_tools`/`effective_skills`
exactly match the frozen plan. On mismatch, raise a distinct error whose code is
`manifest_drift` so callers can report it precisely.

- [ ] **Step 4: Implement `from_persisted()`.**

Reconstruct a `SubagentLaunchPlan` from `to_persisted_dict()` output, then
assert the recomputed `_plan_id(canonical_manifest_json)` equals the stored
`plan_id` — a tampered row fails closed.

- [ ] **Step 5: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_subagent_scheduled_firing.py tests/sdk/test_subagent_v1.py tests/sdk/test_subagent_capabilities.py
uv run ruff check src/sdk/coordinator.py
uv run mypy src/sdk/coordinator.py
git add src/sdk/coordinator.py tests/sdk/test_subagent_scheduled_firing.py
git commit -m "feat: launch subagents from a frozen manifest"
```

---

## Task 3: Async scheduler with durable trigger restoration

**Files:** `src/subagent/scheduler.py`, `tests/sdk/test_subagent_scheduler_restore.py`

**Interfaces:**
- `get_scheduler() -> AsyncIOScheduler` (created only inside a running loop)
- `async def shutdown_scheduler() -> None`
- `create_schedule(...) -> dict` / `cancel_schedule(user_id, schedule_id) -> bool`
- `list_schedules(user_id) -> list[dict]` / `get_schedule(user_id, schedule_id) -> dict | None`
- `_restore_schedules()` — reconstructs real triggers from persisted metadata

- [ ] **Step 1: Write failing tests.**

- One-shot schedule restores to its **original** `run_at`, not "now + 30s".
- Recurring schedule restores its cron expression **and timezone**.
- A one-shot whose `run_at` is in the past restores as `expired`, not delayed.
- Restore is idempotent: two calls produce one APScheduler job per schedule.
- A schedule whose subagent was deleted is surfaced as `invalid` at startup, not
  silently no-op on every fire.
- `get_scheduler()` outside a running loop raises a clear error.

- [ ] **Step 2: Run red.**

```bash
uv run pytest -q tests/sdk/test_subagent_scheduler_restore.py
```

- [ ] **Step 3: Switch to `AsyncIOScheduler`.**

Construct and `start()` it inside the running loop (the FastAPI lifespan
already provides one). Keep the `SQLAlchemyJobStore` on `jobs.db` unchanged.
Add an explicit loop guard so a future non-async caller gets a real error rather
than a silently broken scheduler.

- [ ] **Step 4: Persist trigger metadata and restore faithfully.**

Write `trigger_kind` / `run_at` / `cron` / `timezone` at creation, and build
`DateTrigger` / `CronTrigger(timezone=…)` from those columns on restore.
Namespace APScheduler job IDs as `sched:{schedule_id}`.

- [ ] **Step 5: Replace the firing callback.**

`_fire_schedule(schedule_id)` must: load the schedule (user-scoped), refuse
non-`scheduled`/cancelled rows, re-verify the frozen plan via Task 2,
`await coordinator.start_with_plan(...)`, store the returned work-queue task ID
as `last_run_id`, and set status `running`. Delete the `SubagentManager.invoke()`
path. Failures mark the row `failed` with `last_error` and never raise into
APScheduler's thread.

- [ ] **Step 6: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_subagent_scheduler_restore.py tests/sdk/test_subagent_scheduled_firing.py
uv run ruff check src/subagent/scheduler.py
uv run mypy src/subagent/scheduler.py
git add src/subagent/scheduler.py tests/sdk/test_subagent_scheduler_restore.py
git commit -m "feat: fire subagent schedules through the governed coordinator"
```

---

## Task 4: Governed HTTP API

**Files:** `src/http/routers/subagent_schedules.py`, `src/http/routers/__init__.py`, `src/http/main.py`, `src/config/settings.py`, `config.yaml`, `tests/api/test_subagent_schedules.py`

**Interfaces:**
- `POST /subagents/schedules` → `{schedule_id, status, manifest_hash}`
- `GET /subagents/schedules`, `GET /subagents/schedules/{id}`, `GET /subagents/schedules/{id}/runs`, `DELETE /subagents/schedules/{id}`

- [ ] **Step 1: Write failing API tests.**

- Create one-shot and recurring; reject when both or neither of `run_at`/`cron`
  are supplied.
- Reject invalid cron and unknown timezone **before** persistence.
- Reject when the subagent is missing, disabled, or the plan is not ready —
  assert nothing was written to the store.
- Reject when any effective tool resolves to `ask`/`deny`, naming the tool.
- Cross-user read/cancel returns 404, not 403 (no existence leak).
- `/subagents/jobs` is unchanged and still work-queue scoped.
- `DELETE` is idempotent and race-safe against a firing trigger.

- [ ] **Step 2: Run red.**

```bash
uv run pytest -q tests/api/test_subagent_schedules.py
```

- [ ] **Step 3: Implement the router.**

Mount under the existing `/subagents` prefix. Use `resolve_user_id` exactly as
the sibling routes do. Add `SchedulingConfig(subagent_enabled: bool = False)`
(env `SCHEDULING_SUBAGENT_ENABLED`) and enforce it so the surface is dark until
a deployment opts in.

- [ ] **Step 4: Verify and commit.**

```bash
uv run pytest -q tests/api/test_subagent_schedules.py tests/api/test_subagents.py tests/config/test_settings_resolution.py
uv run ruff check src/http/routers/subagent_schedules.py src/http/main.py src/config/settings.py
uv run mypy src/http/routers/subagent_schedules.py src/config/settings.py
git add src/http/routers/subagent_schedules.py src/http/routers/__init__.py src/http/main.py src/config/settings.py config.yaml tests/api/test_subagent_schedules.py
git commit -m "feat: expose governed subagent schedules over HTTP"
```

---

## Task 5: `subagent_schedule` tool

**Files:** `src/sdk/tools_core/subagent.py`, `src/sdk/native_tools.py`, `tests/sdk/test_subagent_schedule_tool.py`

**Interfaces:** `subagent_schedule(subagent_name, task, workspace_id, run_at, cron, timezone)`

- [ ] **Step 1: Write failing tool tests.**

The tool returns a schedule ID and **not** permission to execute; a not-ready
plan returns `is_error=True` with the rejected capability names; a denied
create surfaces as a governance failure rather than a partial schedule.

- [ ] **Step 2: Run red.**

```bash
uv run pytest -q tests/sdk/test_subagent_schedule_tool.py
```

- [ ] **Step 3: Implement and register.**

Mark the tool `destructive=True` (creating unattended future work is a write)
so it is approval-gated like any other write. It must reuse the same service
function as the HTTP route — no duplicated validation logic.

- [ ] **Step 4: Verify and commit.**

```bash
uv run pytest -q tests/sdk/test_subagent_schedule_tool.py tests/sdk/test_tool_schema_budget.py
uv run ruff check src/sdk/tools_core/subagent.py src/sdk/native_tools.py
git add src/sdk/tools_core/subagent.py src/sdk/native_tools.py
git commit -m "feat: add subagent_schedule tool"
```

---

## Task 6: Lifecycle, legacy migration, and full verification

**Files:** `src/http/main.py`, `src/subagent/scheduler.py`, `tests/api/test_subagent_schedules.py`, `tests/sdk/test_subagent_scheduler_restore.py`

- [ ] **Step 1: Add an app-lifecycle test.**

Startup restores schedules idempotently; shutdown awaits the scheduler. Assert
the companion `/scheduler/*` routes remain absent/unchanged.

- [ ] **Step 2: Add legacy migration.**

Convert valid `job_results` rows with `status='scheduled'` into
`subagent_schedules` rows, marking rows whose trigger type cannot be determined
as `needs_review` instead of guessing. Make it run once and be re-entrant.

- [ ] **Step 3: Full verification.**

```bash
uv run pytest -q
uv run ruff check src/
uv run mypy src/subagent/scheduler.py src/subagent/schedules_store.py \
  src/http/routers/subagent_schedules.py src/sdk/coordinator.py
```

- [ ] **Step 4: Close #46 with evidence.**

Comment on the issue with the API shape, the allow-only unattended rule, the
rejection reasons, and the full-suite counts. Note explicitly that no scheduled
path uses `SubagentManager.invoke()`.

## Acceptance criteria

- A user can create, list, inspect, and cancel a schedule over HTTP; the tool
  surface matches.
- A permitted schedule produces a governed work-queue run with a receipt.
- A schedule whose plan is not ready is rejected **before** persistence, naming
  the `ask`/`deny` capability.
- Restart preserves exact one-shot and recurring trigger semantics; expired
  one-shots do not silently run late.
- No scheduled execution touches `SubagentManager.invoke()`.
- Cross-user access is impossible.
- `/subagents/jobs` and `/scheduler/*` keep their current meaning.

## Non-goals for this change

- Replacing APScheduler or building a general workflow/cron platform.
- An attestation mode for unattended gated tools (a separate governance
  feature; v1 is allow-only).
- Automatically re-authorizing a schedule after a profile edit — drift is
  rejected and the operator recreates the schedule.
