# Native Chat Polish and Rubric Design

**Status:** Approved product design; technical contracts revised after self-review

## Purpose

Polish the Native SDK chat interface and expose reasoning, context occupancy, run metadata,
rubric evaluation, and global rubric settings without turning the chat into a dashboard. The
composer becomes the compact control surface. Operational details remain attached to the run
that produced them.

This design also corrects backend flaws that would otherwise make the UI inaccurate: rubric
retries are not consistently bounded across transports, context occupancy does not reflect
summarized model input, rubric events are buffered, and streamed run metadata is not persisted.

This is an umbrella design delivered through gated implementation phases. Backend contracts,
orchestration, and persistence must land before Native state integration; rendering and motion land
only after state transitions are deterministic.

## Scope

The implementation includes:

1. Visual hierarchy, interaction states, and restrained motion.
2. Expanded reasoning in the transcript.
3. Live rubric-check status and criteria.
4. Context occupancy in the composer.
5. Per-run model, usage, context, and rubric metadata.
6. Global-per-user rubric configuration and editable grader guidance.
7. Correct, bounded rubric revision behavior for REST, SSE, and WebSocket runs.

The implementation does not include:

- Per-session rubric enablement.
- User-facing grader tools.
- A grader tool-execution loop.
- Trigger controls in the frontend.
- A general redesign of Tools, Skills, or Subagents.
- Animated transcript reflow or scroll position.

## Superseded Requirements

For this feature area, this specification supersedes earlier requirements that:

- Collapse reasoning after stream completion in
  `2026-07-17-bubble-streaming-indicators-design.md` and
  `2026-07-19-chat-ux-completeness-design.md`.
- Route rubric revisions recursively through TriggerRegistry in
  `2026-07-27-loop-engineering-design.md`.
- Expose grader tools before a bounded grader tool executor exists.

External cron, webhook, file-change, and manual trigger behavior is not superseded.

## Terminology

- **Global rubric setting:** applies to every chat owned by one user. It is not process-global
  across different users.
- **Grader system prompt:** protected grader protocol and security instructions. It is not shown
  in normal Settings.
- **Grader prompt:** user-editable criteria and grading guidance stored in `grader_prompt.md`.
  Its content is treated as untrusted rubric input, not as system-level instructions.
- **Context occupancy:** estimated tokens in the actual prepared agent context divided by the
  selected model's registered context limit. It is distinct from billable provider usage.
- **Run usage:** aggregate provider usage for one user request, separated into agent, grader, and
  summarizer categories. Every category is present and carries an availability flag.
- **Model ID:** the existing canonical wire and storage form `provider:model`. The Native UI renders
  that ID as `provider/model`; the slash form is display text only and is never sent to model
  resolution, cache keys, or persistence.

## Delivery Decomposition

Implement the design as independently verified phases:

1. Freeze typed settings, run-result, event, metadata, and history contracts.
2. Add canonical user settings and grader-prompt storage with migration and revisioning.
3. Add session-scoped summarization telemetry and prepared-context measurement.
4. Replace recursive rubric reruns with bounded run orchestration and immutable outcomes.
5. Centralize durable run persistence and complete-turn history retrieval.
6. Project the runner through REST, SSE, and WebSocket with transport contract tests.
7. Migrate Native state to turns, runs, attempts, and correlated request generations.
8. Add Native rendering, settings, intrinsic composer layout, and accessibility.
9. Add motion and update visual baselines.

Each phase has its own implementation plan and cannot depend on unmerged contracts from a later
phase. The frontend may use contract fixtures before the transport phase, but it does not integrate
against provisional payloads.

## Visual Direction

### Overall layout

Keep the existing two-pane desktop layout and compact control-plane character. Do not add a
persistent dashboard header or right-hand inspector.

The sidebar remains unlabeled. Tools, Skills, Subagents, and Settings form one continuous bottom
column with identical row heights, alignment, hit targets, hover states, pressed states, focus
states, and selected states. There is no divider between those four rows. The theme control stays
on the Settings row. Chat history consumes the flexible space above.

Starter actions become compact outlined pills with visible hover and pressed feedback. User
messages use a slightly stronger surface than assistant responses. Assistant answers remain the
strongest content within assistant turns; reasoning and tool activity use quieter surfaces.

