# Subagent Scheduler Unification Design

**Date:** 2026-09-25  
**Status:** Draft for review  
**Issue:** #46

## Goal

Expose the existing APScheduler-based subagent scheduler through a governed API
and tool surface without creating a second subagent execution path or allowing
unattended work to stall on unanswered approvals.

## Current state

`src/subagent/scheduler.py` already provides durable one-shot and recurring
triggers backed by APScheduler and SQLite. However:

- no API or tool creates schedules;
- schedule trigger metadata is not persisted, so restore schedules every row as
  a one-shot job roughly 30 seconds after startup;
- the existing `/subagents/jobs` endpoints expose the work queue, not APScheduler
  schedules;
- scheduled execution calls the legacy `SubagentManager.invoke()` path instead
  of the current `SubagentCoordinator` launch-manifest path;
- scheduled runs have no explicit unattended-approval policy.

## Scope

### In scope

- A distinct scheduled-subagent API and tool surface.
- Durable schedule metadata and trigger restoration.
- Frozen capability manifest validation at schedule creation.
- Safe unattended execution using the current coordinator/work queue.
- Per-user authorization and audit/receipt visibility.
- Cancellation, listing, status, and startup reconciliation.

### Out of scope

- Replacing APScheduler.
- A general-purpose workflow/cron platform.
- Automatically approving newly added tools after a schedule is created.
- The unrelated companion scheduler under `/scheduler/*`.

## API design

Add a separate `/subagents/schedules` resource. Existing `/subagents/jobs`
continues to represent immediate work-queue runs.

### Create

`POST /subagents/schedules`

```json
{
  "subagent_name": "researcher",
  "task": "Review the latest deployment status",
  "workspace_id": "personal",
  "run_at": "2026-09-25T18:00:00Z",
  "cron": null,
  "timezone": "UTC"
}
```

Exactly one of `run_at` or `cron` is required. `cron` is validated before
persistence. The response includes `schedule_id`, `status`, and `manifest_hash`.

### Read

- `GET /subagents/schedules`
- `GET /subagents/schedules/{schedule_id}`
- `GET /subagents/schedules/{schedule_id}/runs`

All reads require the resolved user identity. A schedule ID alone never grants
cross-user access.

### Cancel

- `DELETE /subagents/schedules/{schedule_id}`

Cancellation removes the APScheduler trigger and marks the schedule cancelled.
It does not silently cancel an already-running work-queue task; that operation
continues to use the existing job cancellation path.

## Tool design

Add `subagent_schedule` with:

- `subagent_name`
- `task`
- `workspace_id`
- one of `run_at` or `cron`
- optional `timezone`

The tool is a write and requires approval by default. It returns a schedule ID,
not permission to execute arbitrary future tool calls.

## Durable schedule model

Create a dedicated `subagent_schedules` table rather than overloading
`job_results`:

```text
id
user_id
workspace_id
subagent_name
task
trigger_kind        once | cron
run_at              nullable
cron                nullable
timezone
status              scheduled | running | completed | failed | cancelled | rejected
manifest_json
manifest_hash
created_at
updated_at
last_run_id
last_error
```

Keep schedule definitions separate from execution results. A run references the
schedule ID and writes to the existing subagent work queue/receipts.

## Frozen manifest and unattended policy

At schedule creation:

1. Resolve the subagent profile with `SubagentCoordinator.preflight()`.
2. Reject if the profile is missing, disabled, invalid, or the plan is not ready.
3. Build and persist the complete launch plan, including effective tools,
   skills, workspace, limits, and manifest hash.
4. Resolve every effective tool permission for unattended use.
5. Reject the schedule if any effective tool is `ask` or `deny`.

The first release therefore supports unattended schedules only when all
effective tools are explicitly `allow`. A schedule never creates a pending
approval that nobody can answer. A future explicit attestation mode may be added
as a separate governance feature.

Before each run, re-check that the subagent still exists and that the stored
manifest remains valid. If the profile or required capability has changed, mark
the run rejected/failed with a clear reason; never silently broaden the
manifest.

## Execution path

The APScheduler callback must not call `SubagentManager.invoke()`.

It should:

1. Load the schedule and validate ownership/status.
2. Start the current `SubagentCoordinator.start()`/equivalent launch path with
   the frozen manifest.
3. Return the work-queue job ID as the schedule run ID.
4. Let the existing work queue, completion outbox, and audit/receipt systems
   record execution and terminal outcome.
5. Update the schedule's last run/status without duplicating the result as a
   second execution path.

A schedule may be marked `running` while its work-queue job is active. The
schedule run endpoint joins schedule metadata with the existing job result.

## Trigger restoration

Persist enough trigger metadata to reconstruct the original schedule:

- one-shot jobs restore their original UTC `run_at`;
- recurring jobs restore the original cron expression and timezone;
- expired one-shot jobs are marked missed/expired rather than silently delayed;
- recurring jobs preserve their next-run behavior;
- invalid or deleted definitions are surfaced during startup reconciliation.

Restoration must be idempotent and must not create duplicate APScheduler jobs.

## Lifecycle and safety

- Scheduler startup/shutdown is owned by the HTTP application lifecycle.
- Repeated `get_scheduler()` calls return one process-local scheduler.
- Schedule creation and cancellation require authenticated user identity.
- All schedule/run reads are user-scoped.
- APScheduler job IDs are namespaced by user and schedule ID.
- Duplicate create, restore, and cancellation operations are idempotent.
- Cancellation is race-safe against a trigger firing.
- Scheduler errors are recorded in the schedule row and structured logs.
- A stale or deleted subagent cannot cause repeated silent failures.

## Migration and compatibility

- Existing `schedule_once`, `schedule_recurring`, and legacy `job_results` data
  are read for migration/reporting only; new writes use `subagent_schedules`.
- On startup, one-time migration converts valid legacy rows where possible and
  marks ambiguous rows for manual review rather than guessing trigger type.
- Existing immediate `/subagents/{name}/start` behavior is unchanged.
- The old `/subagents/jobs` response remains work-queue scoped.

## Testing strategy

### Unit tests

- Create one-shot and recurring schedules.
- Reject invalid cron, timezone, missing profile, and missing trigger.
- Reject schedules containing ask/deny effective tools.
- User isolation for create/list/get/cancel.
- Idempotent restore and duplicate APScheduler IDs.
- Restore original trigger metadata.
- Deleted profile/invalid manifest fails clearly.
- Cancellation races with firing.
- Legacy migration marks ambiguous rows safely.

### Integration tests

- Create a schedule through the API, advance/fire it, and observe a real
  work-queue run/receipt.
- Confirm an ask-gated scheduled plan is rejected before persistence.
- Confirm an allow-listed scheduled run uses the current coordinator path.
- Restart the scheduler and verify one-shot/recurring restoration.
- Confirm schedule and work-queue job IDs/results are correlated.

## Rollout

1. Add migration/schema and read-only schedule APIs.
2. Add coordinator-backed firing behind an explicit feature flag.
3. Add `subagent_schedule` after API behavior is verified.
4. Migrate legacy rows and publish release notes.
5. Keep the feature disabled by default until unattended policy is enabled.

## Acceptance criteria

- A user can create, list, inspect, and cancel a schedule through the API.
- A permitted schedule creates a governed work-queue run and receipt.
- A schedule requiring approval is rejected before it is persisted.
- Restart preserves exact one-shot and recurring trigger semantics.
- No scheduled execution uses the legacy `SubagentManager.invoke()` path.
- Cross-user schedule access is impossible.
- Existing immediate subagent jobs remain backward compatible.
- The `/scheduler/*` companion API remains separate.
