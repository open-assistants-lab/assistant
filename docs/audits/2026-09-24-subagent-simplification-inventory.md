# Subagent Simplification Inventory

**Date:** 2026-09-24
**Branch/base:** `feat/subagent-capability-reliability` / `2b185e24`
**Plan:** `docs/superpowers/plans/2026-09-24-subagent-architecture-simplification.md`
**Scope:** read-only architecture and compatibility audit; no runtime implementation in this task.

## Characterization Evidence

- Baseline full suite: `uv run pytest -q` -> **3223 passed, 27 skipped, 51 warnings** in 585.31s.
- Task 1 focused suite after characterization tests: `uv run pytest tests/sdk/test_subagent_launch_preflight.py tests/sdk/test_subagent_completion.py tests/sdk/test_subagent_capabilities.py tests/sdk/test_subagent_v1.py tests/sdk/test_permission_policy.py -q` -> **137 passed**.
- Legacy empty selection: `ToolSelectionMode.LEGACY` with `AgentProfile.tools == []` produces a ready plan with `effective_tools == ()`. The old subagent builder interpreted an empty profile tool list as all registered tools, so this is a compatibility change, not a safe restoration target.
- Runtime `ask`: generic launch preflight checks `skills_load` with `{}`. The permission policy can return `allow` for that call while `skills_load(name="deployment")` returns item-level `ask`. `HITLMiddleware` evaluates actual args, writes a pending proposal, returns a non-error result (`is_error=False` by default), and the child coordinator currently has no terminal signal tied to that proposal. Thus the preflight/runtime distinction is real.
- Completion crash window: the parent consumer can persist a message and then fail before outbox acknowledgement. Replay currently scans up to 100,000 session messages and suppresses the second insert by matching `metadata.subagent_completion` and `task_id`. The characterization test injects this post-append failure and confirms the existing scan deduplicates the retry.

## Persisted State and Authority

| Record/fields | Writers and transitions | Readers/consumers | Current authority / simplification note |
|---|---|---|---|
| `work_queue.id`, `user_id`, `agent_name`, `task`, `parent_id`, `parent_session_id`, `workspace_id`, `created_at`, `updated_at` | `SubagentWorkQueueDB.insert_task`; identity/routing fields copied into completion outbox on terminal transition | `SubagentCoordinator`, `src/sdk/tools_core/subagent.py`, `src/http/routers/subagents.py`, completion replay | Task row is the durable identity source. `workspace_id` is stored as user-level (`"user"`) by the queue even when a coordinator was constructed with a requested workspace; it is not the parent session's execution/notification route. Persist the requested execution workspace in `launch_plan` and use that frozen field for tool scope and completion delivery; keep user-level queue/profile storage unchanged.
| `status` | insert -> `pending`; claim/start -> `running`; cancellation -> `cancelling`/`cancelled`; terminal methods -> `completed`, `failed`, `timed_out`, or `cancelled`; stale recovery -> `failed` or `cancelled` | Coordinator execution/recovery, check/list tools, HTTP job serialization, tests | Authoritative lifecycle state. `TaskStatus` currently has no `blocked`; runtime approval block should use `failed` plus result-level `terminal_reason="blocked"` and a stable error code.
| `config` | `insert_task` freezes an `AgentProfile` JSON snapshot; start/delegate/invoke paths supply the execution profile | Coordinator execution/recovery reconstructs `AgentProfile`; APIs/tools display selected metadata | Runtime profile snapshot. Successful runs copy effective tool/skill names into this profile, so it is not an unchanged copy of the user's definition.
| `launch_plan` | `insert_task` stores `SubagentLaunchPlan.model_dump(mode="json")` | `SubagentCoordinator` restores `effective_tools`; queue completion copies `plan_id` into the result; API/task records can expose it | Intended authoritative launch capability snapshot. It is separate from the `config` profile copy. Plan ID currently hashes profile declarations/mode/identity, not the resolved decisions/effective manifest.
| `result` | `set_completed`, `set_failed`, cancellation and stale-recovery paths serialize `SubagentResult` | Subagent check/task tools, HTTP serialization, coordinator result handling, completion event creation | Output, usage, structured output, and result-level terminal reason belong here. Preserve separation from lifecycle `status`.
| `error`, `terminal_reason`, `error_code` on task row | Failure, timeout, cancellation, recovery paths; completion stores terminal reason in both the task column and `SubagentResult` | API/task serializers, cancellation/recovery logic, tests; code-level field consumers found in coordinator/tools/router | Some overlap with `result.error`, `result.terminal_reason`, and `result.error_code`. Keep lifecycle `status` authoritative. Remove a duplicate only after consumer/API/recovery contracts are pinned.
| `progress`, `instructions`, `cancel_requested` | Progress/context updates, supervisor instruction/cancel tools, deletion and recovery paths | `SubagentContext`, tools, API serializers and coordinator | Live control state. Distinct from immutable launch manifest; no evidence supports collapsing it into result data.
| `claimed_by`, `claimed_at`, `heartbeat_at`, `started_at`, `completed_at` | Claim/start/heartbeat/terminal transitions | Coordinator startup stale-task recovery and tests | Worker lifecycle/recovery metadata. No separate lease/reaper abstraction exists; do not remove as presentation-only fields.
| `completion_events.task_id`, routing fields, `status`, `result`, `error`, `delivered_at`, `attempts`, `created_at` | Inserted in the same work-queue transaction as terminal task transition; acknowledged after all matching bus consumers return successfully; attempt count updated on no subscriber/error | `SubagentCoordinator.drain_completion_events` reconstructs `SubagentCompletion` and publishes it; parent consumer is `RunService.handle_subagent_completion` | Outbox is justified for durable replay. The event duplicates terminal status/result/error and routing data from a task row. No task/outbox deletion or retention method was found; tasks appear retained indefinitely, so later replacement with task-ID/routing event data is feasible if recovery joins the retained task row.

