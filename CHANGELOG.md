# Changelog

## v0.6.26 — 2026-10-02

### Fixed — the tracing acceptance gate is now real
**`config.yaml` could force product tracing on over an explicit opt-out.** The committed yaml shipped `observability.langfuse.enabled: true`, the Docker image bakes that yaml in, and the loader's yaml-bridge copied it onto `langfuse.enabled` *after* construction — so `LANGFUSE_ENABLED=0` was silently overwritten. The gate only appeared to hold while Langfuse keys/hosts were absent; the moment credentials were wired (as v0.6.25's product-tracing setup does), a container whose owner had explicitly **not** accepted tracing was sending full message payloads to our Langfuse. Found by testing the acceptance contract rather than asserting it: the gate-closed container produced 8 observations.

- **Env beats yaml on the gate**: the bridge now applies yaml's `enabled`/`host`/`environment` only when the corresponding `LANGFUSE_*` env var is silent, matching the documented `env > .env > yaml` precedence everywhere else.
- **Consent default is OFF**: `config.yaml` ships `enabled: false`. Opt in per deployment with `LANGFUSE_ENABLED=1` or `enabled: true` in yaml — but never both ways: an explicit `=0` closes the gate regardless of yaml.
- **Regression tests pin the contract**: env opt-out beats yaml-true; yaml applies when env is silent; env opt-in beats yaml-false. The fail-closed host validation (enabled + credentials + no host → refuse to boot) is unchanged, now triggered by explicit opt-in instead of the yaml default.

### Product tracing, wired but off (subject to user acceptance)
Hosted deployments can trace to our own observability, per sink, each with its own acceptance switch:

| sink | contents | acceptance switch |
|---|---|---|
| Langfuse (`langfuse.openassistants.org`) | semantic: full message payloads | `LANGFUSE_ENABLED=1` |
| ClickStack (`clickstack.openassistants.org`) | operational: allowlisted attributes, no prompts | set `OBSERVABILITY__CLICKSTACK__ENDPOINT` + `...__HEADERS` |

Credentials live only in the gitignored `docker/.env` (compose `env_file`); `docker/.env.example` documents the shape with placeholders and the acceptance semantics. **Verified by test at the image level, both directions**: `LANGFUSE_ENABLED=0` → a message completes and produces **zero** Langfuse observations; `LANGFUSE_ENABLED=1` → the same message produces its generation + spans. ClickStack's OTLP ingest takes a plain `authorization: <OTLP_AUTH_TOKEN>` header — the UI/MCP bearer key is a different credential and is rejected.

Also: `opencode.json` (repo root) is untracked and gitignored — it carried a live Langfuse MCP credential, now rotated; `opencode.example.json` documents the shape.

## v0.6.25 — 2026-10-01

### Added — trace fan-out, and an agent that finishes what it starts
- **Operational traces fan out to two sinks.** We host deployments, so traces belong in our Langfuse *and* our ClickStack, and a tenant may bring their own later. `observability.clickstack` is a second OTLP destination fanned out in parallel to `observability.otel` — one span of work, N destinations, the same filtered exporter (operational spans, allowlisted attributes, **no prompts**) wrapping every sink equally. Verified live end to end in both directions: Langfuse generations land and ClickStack stores real spans from a real run (`sandbox.exec`, `db.query`, with true durations) and reads them back through its MCP.
  - ClickStack ingest auth is the **plain `authorization: <OTLP_AUTH_TOKEN>`** header — the UI/MCP bearer key is a different credential and 401s. Wrong-credential failures show up as `Failed to export span batch code: 401` in the server log; that log line is where to look.
- **`run_tests`** — a bounded pytest runner over the sandbox seam (timeout, output and write caps), enforcing `filesystem.allowed_roots` before anything runs. It returns an honest `RESULT: ALL PASS / FAILING` line first and points at the failing test as the spec. Deliberately **not** `read_only`-auto-approved: pytest executes arbitrary code, so blanket auto-approval would be a wider hole than the shell it sits beside. Benchmark traces showed why it's needed: agents were spending 4 of 13 calls building a custom `run_pytest` tool to route around approval friction, and in 3 of 5 runs never editing anything at all.

### Fixed — three silent-misapplication bugs, two found by one trace
- **The ClickStack sink could only ever be configured on purpose.** It was typed as `OtelConfig`, whose env prefix is `OTEL_` — so `OTEL_ENDPOINT` silently configured *both* sinks and every span was exported to one destination **twice**. `ClickstackConfig` now has its own env namespace (`CLICKSTACK_OTEL_*` or the `OBSERVABILITY__CLICKSTACK__` nested path); caught by the lifecycle gate test (`assert 2 == 1` exporter builds), which did exactly what it exists for.
- **The duplicate-call guard served stale receipts for state-changing tools.** Its nudge premise — "the same call returns the same result" — is false after an edit: a live trace showed `run_tests` re-run after a successful fix being answered with the pre-edit FAILING result, telling the model its fix did not work when it did. The guard now tracks **read-only tools only** (identical re-reads stay deduplicated); non-read-only tools re-execute, bounded by the per-tool repetition budget, max iterations and cost limits. Grader re-run seeding applies the same filter.
- **The filesystem boundary is visible at startup.** The effective `filesystem.allowed_roots` is logged (`filesystem.boundary_effective`) and echoed to stdout when the server starts. A security-relevant setting that silently fails to apply is a security bug, and this one twice went unnoticed — third instance of the same class (after NoDecode and the dropped MCP `type` field).