### Motion

Use quiet-precision motion:

- Hover and press feedback: 120 ms.
- Message and status entrance: 160 ms fade with at most 4 px upward translation.
- Panel transition: 180 ms crossfade with at most 8 px translation.
- Expandable rubric details: 180 ms opacity/translation transition without animated height.
- Theme changes snap atomically; do not animate the entire token tree.
- Context compression: a softly flowing rail, only while an explicit compression event is active.

Do not use bounce or spring motion. Do not animate transcript height, virtual-list geometry, or
scroll position. Under reduced motion, remove translation and continuous movement; state changes
snap or use opacity only.

Native SDK transitions must use keyed opacity and transform updates. The design does not assume a
general animated-height primitive.

## Composer Control Surface

The composer must use intrinsic content height rather than a calculated fixed card height. This
change precedes adding status controls so the textarea and bottom action row cannot clip below the
window.

The lower action row contains:

- A searchable model selector displaying `provider/model` while retaining `provider:model` as its
  underlying model ID.
- A passive global `Rubric on`, `Rubric off`, or `Rubric unavailable` status.
- A context status affordance.
- The Send or Stop control.

The model selection is snapshotted into the active run at send time. Changing the selector after a
run starts cannot alter the active run. Remove blind model cycling and the duplicate hosted-model
control.

The rubric status is read-only in the composer. Activating it opens the global rubric settings. It
must report effective state: enabled plus a valid non-empty grader prompt. It must not infer state
from the configured boolean alone.

### Context rail and Send halo

A 2-3 px rail follows the bottom edge of the composer:

- Below 70%: quiet teal with no numeric label.
- From 70% through 80%: clearer teal, still without a Send halo.
- Above 80%: show the percentage beside Send and add a partial amber halo around Send.
- Unknown model limit: hide the rail and expose `Context limit unavailable` through status text.
- Stale data: retain the last value, mark it stale in the details, and do not animate it.

The Send halo is an escalation signal, not an always-visible meter. Color is never the only status
signal. The percentage and accessibility status communicate the warning textually.

During explicit context compression, the rail flows gently and the status reads `Compressing
context...`. Send may be visually subdued but must only be disabled if the backend cannot safely
accept another request.

Activating the rail, percentage, or context status opens a compact details surface containing:

- Estimated prepared-context tokens and model limit.
- Context percentage.
- Whether the value is live or stale.
- Summarization status and last compression result.
- Aggregate run usage in a separate section so usage is not confused with context occupancy.

## Transcript Model

The frontend models the transcript as turns. A turn contains ordered reasoning, tool, answer, and
rubric blocks and is keyed by a backend `run_id`. Persisted message IDs key individual blocks.
Legacy history without run IDs falls back to existing role-order grouping.

### Reasoning

Reasoning remains expanded during and after streaming. Remove the completion-time collapse. It is
rendered above its answer with a muted `Reasoning` label and surface. The full content participates
in virtual-list measurement and extent estimation. No fixed 48 px estimate may be used for
expanded reasoning.

### Rubric check

Rubric status is attached to the answer from the same `run_id`. The status sequence is:

- `Checking rubric...`
- `Rubric passed - N/N criteria`
- `Rubric needs revision - N/N criteria`
- `Revision A of B...`
- `Rubric incomplete after B attempts`
- `Rubric check failed`

Activating the completed row reveals the grader explanation and each criterion. Failing criteria
show their gap. Passed status uses muted green, revision uses amber, and grader errors use
restrained red. The feature is never labeled `Verified`, because it is a rubric evaluation rather
than guaranteed factual verification.

When revision is required, `response_revision_start` identifies the run and new attempt. The
frontend clears and replaces the active answer block for that run rather than appending duplicate
discarded answers. Reasoning and tool activity remain visible, expanded, and labeled by attempt so
the operational audit trail is not lost. Only the final assistant answer is persisted; attempt-
scoped reasoning and tool records retain `run_id` and attempt metadata.

## Backend Architecture

### Effective user settings

Introduce one typed effective-settings resolver used by the Settings API, model resolution,
context reporting, and loop construction. Resolution order is:

1. Explicit request model override on REST message, SSE message-stream, and WebSocket send.
2. Saved per-user override.
3. Environment/config default.

