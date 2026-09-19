# Changelog

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
