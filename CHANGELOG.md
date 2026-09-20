# Changelog

## Unreleased

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