Missing saved fields inherit defaults. Explicit `false`, empty lists, and numeric values remain
valid overrides and must not be lost through truthiness checks.

Add `DataPaths.user_settings_path()` as `<user_dir>/settings.json` and
`DataPaths.user_grader_prompt_path()` as `<user_dir>/grader_prompt.md`. Both paths use the existing
validated `DataPaths.user_id`. Serialize writes per user and use atomic temporary-file replacement.
On first access, merge the legacy `data/users/{user_id}/settings.json` into the canonical document,
prefer canonical values on conflict, rename the legacy file with a `.migrated` suffix, and record
`schema_version: 1`. A malformed legacy file is left in place and reported without replacing a
valid canonical document.

The current authentication dependency authenticates a shared API key but does not establish a user
principal. This design guarantees path validation in solo mode; it does not claim tenant isolation.
Before these endpoints are enabled in team mode, user identity must come from authenticated claims
and caller-supplied user IDs must be ignored. Team-mode identity work is a separate security change.

Global rubric settings contain:

- `enabled`.
- `grader_model` as a canonical `provider:model` model ID; Settings displays it as
  `provider/model`.
- `max_attempts`, constrained to 1-3.

The effective resolver accepts the existing `max_iterations` config/environment field as a
backward-compatible alias for `max_attempts`. Existing deployments do not need an immediate config
migration; new user settings and UI copy use the unambiguous `max_attempts` name.

Existing request-level `verification.rubric` remains supported for API compatibility and overrides
the user grader prompt for that run only. The Native client does not expose this override. The
legacy configured `default_rubric` is used only when no user grader prompt or seed can be loaded and
is deprecated. Configured `grader_tools` are ignored with a warning until a bounded read-only grader
tool executor exists.

A request-level rubric changes criteria but does not enable a globally disabled rubric check. When
effective state is `off`, the override is ignored and terminal rubric status is `not_run`.

Do not expose grader tools in this version. Existing operator-only grader system-prompt
configuration is not exposed through user settings.

Settings changes increment a user settings revision. The loop cache key includes that revision, and
the save path removes older-revision entries under the loop cache lock. In-flight runs continue
using the immutable settings snapshot captured at run start; subsequent runs use the new revision.

`GET /settings` retains provider status and returns these additional fields:

```json
{
  "schema_version": 1,
  "revision": 7,
  "saved": {
    "default_model": "anthropic:claude-sonnet-4",
    "verification": {
      "enabled": true,
      "grader_model": null,
      "max_attempts": 3
    }
  },
  "effective": {
    "default_model": "anthropic:claude-sonnet-4",
    "verification": {
      "state": "on",
      "unavailable_reason": null,
      "grader_model": "ollama-cloud:deepseek-v4-flash",
      "max_attempts": 3,
      "grader_prompt_hash": "sha256:..."
    }
  },
  "provider_status": {
    "anthropic": {"name": "Anthropic", "has_key": true, "key_source": "env"}
  }
}
```

`PATCH /settings` accepts `expected_revision` plus optional saved fields. Omitted fields remain
unchanged; JSON `null` removes a saved override and restores inheritance; explicit false and numeric
values override defaults. Invalid models or attempt ranges return `422`. A stale revision returns
`409 revision_conflict` with the latest effective document. Success returns the complete updated GET
shape.

### Grader prompt storage

`seeds/prompts/grader_prompt.md` is immutable application seed content. On first access, copy it to
the canonical user prompt directory. The user copy contains rubric criteria and optional grading
guidance only.

Provide:

- `GET /user/grader-prompt` returning `content`, `source`, `content_hash`, and the shared settings
  `revision`.
- `PUT /user/grader-prompt` accepting `content` and `expected_revision`.
- `POST /user/grader-prompt/reset` accepting `expected_revision` and restoring the seed.

An empty update returns a validation error rather than silently disabling rubric checking. If the
user copy is missing, reseed it. If both the user copy and seed are unavailable, effective rubric
state is unavailable: Settings returns reason `missing_prompt`, prompt retrieval returns a
configuration error, and ordinary chat continues with terminal rubric status `not_run`.