### Consumer findings

- `src/sdk/coordinator.py`: all launch paths (`start`, `delegate`, deprecated `invoke`) preflight; task execution consumes stored effective tools; completion recovery drains the outbox. `SubagentCoordinator.workspace_id` is always the user-level storage scope while `requested_workspace_id` retains the caller workspace. Current preflight passes the former into the launch plan, and `drain_completion_events` routes using the outbox's user-level `workspace_id`, so parent-workspace scope/notification is not preserved. Store the requested workspace in the immutable manifest and use it during execution/replay. Repository production code has no direct caller of `SubagentCoordinator.invoke`; one test only checks a disabled-agent invocation. External callers are unknown, so retain the method for now.
- `src/sdk/tools_core/subagent.py`: creates/updates profiles and serializes progress/result/task records for tools; task-row `status` and parsed `result` are separate outputs.
- `src/http/routers/subagents.py`: `_serialize_job` parses `progress`, `result`, and `instructions`; status and error fields are otherwise passed through. API tests assert pending/running/failed/completed states.
- `src/sdk/run_service.py`: completion callback deduplicates by scanning `get_messages_by_session_id(session_id, limit=100000)`; idle parents receive an assistant note and active parents receive a persisted user-role steer plus a loop steer.
- `src/sdk/subagent_completion.py`: in-process bus is not durable; the SQLite outbox plus startup/explicit drain provides durability.
- `src/sdk/research.py`: uses `delegate`, not deprecated `invoke`.

## Profile Compatibility

- `SubagentCoordinator.load_tool_selection_mode` returns `LEGACY` when `runtime-policy.json` is absent, malformed, or contains an unknown selection. That conflates a true legacy profile with corrupt/unsupported policy data.
- `AgentProfile` parser loads YAML frontmatter into a Pydantic model. The `dumps_profile` serializer uses `exclude_defaults=True`; consequently default/empty `tools` may be omitted on disk even when the in-memory caller supplied `tools=[]`. Old on-disk profiles with no tools field are therefore ambiguous unless raw frontmatter proves the field was explicitly present.
- No deployment data-root/path environment variable is set in this worktree. I did not inspect the default personal `~/Assistant` store. The repository contains three `kits/*/PROFILE.md` files, which are packaged role kits, not evidence of the user's subagent profile corpus. Actual legacy-profile prevalence is therefore unknown.
- Approved migration rule: one-time, per-profile migration. Raw explicit non-empty `tools` -> `ALLOWLIST`; raw explicit `tools: []` -> `NONE`; omitted or ambiguous -> `SAFE_DEFAULT`; emit a diagnostic and persist a versioned sidecar atomically. Never map legacy empty to all native tools. New create/update APIs preserve omitted-vs-empty while the values are still distinguishable in memory.

## SAFE_DEFAULT Breadth and Scope

The current annotation-derived SAFE_DEFAULT includes **23 tools**, totaling **6,075 serialized schema characters** (about **1,519 tokens** using the explicitly approximate 4-characters-per-token estimate):

`app_list`, `app_schema`, `app_summarize`, `browser_screenshot`, `browser_snapshot`, `files_glob_search`, `files_grep_search`, `files_list`, `files_read`, `files_versions_list`, `mcp_list`, `mcp_tools`, `message_count`, `message_history`, `message_search`, `message_timeline`, `research_list`, `research_start`, `search_corpus`, `skills_load`, `time_get`, `tool_result_read`.

The set is broader than “local, workspace-scoped, read-only”:

- Apps, messages, research, corpus, skills, MCP, tool-result, and browser tools expose user-level, cross-session, external, or broader private state rather than only one workspace's local files.
- `research_start` is marked read-only by annotations but starts research work; annotations alone do not establish the product-level default boundary.
- `time_get` is low-risk but not workspace-scoped.
- `files_versions_list` reads the user-level `.versions` root, not only the selected workspace.
- `files_list`/`files_read` accept model-visible `user_id` and `workspace_id`; their filesystem resolver accepts absolute paths inside the user's entire data root, and relative traversal is not consistently confined to the workspace. They are not safe to include as-is.
- `files_glob_search`/`files_grep_search` confine the requested search root but accept model-visible user/workspace identifiers. Search currently follows file symlinks when reading matches; lexical prefix checks do not prevent an in-workspace symlink from exposing an outside target.

**Evidence-based curation direction:** make the base set a small file-navigation/read set (`files_list`, `files_read`, `files_glob_search`, `files_grep_search`) only after subagent tool construction binds `user_id` and the requested `workspace_id` from the coordinator rather than model input, path arguments are confined to that requested workspace, and search rejects symlink targets outside that root. The requested workspace must be frozen in the launch plan and recovered for replay; queue `workspace_id="user"` is only the storage scope. Keep `files_versions_list` out because its version storage is user-level. `skills_load` remains available only when requested skills pass launch preflight and the run-scoped skill allowlist; bind its user/workspace identity too. All broader tools require explicit allowlisting plus the normal capability/governance checks. Task 4/5/6 must test workspace-specific execution and replay.

**Compatibility measurement limitation:** no environment data path is configured and user profiles were not scanned. The repo therefore has no reliable profile-usage distribution for the 23 tools; retain this as residual risk and use diagnostics/migration counts when real profiles are migrated. The exact curated set is the four file tools above, conditional on identity/path/symlink hardening and tests; their effective scope is the launch plan's requested workspace, not the work-queue's user-level storage scope.

## Outbox and Message-Scan Measurement

- `work_queue` task rows and `completion_events` have no delete/purge/retention implementation in the audited code. The outbox can therefore reference a retained task row, but preserve the current event payload until a test proves restart replay can reconstruct every terminal event—including failure, timeout, cancellation, and blocked—from the task row.
- Current consumer idempotency is at-least-once outbox delivery plus a broad conversation-history scan; it is not a cross-database exactly-once transaction.
- A benchmark seeded 100, 1,000, and 10,000 temporary SQLite messages directly, then measured the existing `MessageStore.get_messages_by_session_id` retrieval (including object conversion): **1.4 ms**, **5.1 ms**, and **82.7 ms** respectively in this environment. The first attempt populated through the full CoreMem ingest API and exceeded a 300-second timeout, so it was discarded; direct bulk seeding was used to isolate history-scan cost. The result is linear and manageable at 10,000 messages, but an indexed delivery key is simpler and remains the correct crash-window idempotency contract.

## Approved Decisions and Remaining Scope

1. **Legacy migration — approved:** one-time per profile, explicit empty -> `NONE`, omitted/ambiguous -> `SAFE_DEFAULT`, explicit non-empty -> `ALLOWLIST`; visible diagnostic, no all-native fallback.
2. **Runtime child `ask` — approved:** stop with result reason `blocked` and stable approval-required code; do not create a non-resumable child-owned proposal; leave main-agent HITL unchanged.
3. **Completion replay — approved:** replay every routable terminal outcome (success/failure/timeout/cancellation/blocked) into the parent conversation after restart; tasks with no parent session retain durable status without a message; consumer must be idempotent.
4. **SAFE_DEFAULT — approved direction:** curate a small local workspace-scoped read-only set. Exact set is the four file tools above only after tool-identity/path/symlink scope hardening and tests. Broader tools require explicit allowlists.
5. **`invoke` — unresolved:** no production callers found in this repo, but external callers unknown; retain for this implementation.
6. **Custom/MCP in child manifest — out of scope:** current strict planner uses native tools only; do not add a second tool discovery/authorization path in this simplification.
7. **Outbox/task retention — implementation ruling:** no deletion path exists; retain task rows and preserve snapshots until replay reconstruction tests prove a smaller event row safe. Payload simplification is not required for the first reliable replay change.

## Task 1 Rulings

- The first benchmark method measured CoreMem ingestion rather than history scanning; switched to bulk SQLite fixture seeding. Cost: this omits ingest and index-update overhead, intentionally, because the measured target was the dedupe query.
- SAFE_DEFAULT annotations are not sufficient to determine child scope. The implementation must bind coordinator identity and requested workspace, constrain paths and reject outside-root symlinks before admitting file tools to the curated default.
- The queue's `workspace_id` is a user-level storage key, not a parent-session route. The launch manifest must carry the requested workspace so runtime tool scope and outbox replay use the same immutable route.
- Use `tests/sdk/test_permission_policy.py` as the argument-sensitive governance test module; this branch has no `tests/sdk/test_item_hitl_policy.py`.