### Benchmark — parity achieved
`scripts/bench/agent_parity.sh` now **asserts its own precondition**: after the server reports healthy, the script greps the startup boundary line for the task directory and aborts loudly if the agent cannot write it. This closes the failure that invalidated ten samples across two rounds: a `#` comment inside the backslash-continued server launch chain detached `FILESYSTEM_ALLOWED_ROOTS` from the command, the agent routed around the refusal by fixing a *copy* of the app in its own data dir (tests passing there), and the harness failed it against the untouched original — the instrument was measuring a phantom.

With the instrument honest, the traced verdict: **6/6 PASS at 12–36 s / 6–15 calls** vs Pi's 7–12 s, inside the ≤2× gate — and the trace says what remains is model time (12.05 s of a 14 s run is 7 generations; tools ~1.3 s; framework under a second). Full breakdown recorded in `docs/audits/2026-09-29-agent-posture-audit.md` §7.

### Verified — #37 closed
Dropped-stream session wedging is verified fixed against the **published image** (`ghcr.io/open-assistants-lab/assistant:0.6.24`): a client killed mid-stream leaves the session usable within seconds (the disconnect teardown releases the session lock), `POST /message/cancel` returns `{"status":"cancelled"}`, and a full run afterwards completes. No operator `/profile/reload`, no forever-`session_busy`. Operational note from the verification: a container's first-ever message blocks ~80 s on ChromaDB's 79 MB ONNX embedding model download before stream headers arrive — a long-polling client with a short timeout will time out on a legitimate cold start.

### Also in this release (previously unreleased)
- Environment variables now override `config.yaml` (`env > .env > yaml > secrets`, with startup disagreement warnings) — previously yaml silently beat every env var.
- `filesystem.allowed_roots` lets the agent work outside its workspace under an enforced boundary, with a per-tool call budget against probing.
- The tenant-isolation guard: once one process serves more than one user, `shell_execute: allow` degrades to `ask` (`allow_shell_when_multi_user` escape hatch).
- MCP config example (`.mcp.example.json`) and the `type` field fix; the server reads `~/Assistant/.mcp.json` only.
- Multi-user isolation posture corrected and documented: **3a = one container, one OS user per tenant** (specified, not built).

## v0.6.24 — 2026-09-28

### Added — subagent scheduling is now reachable (#46)
Scheduled subagent runs can finally be created. The scheduler was built, durable and restored on boot, but nothing could create a job, so the capability was dead code.

- `POST/GET/DELETE /subagents/schedules` and `GET /subagents/schedules/{id}/runs`, which joins a schedule with the work-queue task it last launched. A `subagent_schedule` tool gives agents the same capability under the normal approval model. **Off by default** — set `SCHEDULING_SUBAGENT_ENABLED=1`.
- A schedule may only be created from a **ready** frozen launch plan. `build_launch_plan` already resolves `ask`/`deny` for every declared capability, so requiring readiness is what makes an unattended schedule impossible to register when nobody can answer an approval. There is no attestation mode and no second governance tier.
- The API and the tool share one creation path, so the two surfaces cannot disagree about that gate.
- Cross-user access returns **404, not 403**: a schedule ID never discloses that it exists. Cancellation is idempotent and a foreign cancel leaves the owner's row untouched.

### Fixed
- Trigger restoration is faithful. The previous path discarded the persisted trigger and rebuilt every scheduled row as a one-shot ~30 seconds after startup, so a recurring schedule silently became a one-shot and a future one-shot fired at the wrong time. A one-shot missed while the process was down is now reported as `expired` rather than run hours late.
- Scheduled runs go through the governed coordinator and its work queue, replacing the legacy path that had no capability preflight, no receipts and no work queue. A frozen manifest is re-verified at fire time and the run is **refused** on any difference, with a distinct `manifest_drift` / `manifest_tampered` code, rather than silently widening or narrowing its authority.
- Schedule writes are transactional and serialised. A cancelled or failed `create()` used to leave its row pending on the shared SQLite connection, where the *next* writer's commit made it durable — a schedule the caller never received an ID for, which restore would then have fired unattended. A cancel can no longer overwrite an in-flight run's status, and a disabled server stays visible in `/mcp/health` with a reason instead of silently vanishing.
- `subagent_create`/`subagent_update` now warn when a declared capability would block a launch, so an unlaunchable profile is visible at creation rather than at run time. The launch gate itself is unchanged and stays fail-closed.