Prompt update and reset increment the same user settings revision and invalidate cached loops under
the same lock. The immutable run snapshot captures both the revision and prompt hash. Revision
conflicts return `409` as above. The Settings UI saves prompt changes first, then submits settings
with the returned revision; if the second request fails, it reports that the prompt was saved but
the settings were not.

The protected grader system prompt remains separate from this file and continues to define the
JSON schema, delimiter handling, result semantics, and transcript security rules. User grader
content is inserted only within the rubric delimiters.

### Bounded rubric orchestration

Replace recursive rerun-trigger grading with one bounded runner-level orchestration used by REST,
SSE, and WebSocket.

A shared `RunService` owns user-message persistence, history loading, settings snapshotting, runner
execution, final persistence, and terminal outcome construction. Routers and transport adapters do
not write conversation records. A `SessionWorkerRegistry` owns one asynchronous execution worker
per active session. Each session worker acquires its session lock before allocating `run_id`,
persisting the user message, loading history, or capturing settings. A second run for the same
session returns `409 session_busy`; it is not queued. Workers for different sessions execute
concurrently, so one long-running chat never blocks another chat. Stop addresses only the active
run in its owning session. The registry lives in one API server process; distributed deployment
across multiple server processes is outside this desktop-app scope.

`max_attempts` means the maximum number of graded assistant responses, including the initial
response. For each attempt:

1. Run the agent using the immutable run snapshot.
2. Emit `rubric_evaluation_start` immediately before awaiting the grader.
3. Grade the response against the user grader prompt.
4. Emit `rubric_evaluation_end` immediately after parsing the result.
5. Return on `satisfied`, `invalid_rubric`, or `grader_error`.
6. On `needs_revision`, stop with `max_attempts_reached` when no attempt remains.
7. Otherwise append grader feedback as a user message with source `rubric_middleware`, emit
   `response_revision_start`, and repeat inside the same orchestration.

Attempts are one-based. Each response attempt receives one `attempt` value and each grader call
receives a unique `grading_run_id`. Within a revision cycle, the next attempt's model context retains
the prior attempt's answer, tool results, and synthetic rubric feedback because that feedback refers
to the attempted work. These records are marked audit-only when persisted and are excluded from
future user-turn model context after the run terminates.

Cancellation before an answer completes produces terminal run status `cancelled` and no assistant
answer. Cancellation while a completed answer is being graded preserves that answer with rubric
status `cancelled`. Provider timeout or agent failure produces terminal run status `failed`. Grader
timeout and malformed grader output preserve the answer with rubric status `grader_error`.

Do not invoke the general TriggerRegistry for rubric retries. General external triggers remain
unchanged.

The orchestration returns an immutable `RunResult` rather than requiring routers to read mutable
state back from a cached loop. Same-session runs are serialized. `RunResult` includes:

- Final messages and final assistant answer.
- `run_id` and final attempt.
- Resolved canonical `provider:model` model ID.
- Aggregate agent usage.
- Aggregate grader usage with an explicit availability flag.
- Terminal rubric outcome and evaluation history.
- Last prepared-call context and projected next-turn context.

The `RunService` transactionally persists the final run records before exposing terminal success.
REST returns the durable outcome directly. SSE and WebSocket include the same durable outcome in
the canonical `done` event. Persistence failure emits/returns terminal error code
`persistence_failed`; no successful `done` is sent, and the Native client marks provisional content
as unsaved rather than immediately reconciling history.

### Live events

Introduce a typed `RunEvent` envelope above provider-level `StreamChunk`. `StreamChunk` remains the
low-level SDK/provider representation; `RunService` wraps it with run identity and ordering.

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "sequence": 12,
  "type": "rubric_evaluation_start",
  "timestamp": "2026-08-01T12:00:00Z",
  "session_id": "session-1",
  "run_id": "run-1",
  "attempt": 1,
  "data": {"grading_run_id": "grade-1", "max_attempts": 3}
}
```

`sequence` starts at 1 and increases monotonically within one run. `attempt` is one-based. SSE uses
`type` as the SSE event name and the full envelope as data. WebSocket sends the full envelope as one
JSON message. Clients ignore duplicate `event_id` values and any event with a sequence lower than
the highest applied sequence. A forward sequence gap marks the active run desynchronized; the
client keeps rendering subsequent events but reconciles from complete-turn history after durable
`done`.

SSE and WebSocket project the same canonical events:

- Existing text, reasoning, and tool events with `run_id` and attempt.
- `rubric_evaluation_start` with `run_id`, `grading_run_id`, attempt, and `max_attempts`.
- `rubric_evaluation_end` with those identifiers, result, explanation, and criteria.
- `response_revision_start` with `run_id`, previous attempt, and new attempt.
- `context_snapshot` with session, run, model, estimated prepared tokens, context limit,
  percentage, and freshness.
- `context_compressed` with session, run, model, before/after estimates, and compression status.
- `done` with the durable `RunResult` in `data.result`.
- `error` with `code`, `message`, `retryable`, and optional provisional run metadata.

Normative `data` requirements are:

| Event | Required data |
|---|---|
| `text_start` | `block_id` |
| `text_delta` | `block_id`, `delta` |
| `text_end` | `block_id` |
| `reasoning_start` | `block_id` |
| `reasoning_delta` | `block_id`, `delta` |
| `reasoning_end` | `block_id` |
| `tool_input_start` | `block_id`, `tool_call_id`, `name` |
| `tool_input_delta` | `block_id`, `tool_call_id`, `delta` |
| `tool_input_end` | `block_id`, `tool_call_id`, `arguments` |
| `tool_result` | `block_id`, `tool_call_id`, `name`, `status`, `content` |
| `usage` | `category`, `model`, `llm_call_index`, single-call `usage` |
| `rubric_evaluation_start` | `grading_run_id`, `max_attempts` |
| `rubric_evaluation_end` | complete evaluation object, `max_attempts` |
| `response_revision_start` | `previous_attempt`, `new_attempt`, `max_attempts` |
| `context_snapshot` | complete context snapshot |
| `context_compressed` | `before`, `after`, `status`, optional `error` |
| `done` | durable `result` |
| `error` | `code`, `message`, `retryable`, optional `result` |

Block IDs are opaque strings unique within a run. Event timestamps use UTC ISO 8601. Unknown event
types are ignored and logged; malformed known events mark the run desynchronized and are not
partially applied.

Rubric start events cannot be buffered until grading completes. Event IDs and attempt numbers let
the frontend update the correct turn after chat switches or delayed delivery.

REST is not a live-event transport. It returns the terminal `RunResult` and is tested for outcome
parity with streaming transports. Event-order parity applies only to SSE, WebSocket, and an internal
event collector used by runner tests. SSE and WebSocket expose only the versioned canonical
`RunEvent` envelopes. Remove old wire names such as `messages`, `ai_token`, `tool_start`,
`tool_end`, and bare `reasoning` atomically when the Native client migrates. Low-level provider alias
normalization may remain inside the SDK but cannot cross the HTTP/WebSocket boundary.

### Canonical outcome contracts

Canonical enums are:

- Run status: `completed`, `cancelled`, `failed`.
- Effective rubric state: `on`, `off`, `unavailable`.
- Grader evaluation result: `satisfied`, `needs_revision`, `invalid_rubric`, `grader_error`.
- Terminal rubric status: `not_run`, `satisfied`, `max_attempts_reached`, `invalid_rubric`,
  `grader_error`, `cancelled`.

Legacy grader result `failed` maps to `invalid_rubric` at the compatibility boundary. UI copy maps
`invalid_rubric` to `Rubric configuration invalid` and `grader_error` to `Rubric check failed`.

A criterion is `{name: string, passed: boolean, gap: string | null}`. An evaluation is
`{grading_run_id, attempt, result, explanation, criteria, passed_count, total_count}`. Counts are
derived and validated by the backend.

Each usage aggregate is:

```json
{
  "available": true,
  "calls": 2,
  "models": ["anthropic:claude-sonnet-4"],
  "input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "cache_read_tokens": 0,
  "cache_creation_tokens": 0
}
```

Usage is summed across every call and revision attempt in one category. `agent`, `grader`, and
`summarizer` remain separate categories. When a provider supplies no usage, `available` is false
and numeric values are zero; missing usage is never inferred from context estimates.

`RunResult` contains `schema_version`, `run_id`, `session_id`, run status, final one-based attempt,
canonical model ID, final response, durable final message ID or null, the three usage aggregates,
terminal rubric status and evaluations, final context snapshot or null, and `persisted_at` or null.
The existing REST `MessageResponse` remains backward compatible and adds this object as `run`; its
existing response, usage, and verification fields are derived from `run` during the compatibility
period.

### Context semantics

Measure context immediately before every agent provider call, after middleware, system-prompt
composition, and tool selection. Include system and conversation messages, revision feedback,
reasoning carried into the request, and selected tool schemas. Exclude grader and summarizer calls.
The measurement represents input occupancy and does not reserve projected output tokens.

Each prepared-call snapshot contains canonical model ID, one-based attempt, one-based
`llm_call_index`, estimated input tokens, model context window, percentage, source, and `estimated`.
The approximate tokenizer sets `estimated: true`; provider-reported input usage may refine a
completed-call snapshot but must not be presented as the next call's prepared context.

`RunResult.last_call_context` is the last prepared agent-call snapshot. After the final answer is
known, the runner also calculates `RunResult.next_context`, a projected next-turn occupancy that
includes the final answer and retained summary but excludes a not-yet-known next user message and
future dynamic tool selection. The composer rail uses `next_context`; it does not use
`last_call_context`.

Snapshot `source` is `prepared_context`, `provider_usage`, `post_run_projection`, or
`history_estimate`; freshness is `live` or `stale`. Unknown estimated tokens or context window are
JSON null, and percentage is null when either operand is unknown or the context window is zero.
Compression status is `succeeded` or `failed`.

Fix summary persistence so summaries carry the originating `session_id`. Emit
`context_compressed` only when summarization actually changes prepared context. The existing
`/context-info` endpoint remains a fallback snapshot endpoint, uses effective user/model settings,
and returns `source: history_estimate` with `estimated: true`. It does not drive a compression
animation by itself. Changing chat or model marks the previous snapshot stale until a matching live
snapshot or fallback response arrives.

Summarization returns a typed result containing whether compression occurred, before/after message
sets, before/after token estimates, and failure status. Both threshold summarization and forced
overflow compression use this result and emit events through the same run event sink. A failed
compression emits `context_compressed` with failure status and retains the previous snapshot.

### Persistence

Persist only the final assistant answer from a revised run. Persist attempt-scoped reasoning and
tool activity for auditability, stable message IDs, and the same `run_id` on every record needed to
reconstruct the turn. Superseded assistant answers are never persisted.

The initiating user message receives `run_id` when `RunService` persists it. Audit records include
`block_id`, one-based `attempt`, monotonic `block_sequence`, optional `tool_call_id`, and
`include_in_model_context: false`. Synthetic rubric feedback is stored in the rubric evaluation
history, not as an ordinary persisted user message. Future model-context loading includes the user
message and final assistant answer but excludes audit-only reasoning, tools, and rubric records.
The in-memory retry transcript still contains the attempt data needed for the immediate revision.

Persistence is idempotent on `run_id`. One shared transaction writes the final answer, audit
records, metadata, and terminal run record. Database-generated message IDs are returned in
`RunResult`; retries after an uncertain adapter disconnect read the existing run instead of writing
duplicates.

Final assistant metadata uses a versioned shape:

```json
{
  "schema_version": 1,
  "run_id": "...",
  "model": "provider:model",
  "usage": {
    "agent": {"available": true, "calls": 2, "models": ["provider:model"], "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
    "grader": {"available": true, "calls": 1, "models": ["provider:grader"], "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
    "summarizer": {"available": false, "calls": 0, "models": [], "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
  },
  "last_call_context": {
    "estimated_tokens": 0,
    "context_window": 0,
    "percentage": 0.0,
    "estimated": true
  },
  "next_context": {
    "estimated_tokens": 0,
    "context_window": 0,
    "percentage": 0.0,
    "estimated": true
  },
  "rubric": {
    "status": "satisfied",
    "attempts": 1,
    "max_attempts": 3,
    "latest_evaluation": {
      "grading_run_id": "...",
      "attempt": 1,
      "result": "satisfied",
      "explanation": "All criteria passed.",
      "criteria": [],
      "passed_count": 0,
      "total_count": 0
    }
  }
}
```

REST, SSE, and WebSocket use the same `RunService` persistence path. The existing `/conversation`
response remains backward compatible and adds stable message IDs. Add
`GET /conversation/turns?session_id=...&limit=...&cursor=...` for the Native client. Its limit counts
complete turns, never raw records; each turn contains the initiating user message, ordered audit
blocks, final answer if present, and terminal run metadata. Cursor pagination never splits a run.

```json
{
  "turns": [{
    "run_id": "run-1",
    "status": "completed",
    "user": {"id": "message-1", "content": "...", "timestamp": "..."},
    "blocks": [{
      "id": "block-1",
      "type": "reasoning",
      "attempt": 1,
      "sequence": 1,
      "content": "..."
    }],
    "answer": {"id": "message-2", "content": "...", "timestamp": "..."},
    "result": {"schema_version": 1, "run_id": "run-1"}
  }],
  "next_cursor": null
}
```

Message, block, run, and cursor IDs are opaque strings. Block type is `reasoning` or `tool`; tool
blocks additionally contain `tool_call_id`, `name`, `status`, `arguments`, and `result`. `answer` is
null for cancelled or failed runs without a completed answer. The embedded `result` follows the
complete canonical `RunResult` contract rather than the abbreviated example above.

Mixed history groups versioned records by `run_id`. Legacy records are grouped by the existing role
ordering until the next versioned user message. Orphan legacy reasoning/tools form a read-only
legacy turn; duplicate stable IDs are ignored; interrupted versioned runs may omit a final answer
and carry terminal `cancelled` or `failed` status. Full rubric history is persisted once in terminal
run metadata.

## Frontend State and Ordering

Each chat owns:

- Selected next-run model.
- Context state with status, model, generation, timestamp, and stale marker.
- Latest completed run metadata.
- Optional active run containing stream key, run ID, snapshotted model, attempt, usage, rubric
  evaluations, and completion phase.

New chats inherit the effective default model. A completed run persists its model; reopening a chat
uses the latest completed run model as that chat's next-run selection. Changing the selector affects
subsequent runs in that chat and is not durable until a run completes.

Use separate request-key namespaces for streams, history, context, title generation, and settings.
A delayed context response is accepted only when session ID, model ID, and request generation still
match the chat.

The normal ordering is:

1. Send snapshots the selected provider/model and effective rubric settings.
2. Stream reasoning, tools, and answer blocks into the active turn.
3. Accumulate usage by run and category.
4. Apply live context and rubric events by run ID and attempt.
5. Replace the active answer in place if a revision starts.
6. Receive durable `done`; the backend has already persisted the run.
7. Merge complete-turn history by stable IDs and perform a fallback context refresh if no terminal
   snapshot arrived.

Switching chats does not redirect active-run events to the visible chat. Unread status is updated on
the owning chat. Restoring history reconstructs turns from stable IDs and metadata rather than
whichever message was most recently appended locally.

History responses captured before an active run's generation are ignored. Reconciliation merges
complete turns and never replaces an active provisional turn. The Native client retains the latest
100 complete turns plus any active turn; pagination may fetch older complete turns without splitting
a run.

## Settings UI

Under `Settings -> General`, add a `Rubric check` card containing:

- Global enable/disable.
- Grader model selector displaying `provider/model` over a canonical `provider:model` value.
- Maximum response attempts from 1 through 3, explained in user language.
- Editable grader prompt criteria.
- Reset-to-default action.

Saving is explicit. It writes settings and grader-prompt changes through their respective APIs,
reports partial failures accurately, and never claims unsaved values were applied. The UI retains
failed edits for retry. Changes affect subsequent runs in every session; active runs retain their
captured settings.

## Accessibility

- Tools, Skills, Subagents, and Settings are keyboard-focusable named navigation controls.
- Context occupancy exposes progress/status semantics and textual warning state.
- Rubric status uses text in addition to color.
- Reasoning, criteria, and error content remain available to accessibility snapshots.
- Focus remains stable when status rows appear or a revision replaces answer content.
- Reduced-motion behavior follows the motion rules above.

## Error Handling

- Unknown context limit hides the meter and explains why in status details.
- Failed context refresh retains the last valid value and marks it stale.
- Grader errors preserve the assistant answer and show `Rubric check failed`.
- Maximum attempts preserve the final attempted answer and show an amber incomplete state.
- Missing grader prompt makes rubric unavailable without preventing ordinary chat.
- Effective rubric availability reasons are `missing_prompt`, `invalid_grader_model`,
  `missing_credentials`, and `provider_unavailable`. An unavailable rubric downgrades the run to
  ordinary chat and records rubric status `not_run` plus the reason.
- Settings validation identifies the specific invalid field.
- A failed settings or prompt request retains edits and does not increment revision or invalidate
  loops. If prompt save succeeds and the following settings save fails, the successful prompt
  revision remains active and the UI reports the partial save.
- Persistence failure leaves provisional content visible as unsaved and offers retry; it never
  triggers immediate history reconciliation.
- Unsupported or malformed legacy metadata falls back to role-order transcript grouping.

## Testing

### Backend tests

- Effective settings precedence, including explicit false and empty-list values.
- Canonical validated settings paths, legacy migration, authentication, atomic writes, and
  concurrent updates.
- Grader prompt seed, get, update, reset, empty validation, and missing-seed behavior.
- Rubric satisfied on first attempt.
- Needs-revision followed by success with one bounded orchestration.
- Maximum attempts reached without recursion.
- Grader failure and malformed grader output.
- Equivalent terminal rubric outcomes for REST, SSE, and WebSocket; identical live event ordering
  for SSE, WebSocket, and the internal event collector.
- Same-session serialization and different-session independence.
- Session lock acquired before user persistence and `409 session_busy` for concurrent sends.
- Aggregate agent, grader, and summarizer usage with unavailable-provider usage.
- Final-only assistant persistence with stable run/message IDs.
- Idempotent persistence by `run_id`, durable `done`, and persistence-failure behavior.
- Audit-only records excluded from future model context.
- Session-scoped summary persistence and before/after context events.
- Effective model resolution in `/context-info` and estimated-value labeling.
- Event envelope sequencing, duplicate suppression, gap handling, and blocked-grader proof that
  rubric start is observable before grader completion.
- SSE and WebSocket contract tests assert that no legacy transport event names are emitted.

### Native SDK tests

- Model label uses `provider/model` and selection is snapshotted into an active run.
- Separate request keys prevent history/context/stream collisions.
- Stale context responses are rejected by session, model, and generation.
- Reasoning remains expanded after completion.
- Variable-height estimates account for long reasoning and rubric details.
- Rubric events update the correct run and attempt.
- Revision replaces the active answer rather than appending a duplicate.
- History metadata restores model, usage, context, and terminal rubric status.
- Unknown and stale context states render without misleading percentages.
- Intrinsic composer layout remains inside desktop and minimum window bounds.
- Sidebar destinations expose control semantics and keyboard focus.
- Reduced motion removes translation and continuous rail movement.

### Visual verification

- Update deterministic light and dark baselines.
- Capture empty, streaming reasoning, rubric checking, rubric passed, revision, high-context, context
  compression, stale-context, settings, and minimum-window states.
- Verify interaction states for every sidebar row, starter pill, model selector, rubric status,
  context affordance, and Send/Stop control.

Backend tests inject deterministic UUID, clock, token-estimator, provider, event-sink, and
filesystem-failure boundaries. One scripted orchestration fixture drives REST, SSE, and WebSocket
parity assertions. The blocked-grader fixture waits on an event so tests can observe rubric start
before releasing grader completion.

Native state and motion tests use fixture `RunEvent` envelopes and complete-turn responses, an
injectable animation clock, and an explicit reduced-motion model setting. Supported visual bounds
are the 1200x720 baseline and an 800x600 minimum desktop window. The composer, bottom navigation,
and active status remain fully visible at both sizes.

## Acceptance Criteria

- The interface retains the approved compact control-plane appearance without a dashboard header.
- Reasoning remains expanded and readable.
- Rubric revisions are bounded and behaviorally identical across REST, SSE, and WebSocket.
- Rubric status is attached to the correct run and never claims factual verification.
- The context rail reflects prepared agent context or clearly reports an estimate/stale state.
- Compression animation occurs only after an explicit compression event.
- Provider/model, aggregate usage, context, and rubric outcome survive history reload.
- Global-per-user rubric settings affect subsequent runs and never mutate an active run.
- The composer does not clip at supported window sizes.
- All new states are keyboard accessible, textually identifiable, and reduced-motion safe.