### Changed
- Legacy `job_results` rows are migrated at startup into `subagent_schedules`, but **parked as `needs_review`** rather than given a reconstructed trigger: that table stored no `run_at`/`cron`/`timezone`, so a 03:00 one-shot and a daily cron are indistinguishable and inventing a time could fire unattended work at the wrong moment. Each row keeps its reason and the originating job id, and the legacy row is marked `migrated` so the migration is re-entrant.

### Deployment
- The local property-research deployment wiring is now on main: the firecrawl CLI and Node in the image (the research agent's only web access runs inside the container), a 180s shell timeout for long scrapes, and compose on `127.0.0.1:8099` where 8080 is already held. This deployment also sets `governance.permissions.tools.shell_execute: allow` at the admin level, overriding the `ask` default that exists because #40 is open. It is scoped to that config file, not the code.

## v0.6.23 — 2026-09-26

### Fixed
- Token usage is no longer reported as zero for Ollama providers (#48). Two payload shapes defeated the parser: a `usage` object that was *present but zeroed* took precedence over the authoritative native `prompt_eval_count`/`eval_count` (because `dict.get(key, fallback)` only falls back when a key is **absent**), and `usage` in OpenAI `input_tokens`/`output_tokens` shape missed both known keys entirely. All three shapes — native counts, `prompt_tokens`/`completion_tokens`, and `input_tokens`/`output_tokens` — now resolve correctly, and each field falls back independently. A payload with no token information at all now emits **no** usage event rather than a zeroed one, which previously read as "measured and free".
  - This also makes `RunConfig.cost_limit_usd` enforceable again: it is checked against the running total fed only by provider-reported tokens, so zeroed usage meant the ceiling could never be reached.
- Documented `METERING_ENABLED`, which was previously undiscoverable. Usage events are produced on the stream and enforced by `cost_limit_usd` regardless, but they are **not persisted** while it is off, so `GET /usage/summary` returns `[]` for a self-hoster by design rather than by defect. Set `METERING_ENABLED=1` to persist. `GET /billing/cost` is separately tenant-scoped and reports month-to-date cost across that tenant's members, so a single-container trusted deployment with no tenant sees `0.0` by design.

### Documentation — corrects a published claim
- **Workspaces are a named scope, not a storage boundary** (#47). Files, custom tools, skills, subagent definitions, conversation history and memory are all **user-global**; two workspaces belonging to one user share them. This is what the code has always done — the isolation claim in `workspace_models.py` was the defect — and the docstrings now say so.
  - Concretely, a previous release note stated *"Child file access is confined to that workspace."* **That was not accurate.** `workspace_id` is threaded correctly to the path resolver, but the resolver returns a user-scoped directory, so a child's file access is confined to the **user's** files directory. Accordingly, `requested_workspace_id` in a subagent launch manifest scopes skills, prompt and tool-selection context — it does not confine the filesystem. That guarantee needs to be added deliberately if it is ever wanted.
  - The real isolation boundary is `user_id`. The sharing boundary that matters next is the **team**: `DataPaths` already accepts `team_id` and team data already lives under `data/teams/{team_id}/`, so moving from user-global to team-scoped storage is a scope change on a seam that already exists.
  - The per-workspace `workspace_id` column on the messages table is currently never populated and is left in place as reserved.

### Added — subagent scheduling foundations
- Durable subagent schedule definitions now have a real store, and a scheduled run launches from a frozen capability manifest that is re-verified against current state at fire time; any difference fails the run rather than silently widening or narrowing its authority.
- The APScheduler path is now async and restores triggers faithfully. Previously every persisted schedule was rebuilt as a one-shot roughly 30 seconds after startup — so a recurring schedule silently became a one-shot and a future one-shot fired at the wrong time. A one-shot missed while the process was down is now reported as `expired` rather than run hours late.
- Scheduled runs go through the governed coordinator and its work queue, replacing the legacy path that had no capability preflight, no receipts and no work queue. The whole warning path on create/update is advisory: a subagent that cannot launch now says so at creation instead of silently failing at run time.
- **Still not reachable**: no API or tool creates a schedule yet. Tracked in `docs/superpowers/plans/2026-09-25-subagent-scheduler.md`.

## v0.6.22 — 2026-09-26

### Changed — behaviour change, read before upgrading
- **`mcp.exposure` now defaults to `auto`.** The exposed set of MCP tools is decided by *measuring* the deferrable tool definitions against the model's context window and deferring when they reach 10% (`auto:10`), instead of asking an operator to guess. `always`, `search` and `never` remain available as explicit overrides. This is the default behaviour change in this release: a deployment that never set `exposure` now gets the measured policy.
  - `auto` measures the **durable metadata cache** and opens **no** MCP connections, where `always` opens one per configured server. A cold cache defers rather than injecting a catalogue that has never been costed against. This deliberately differs from Pi's `deferWithMissingMetadata: false`; set `mcp.defer_with_missing_metadata: false` to opt into that behaviour instead.
  - A percentage is the right unit, so the same deployment exposes a different tool set on an 8k local model than on a 200k hosted one. The effective decision is reported in `/mcp/health` as `exposure_decision` and written to the audit stream.
  - The v0.6.21 values `direct`, `hybrid` and `proxy` are still accepted for one release, mapped to `always`, `search` and `never`, and reported as deprecated in `/mcp/health`. An unrecognised value fails closed to `never` with a configuration error.
  - Note: v0.6.21 shipped a `config.yaml` containing an explicit `exposure: direct`, so deployments using that file keep a fixed surface. This release's `config.yaml` ships `exposure: auto`.

- **MCP tools can now be filtered with globs.** `mcp.include_tools` then `mcp.exclude_tools` (exclude is applied second, so a name in both is excluded), per-server equivalents on each entry in `.mcp.json`, and a coarse `tools.disabled` family gate that applies to every tool family. All of them narrow only: none can re-admit a capability-disabled or governance-denied tool.

### Added
- One always-present MCP surface. `mcp_list`, `mcp_tools` and `mcp_reload` are removed and their behaviour is reachable through `mcp_proxy` actions (`status`, `describe`, `refresh`). The MCP tool surface drops from ~725 to ~342 tokens per turn.
- A governance firewall for the tool surface, enforced in code and covered by tests: exposure decisions never influence permission resolution, a tool discovered by `tool_search` is still permission-checked on invocation, an excluded server stays visible in `/mcp/health` with a reason, the capability floor is removal-only, and an automatic change to the reachable tool set is audited as an `exposure_decision` event. The new audit kind is additive and needs no migration.
- Per-server `enabled` replaces a `disabled` field that shipped in v0.6.21, was excluded from serialization so it could not be read from `.mcp.json`, and was never consulted. A disabled server is never connected and reports `status: disabled`.
- Every `tools/list` fetch is bounded by `mcp.refresh_timeout_seconds`, so a hung server can no longer stall a loop build or a reconnect.
- `mcp.always_load` exempts named tools from deferral. Server-supplied `meta.alwaysLoad` is ignored unless the operator sets `trust_server_exemptions` for that server, so a third-party server cannot grant itself a permanent slot in every context window.

### Fixed
- MCP lifecycle: a failed tool discovery closes its candidate connection instead of leaking it, configuration changes and deletions are reconciled centrally, reconnects from a superseded generation are rejected, `close_mcp_manager()` releases connections and listeners, and stale connections can be reaped. Health and cache reads no longer start a lazy server.
- Schedule writes are transactional and serialized, so a cancelled or failed write can no longer be committed by an unrelated later writer.

### Subagent scheduling (not yet reachable)
- Durable subagent schedule definitions now have a real store, and a scheduled run launches from a frozen capability manifest that is re-verified against current state at fire time; any difference fails the run rather than silently widening or narrowing its authority. **No API or tool creates a schedule yet** — this is the persistence and launch layer only. Tracked in `docs/superpowers/plans/2026-09-25-subagent-scheduler.md`.

## v0.6.21 — 2026-09-26

### Added
- Durable subagent schedule definitions now have a real store (`data/subagent_schedules.db`), the first step toward exposing the existing APScheduler subagent scheduler, which is built, durable, and restored on boot but unreachable because nothing creates a job (#46). Schedule definitions live in their own database so APScheduler keeps sole ownership of the `jobs.db` schema, and every read and write is scoped by `user_id`.

  Not yet reachable through an API or tool: this release only adds the persistence layer. Exposing it over `/subagents/schedules` and `subagent_schedule` is tracked in `docs/superpowers/plans/2026-09-25-subagent-scheduler.md`.

- MCP proxy-first architecture, available behind `mcp.exposure` (`direct` | `hybrid` | `proxy`, default `direct`). A governed `mcp_proxy` tool provides cache-backed `search`/`describe` plus `call`, revalidating capabilities and current `allow`/`ask`/`deny` policy before every dispatch. Search and describe read a secret-free durable metadata cache without opening a server connection; `hybrid` promotes only explicitly allowlisted tools via `sync_direct_tools()` with stale removal. Transport host allowlists and OAuth remain out of scope.

- MCP lifecycle reliability: a failed tool discovery now closes its candidate connection instead of leaking it, config changes and deletions are reconciled centrally, reconnects from a superseded generation are rejected, `close_mcp_manager()` releases a user's connections and listeners, and stale connections can be reaped. Health and cache reads no longer start a lazy server.

### Fixed
- Schedule writes are transactional and serialized. A cancelled or failed `create()` used to leave its row pending on the shared aiosqlite connection, where the *next* writer's commit made it durable — a schedule the caller never received an ID for, which startup restore would then fire unattended. Writes are now serialized so at most one write transaction is open and every failure path rolls back.
- A schedule that records a stop decision (cancelled/rejected/expired/invalid) can no longer be silently re-armed back to `scheduled`. Outcome states (`completed`/`failed`) stay re-drivable so a recurring schedule still fires again after them.
- Cancelling a schedule no longer overwrites an in-flight run's status; the row keeps saying `running` so the live run stays observable, since the work queue remains the authority on it.
- Corrupt schedule-database recovery now also removes the `-wal`/`-shm` sidecars. A stale WAL is a self-contained log that SQLite replays over the fresh file, which resurrected the old rows *and* the old schema, silently turning schema creation into a no-op. Earlier quarantined files are no longer overwritten.

## v0.6.20 — 2026-09-25

### Removed
- Removed the runtime companion concept: proactive check-in scheduler, companion notifications/memory APIs, companion configuration, and companion storage aliases. The separate durable subagent scheduler remains available for the scheduled-subagent design.

## v0.6.19 — 2026-09-25

### Fixed
- Explicit sandbox environment allowlists restore deployment-owned secrets for custom commands without inheriting the full process environment (#43).
- Deprecated `governance.tiers` configuration is migrated explicitly to `governance.permissions.tools`; custom tools declaring `requires_approval` now fall back to approval instead of silently resolving to allow (#44).
- `tool_search` now loads discovered native/indexed tools into the live loop so the next model request can call them directly, instead of advertising tools that remain absent from the callable schema (#45).

## v0.6.18 — 2026-09-25

### Fixed
- Summarization recovery now uses the corrected message-store import, bounds persisted summaries, and excludes unusable oversized summaries from model context while retaining history (#42).
- `shell_execute` now defaults to approval-required when governance is active, and interpreter capabilities are no longer in the safe default shell allowlist (#40).
- Tool and governance results now carry explicit machine-readable outcomes; contradictory success/error states are rejected, and proposal status retains its replay-compatible terminal meaning (#38, #41).
- Dropped-session activity tracking, stale-run cancellation, opt-in pipeline failure semantics, truthful custom-output truncation, and configurable iteration exhaustion are now covered by regression tests (#35–#39).
- The native desktop path resumes app-owned Keychain credentials, provider selection, Skills/Subagents workspace destinations, and context-compression timeline events.

- Subagent launches now fail closed when declared tools or skills are missing, disabled, denied, or require approval. Each run uses a frozen capability manifest and requested workspace; child file access is confined to that workspace.
- Subagent completion delivery now persists and replays terminal outcomes idempotently. A shared per-user drain lock prevents workspace coordinators from concurrently publishing the same in-process notification; replay remains at-least-once across a crash between publish and acknowledgement.
- Custom `TOOL.md` commands now run through the sandbox seam (#34). Both wrappers (`_parse_tool_file` and the index-rebuilt twin) called `subprocess.run(..., shell=True)` directly, so a custom command received none of the caps every other command path honours: no `RLIMIT_FSIZE` (the write budget), no CPU or address-space limits, no uid drop, no scrubbed environment, and the server process's cwd rather than the workspace. Because the agent can author `TOOL.md` files with `files_write`, this was agent-reachable, not just author-reachable. Commands now execute as a single `sh -c` argv through `SandboxBackend.run()`, with the output and write budgets taken from `shell_tool.*` and the tool's declared `annotations.timeout_seconds` as the only wall-clock cap; signal kills and the `128+n` pipeline band still raise instead of returning a success string (#25, #32 part 2).
- The soft backend no longer sets `RLIMIT_NPROC=256` (#34). Without a PID namespace that limit is enforced against the whole real UID's process count on the host, so any forked command — a `a | b` pipeline, a tool that spawns a helper — failed with `EAGAIN` once the operator's ambient process count passed 256. Custom `TOOL.md` commands are typically pipelines, which is where it surfaced. The bwrap backend keeps its cap: `--unshare-pid` makes the count namespace-local.
- `timeout_seconds: none` (#23) is honoured on the seam: the backends skip the `RLIMIT_CPU` derivation instead of computing `int(None)`, and `NullSandboxBackend` plus the `SandboxBackend` protocol accept `user_id` like the other backends already did.
- A custom `TOOL.md` command killed by a signal inside a pipeline was reported as a failure *string*, which governance receipts as a successful execution (#32 part 2). The seam only recognised a kill when the shell *itself* died (`returncode < 0`); bash reports a signalled child as `128 + n`, so that status is now read as a signal death and raises `CommandKilledError` with the signal number. This also covers a plain (non-pipeline) command killed by a signal, since the shell encodes it the same way. A command that deliberately exits `128 + n` is indistinguishable by exit status and is reported as killed — both are failures, so only the marker differs.

  Not fixed, filed as #35: when an **early** pipeline member fails, the shell reports the *last* command's status, so `false | cat` still reads as success. Surfacing it means changing pipeline semantics (`pipefail`), which would also reclassify legitimate `grep`-style pipelines.

### Changed
- The sandbox's workspace write budget is its own number (#32 part 3). `RLIMIT_FSIZE` was derived from `max_output_bytes × 8` — about 800 KB with the shipped `shell_tool` defaults — so a command that legitimately wrote a larger file (a download, a generated report, an export) was killed, and raising the stdout budget silently raised the write cap with it. Writes are now bounded by `SandboxLimits.max_write_bytes`, defaulting to **64 MB** and configurable as `shell_tool.max_write_mb` (env `SHELL_TOOL_MAX_WRITE_MB`), independent of output capture. The cap is clamped to the host's hard `RLIMIT_FSIZE` so a host policy below the default cannot fail the command.

  Note the distinction: this is a **per-file** limit, not an aggregate quota — file count and total workspace size remain unbounded, bounded only by the command timeout and the host filesystem.

## v0.6.17 — 2026-09-19

### Fixed
- `GET /pendings` auto-executed an approved `show_then_auto_send` proposal without checking whether it was designated async, so a tool configured for the durable async path (#21) ran **inside the pendings request**: the request blocked for the run's duration, no operation was created (no cancel, no dispatch receipt, no `uncertain` handling), and the proposal settled through the synchronous leg (#33). The scan now mirrors the approve endpoint — executor-bearing rows are dispatched through the operation ledger and report the operation id and status, and only executor-less rows run synchronously. A refused dispatch fails closed, leaving the proposal for a human and logging the reason.

### Verification
- Chunked suite: sdk 1,975 passed / 4 skipped; api 609 passed / 6 skipped; unit+storage+config+integration 500 passed. mypy: 64 errors / 19 files.

## v0.6.16 — 2026-09-19

### Added
- `proposals.outcome` records what a consumed approval actually did — `succeeded`, `refused` (the tool never ran: disabled, tier re-checked to `hard_block`, or unresolvable), `failed`, `timed_out`, or `killed` (#32 part 1). Previously every consumed proposal read `status='executed'`, so a killed or failed action was indistinguishable from a clean one without reading `structured_content`. `status` keeps its meaning ("approved and consumed, terminal"; `replay_resume` depends on it). The outcome is derived from the receipt governance already builds, so the two cannot disagree, and it reaches `get_pending`, the pendings endpoints, the approve response and the idempotent re-approve. Existing databases gain the column through the existing `ALTER TABLE` migration; rows written before this change read as `None` — unknown, not a claim of success.

### Fixed
- A timed-out `which` availability probe was reported as a *governed* timeout — claiming the command hit its cap when only the check had — and then as "not found on PATH". It now reports that the availability could not be verified.
- The pendings scan hard-coded `status='executed'` beside a freshly executed row, so a proposal settled differently in the meantime could be labelled as executed; it re-reads the persisted row.

### Known limits
- `outcome` is derived from `is_error`, so failures a tool reports as a plain string still read `succeeded` (for example `web_fetch`'s network errors, the `browser_*` CLI failures, and `shell_execute`'s policy refusals). Those are narrow handlers, deferred with #30's scope. Governance-class failures (#23/#24/#25/#26/#30) and the three refusals are exact.

### Verification
- Chunked suite: sdk 1,975 passed / 4 skipped; api 607 passed / 6 skipped; unit+storage+config+integration 500 passed. mypy: 64 errors / 19 files (68 / 19 at the v0.6.13 baseline).

## v0.6.15 — 2026-09-19

### Fixed
- A tool whose body raised was receipted as a successful run: 38 broad `except Exception` handlers in tool bodies converted the exception into a plain string, and governance receipts a string as `executed: true, is_error: false` (#30). All now return `ToolResult(is_error=True)` or re-raise a marker failure. The same shape had already swallowed a deliberate timeout or kill three times during #23–#25, in each case making the fix inert until a re-raise was added.
- A subagent run that was cancelled, timed out or failed was receipted as executed: the coordinator caught those one layer below the tool, which passed the plain string through (#30).
- `mcp_reload` reported a clean reload after a failure that had already unregistered the session's MCP tools (#30).

### Changed
- Tool bodies that can report failure are declared `-> ToolResult | str`; `ToolAnnotations.timeout_seconds` is now `float | None`, which removed two pre-existing mypy errors.
- A convention test walks the AST of every tool body and fails when a catch-all returns a plain string, with an allowlist that fails once an entry is no longer needed.

### Verification
- Chunked suite: sdk 1,965 passed / 4 skipped; api 606 passed / 6 skipped; unit+storage+config+integration 500 passed. mypy: 64 errors / 19 files against 68 / 19 at the v0.6.13 baseline.

## v0.6.14 — 2026-09-19

### Fixed
- A governed tool returning a `ToolResult` lost its outcome: `is_error` was hard-coded `False` on the success-return path and its content became a pydantic repr through `json.dumps(..., default=str)`, so a tool that ran and failed was receipted as completed and logged as `status="completed"` (#26). The receipt now carries the tool's `content`, `is_error` and `structured_content`, with the governance keys winning so a tool cannot spoof `executed`/`tool`; a failed tool is now logged as `status="failed"`.
- A command killed by a signal was receipted `executed: true` (#25). A child killed by a resource limit (`RLIMIT_AS`/`CPU`/`NPROC`/`FSIZE`) or any other signal exits with a negative code and `timed_out=False`, which was indistinguishable from an ordinary non-zero exit — so the tools returned partial output as success. The sandbox now marks kills explicitly, the three native command tools and both custom `TOOL.md` wrappers raise `CommandKilledError` with the signal number and elapsed time, and governance records `executed: false` with `error: "killed"`. Signals are read from the explicit flag rather than the sign of `exit_code`, because the sandbox's own timeout also reports `-1`; timeout details now say "cap reached" rather than "killed" so the two outcomes do not share a word.

### Security and deployment
- Ordinary non-zero exits keep returning their output, so a `grep`-style command is unaffected; only a signal death is treated as a failure.

### Verification
- Chunked suite: sdk 1,953 passed / 4 skipped; api 606 passed / 6 skipped; unit+storage+config+integration 500 passed, with the opt-in phase-0 gate included.

## v0.6.13 — 2026-09-19

### Fixed
- Tool-index integrity was inferred from a row count and a source hash, neither of which can tell whether the rows that should exist are actually present (#29). Three repair gaps closed: a damaged install whose rows were dropped (matching hashes, non-empty index) was never rebuilt; a partial index left by a crash mid-`tool_reload` was never rebuilt; and `disable → tool_reload → re-enable` left no row, leaving the tool `Unknown tool` until another manual reload. The runner now re-indexes when an expected row is missing, comparing row names rather than a count, so the repair does not depend on the hashes. It writes only the gaps, commits the source hashes once the index reflects the sources, and logs which rows were missing.

### Changed
- The per-family indexing rules (which families are indexed, what is skipped, provenance and `reconstruct` payloads) now live in one place, used by both the session runner and `tool_reload`. Two hand-written copies of those rules is what let `tool_reload` stop indexing native rows (#28) and left rows unrecoverable.

### Verification
- Chunked suite: sdk 1,941 passed / 4 skipped; api 606 passed / 6 skipped; unit+storage+config+integration 500 passed; the opt-in phase-0 gate passes with `addopts` cleared.

## v0.6.12 — 2026-09-19

### Fixed
- `tool_reload` cleared the persisted tool index and re-indexed only custom and MCP rows, then committed the source hashes — so the native rows it removed were never rebuilt (#28). Non-core native tools are reachable only through their index row, so a reload left them permanently `Unknown tool`: a restart did not help (the committed hashes still matched) and a further reload did not help either. `tool_reload` now indexes non-core native tools with the same catalogue and filters as the session runner, and its summary reports the native count.

### Verification
- Chunked suite: sdk 1,937 passed / 4 skipped; api 606 passed / 6 skipped; unit+storage+config+integration 500 passed.

## v0.6.11 — 2026-09-19

### Fixed
- `ToolDefinition.ainvoke` could hand back an un-awaited coroutine instead of a result: dispatch was decided from a flag captured at construction, so a coroutine attached afterwards took the thread path, and `ToolResult.from_raw` stringified the coroutine into a **non-error** result — so governance recorded `executed: true` for a tool that never ran. Dispatch now inspects the current callable, with an `isawaitable` fallback for callables `inspect` cannot see through.
- `ToolDefinition.invoke` (the sync seam, used by 13 router endpoints) returned a coroutine that no sync caller can await; it now fails loudly and points at `ainvoke()`.
- The deployment-shared tools directory (`data_root/Tools`) was only half-wired: the session runner passed `workspace_tools_dir=None` to the tool index, so shared tool changes never triggered a reindex and shared-only tools were indexed with an empty `reconstruct` blob — rebuilding into a tool that ran an empty command and returned `(no output)` as a non-error result (#27).
- The tool index counted its own `Tools/.index` directory as a tool source, so the first hash set could never match a later call and the next thing to touch the index cleared every row (#27).
- Disabling a tool destroyed its index row while the enable path never restored it. Non-core native tools are reachable only through their row, so disable-then-enable left the tool permanently `Unknown tool`, invisible to `tool_search` and unrecoverable by restart or `tool_reload` (#27). The purge was redundant: `tool_search` already filters capability-disabled rows at query time.
- `find_tool_file` resolved the shared copy before the per-user one, recording the shared tool's command for a tool the model was shown as the user's override (#27).

### Changed
- `ToolDefinition.invoke()` now raises `TypeError` for async or awaitable-returning callables instead of returning an un-awaited coroutine. Pass synchronous tools through `invoke()`, and use `await ainvoke()` for everything else — the SDK's sync seam no longer returns something a caller cannot await.
- Hashing a `TOOL.md` tolerates an unreadable file instead of aborting session construction.

### Removed
- `src/sdk/tools_core/browser_agent.py` — unreachable code superseded by `browser.py`, which registers the same tool names. It also read a `SandboxResult` field that does not exist, so registering it would have failed every call.

### Verification
- Full suite: 3,032 passed, 11 skipped (chunked: sdk 1,920; api 606; unit+storage+config+integration 500).
- mypy: 68 errors across 19 files, none in a file these changes touch (69/20 at the v0.6.10 baseline; deleting the dead module removed one).

## v0.6.10 — 2026-09-18

### Fixed
- The native command tools (`shell_execute`, `code_execute`, and the CLI adapter behind browser tools) returned a timeout as a normal string, so the governance layer recorded `executed: true` for a command that was killed mid-flight (#24) — the same defect class #23 fixed for custom `TOOL.md` tools. All three now raise the shared timeout failure, and governance records `executed: false` with `error: "timed_out"`.
- `shell_execute`'s catch-all exception handler silently converted the new timeout failure back into a successful string return; it now re-raises explicitly (#24).
- Timeout details for CLI-backed tools no longer carry the full argv, which could include user-supplied secrets, into model-visible content, logs, and audit records.

### Verification
- Full suite: 3,020 passed, 11 skipped.

## v0.6.9 — 2026-09-18

### Fixed
- Custom `TOOL.md` tools silently truncated command output to 5,000 characters with no way to recover the rest (#22). Large results are now preserved and readable in bounded pages via the new read-only `tool_result_read` tool, which returns a preview envelope with the total size, the original result ID, and the next offset. Recovery never reruns the command and does not require `files_read`.
- A cap-killed custom command was returned as a normal string, so the governance layer recorded `executed: true` for work nobody observed completing (#23). A timeout now raises a distinct failure carrying `timed_out` plus an elapsed detail, and governance records `executed: false` with `error: "timed_out"` instead of claiming success.

### Added
- `ToolAnnotations.timeout_seconds` for custom tools: default 300s, any positive value accepted with no ceiling, and the literal `none` as an explicit opt-out. `0`, negative, boolean, non-finite, and non-numeric declarations are rejected at load time rather than silently changing the cap; a rejected definition skips only its own tool instead of aborting session construction.

### Changed
- The custom-command cap is now configurable and no longer overrides a tool author's declared budget.

### Security and deployment
- Saved custom-tool results are scoped to the invoking user and workspace, stored outside the workspace file tree with restrictive permissions, and expire after seven days or eviction.

### Verification
- Full suite: 3,010 passed, 11 skipped.

## v0.6.8 — 2026-09-17

### Added
- Durable governed operations (#21): async `execution_mode` for tools with a durable operation ledger, external-executor outbox, and lifecycle dispatch — all in transactional per-user `governance.db`.
- `ToolAnnotations.execution_mode: sync | async` (default `sync`); custom `TOOL.md` definitions may declare `execution_mode`.
- Async approval returns an accepted operation without synchronous execution; operations expose get/list/cancel APIs.
- External HTTP executors with HMAC-derived, hash-pinned callback capabilities; ordered, idempotent callback events with terminal-progress freeze.
- Operation cancellation: queued operations cancel atomically with their outbox row; running operations retain durable `cancel_requested`.

### Security and deployment
- Executor targeting fails closed: absolute HTTP(S) only, no userinfo, host must match `GovernanceConfig.external_executor_allowed_hosts`.
- Any non-2xx or transport-ambiguous dispatch becomes a durable `uncertain` status with evidence — never automatic replay.
- Async approval revalidates current tool availability, deployment native-tool policy, user capabilities, governance tier, and executor metadata snapshot before consuming a proposal; sync approval behavior is unchanged.
- Callback capabilities are regenerated per dispatch; only SHA-256 hashes are persisted.

### Verification
- Full suite: 2,940 passed, 26 skipped.

## v0.6.7 — 2026-09-15

### Added
- Deployment-native allowlist policy: `tools.native.mode: all | selected | none` with case-sensitive exact/glob `enabled` patterns.
- Native-tool policy enforcement across registry creation, live refresh, ranked persisted search, lazy loading, direct execution, and prompt guidance.

### Security and deployment
- `mode: none` exposes no shipped native tools, including runner meta-tools; custom per-tool `TOOL.md` and MCP definitions remain independent.
- Environment policy precedence is process environment > `.env` > YAML. Invalid native-policy configuration fails closed.

### Verification
- Full suite: 2,897 passed, 26 skipped.

## v0.6.6 — 2026-09-14

### Added
- Processor-isolated observability pipelines: semantic telemetry to Langfuse and endpoint-gated operational telemetry to filtered OTLP/ClickStack.
- Safe operational spans for HTTP requests, sandbox execution, selected SQLite audit operations, and outbound provider traffic—including streaming and OpenAI-compatible providers.
- Export filtering for span attributes, event attributes, links, resources, and status descriptions.
- OpenAI SDK transport seam contract coverage to detect dependency drift.

### Security and privacy
- Operational telemetry is a real no-op without `OTEL_ENDPOINT`.
- Langfuse never receives operational spans; ClickStack never receives Langfuse semantic spans.
- Operational export excludes prompts, tool arguments/results, SQL, command text, request/response content, URL paths/queries/userinfo, and exception descriptions.

### Verification
- Full suite: 2,885 passed, 26 skipped.
- Paired active-lifespan A/B telemetry benchmark: p50 +3.1%, p95 −0.2%, startup −0.020s, RSS +1.16 MiB.

## v0.6.5 — 2026-09-12

### Added
- OpenTelemetry/Langfuse lifecycle foundation with fail-closed Langfuse host validation and an optional filtered OTLP exporter.
